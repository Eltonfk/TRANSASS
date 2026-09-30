"""Reject AppDir targets before executing a bundle or modifying any evidence."""

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).parents[2]
pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux shell packager")


@pytest.fixture
def builder(tmp_path):
    checkout = tmp_path / "checkout"
    script = checkout / "desktop/packaging/linux/build_appimage.sh"
    script.parent.mkdir(parents=True)
    shutil.copy2(ROOT / "desktop/packaging/linux/build_appimage.sh", script)
    return checkout, script


def run_builder(script, target):
    environment = os.environ.copy()
    environment.pop("APPIMAGETOOL", None)
    return subprocess.run(
        ["bash", str(script), "/nonexistent-bundle", str(target)],
        env=environment, capture_output=True, text=True, timeout=10,
    )


@pytest.mark.parametrize("target_name", ["root", "home", "repo", "build", "custom"])
def test_broad_and_arbitrary_targets_fail_before_bundle_checks(builder, tmp_path, target_name):
    checkout, script = builder
    custom = tmp_path / "user-data"
    custom.mkdir()
    sentinel = custom / "history.json"
    sentinel.write_bytes(b"historical evidence")
    target = {"root": Path("/"), "home": Path.home(), "repo": checkout,
              "build": checkout / "build", "custom": custom}[target_name]

    result = run_builder(script, target)

    assert result.returncode != 0
    assert "AppDir recusado" in result.stderr
    assert "APPIMAGETOOL" not in result.stderr
    assert sentinel.read_bytes() == b"historical evidence"
    assert not (checkout / "build").exists()


def test_existing_appdir_is_never_removed_even_with_ownership_marker(builder):
    checkout, script = builder
    appdir = checkout / "build/Transass.AppDir"
    appdir.mkdir(parents=True)
    sentinel = appdir / "old-build-evidence.txt"
    sentinel.write_bytes(b"old build")
    (appdir / ".transass-owned").write_bytes(b"owned")

    result = run_builder(script, appdir)

    assert result.returncode != 0
    assert "AppDir recusado" in result.stderr
    assert sentinel.read_bytes() == b"old build"


@pytest.mark.parametrize("link_location", ["build", "appdir"])
def test_symlink_target_or_build_root_is_refused(builder, tmp_path, link_location):
    checkout, script = builder
    data = tmp_path / "user-data"
    data.mkdir()
    sentinel = data / "history.json"
    sentinel.write_bytes(b"historical evidence")
    if link_location == "build":
        (checkout / "build").symlink_to(data, target_is_directory=True)
    else:
        (checkout / "build").mkdir()
        (checkout / "build/Transass.AppDir").symlink_to(data, target_is_directory=True)

    result = run_builder(script, checkout / "build/Transass.AppDir")

    assert result.returncode != 0
    assert "AppDir recusado" in result.stderr
    assert sentinel.read_bytes() == b"historical evidence"


def test_fresh_canonical_appdir_reaches_tool_validation_without_creating_it(builder):
    checkout, script = builder

    result = run_builder(script, checkout / "build/Transass.AppDir")

    assert result.returncode != 0
    assert "Defina APPIMAGETOOL" in result.stderr
    assert "AppDir recusado" not in result.stderr
    assert not (checkout / "build").exists()
