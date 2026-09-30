from pathlib import Path
from email.parser import BytesParser
import os
import shutil
import site
import subprocess
import sys
import tomllib
import venv
import zipfile

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_setuptools_declares_every_flat_runtime_module() -> None:
    configuration = tomllib.loads(
        (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    declared_modules = set(configuration["tool"]["setuptools"]["py-modules"])
    runtime_modules = {
        path.stem
        for path in (PROJECT_ROOT / "src" / "subtranslate").glob("*.py")
        if path.name != "__init__.py"
    }

    assert declared_modules == runtime_modules


@pytest.fixture(scope="module")
def built_wheel(tmp_path_factory):
    root = tmp_path_factory.mktemp("packaging-wheel")
    checkout = root / "checkout"
    checkout.mkdir()
    for name in ("pyproject.toml", "README.md", "LICENSE"):
        shutil.copy2(PROJECT_ROOT / name, checkout / name)
    for name in ("src/subtranslate", "resources/glossaries"):
        shutil.copytree(
            PROJECT_ROOT / name, checkout / name,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info"),
        )
    wheels = root / "wheels"
    wheels.mkdir()
    # Build in a disposable copy, with the local backend and no network/build
    # isolation download. Never create egg-info or build output in the repo.
    result = subprocess.run(
        [sys.executable, "-I", "-c",
         "import setuptools.build_meta as b; import sys; b.build_wheel(sys.argv[1])",
         str(wheels)],
        cwd=checkout, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    artifacts = list(wheels.glob("*.whl"))
    assert len(artifacts) == 1
    return root, artifacts[0]


def test_built_metadata_declares_runtime_dependencies_from_lock(built_wheel):
    _, wheel = built_wheel
    with zipfile.ZipFile(wheel) as archive:
        metadata_path = next(p for p in archive.namelist() if p.endswith(".dist-info/METADATA"))
        metadata = BytesParser().parsebytes(archive.read(metadata_path))
    requirements = {
        canonicalize_name(r.name): r
        for r in map(Requirement, metadata.get_all("Requires-Dist", []))
    }
    expected = {"flask", "werkzeug", "requests", "pysubs2", "jsonschema", "psutil", "gunicorn"}
    assert set(requirements) == expected
    locked = {
        canonicalize_name(r.name): r
        for line in (PROJECT_ROOT / "requirements.lock").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
        for r in [Requirement(line)]
    }
    for name, requirement in requirements.items():
        assert requirement.specifier == locked[name].specifier
        assert requirement.marker is None or name == "gunicorn"
    assert requirements["gunicorn"].marker is not None
    assert not requirements["gunicorn"].marker.evaluate({"sys_platform": "win32"})
    assert requirements["gunicorn"].marker.evaluate({"sys_platform": "linux"})


def test_built_wheel_contains_every_runtime_asset_and_flat_module(built_wheel):
    _, wheel = built_wheel
    with zipfile.ZipFile(wheel) as archive:
        contents = set(archive.namelist())
        for module in (PROJECT_ROOT / "src/subtranslate").glob("*.py"):
            if module.name != "__init__.py":
                assert module.name in contents
        for directory in ("templates", "static"):
            asset_root = PROJECT_ROOT / "src/subtranslate" / directory
            assets = asset_root.rglob("*")
            for asset in assets:
                if asset.is_file():
                    suffix = f".data/data/share/transass/{directory}/{asset.relative_to(asset_root).as_posix()}"
                    # Match the actual wheel data scheme, including nested assets.
                    member = next(p for p in contents if p.endswith(suffix))
                    assert archive.read(member) == asset.read_bytes()
        for name in ("glossary.json", "v2_1_2_glossary.json"):
            assert any(p.endswith(f".data/data/share/transass/glossaries/{name}") for p in contents)
        assert any(p.endswith(".data/data/share/transass/transass_logo.png") for p in contents)


def test_installed_wheel_imports_and_serves_assets_without_checkout(built_wheel):
    root, wheel = built_wheel
    environment_root = root / "installed"
    # Runtime dependencies are inherited from the test environment. Installing
    # the wheel itself is offline: this proves its layout/imports/resources,
    # not dependency resolution against an external package index.
    venv.EnvBuilder(system_site_packages=True, with_pip=True).create(environment_root)
    python = environment_root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    install = subprocess.run(
        [str(python), "-I", "-m", "pip", "install", "--no-index", "--no-deps", str(wheel)],
        cwd=root, capture_output=True, text=True, timeout=60,
    )
    assert install.returncode == 0, install.stdout + install.stderr
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("TRANSASS_", "TRANSLATOR_", "ANIME_"))
        and key not in {"STATE_DIR", "MEDIA_ROOT", "PYTHONPATH"}
    }
    data = root / "synthetic-user-data"
    environment.update({
        "TRANSASS_DATA_DIR": str(data),
        "TRANSLATOR_WEB_STATE_DIR": str(data / "state"),
        "TRANSLATOR_BASE_LIBRARY": str(data / "media"),
        "ANIME_SUBTITLE_LIBRARY_ROOT": str(data / "state" / "library"),
        "TRANSLATOR_FAILURE_LEDGER_ROOT": str(data / "state" / "ledger"),
        "TRANSASS_CREDENTIAL_BACKEND": "file",
    })
    probe = r'''
import sys
from pathlib import Path
# User-installed dependencies also belong to the local test environment.
# Append their location after the venv; do not load its .pth files or checkout.
sys.path.append(sys.argv[1])
import app
import pipeline_v3
import state_access
import web_fallback_safety
from runtime_paths import resource_root

prefix = Path(sys.prefix).resolve()
for module in (app, pipeline_v3, state_access, web_fallback_safety):
    assert Path(module.__file__).resolve().is_relative_to(prefix), module.__file__
assets = resource_root()
assert assets == prefix / "share" / "transass", assets
client = app.app.test_client()
assert client.get("/health").status_code == 200
page = client.get("/")
assert page.status_code == 200
assert b"i18n.js" in page.data
for asset in (assets / "static").iterdir():
    response = client.get("/static/" + asset.name)
    assert response.status_code == 200, asset.name
    assert response.data == asset.read_bytes(), asset.name
    response.close()
for template in ("index.html", "review.html", "glossary.html"):
    assert (assets / "templates" / template).is_file()
assert (assets / "transass_logo.png").is_file()
assert (assets / "glossaries" / "glossary.json").is_file()
print("INSTALLED_WHEEL_OK")
'''
    result = subprocess.run(
        [str(python), "-I", "-c", probe, site.getusersitepackages()], env=environment, cwd=root,
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "INSTALLED_WHEEL_OK"
