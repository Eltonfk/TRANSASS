from pathlib import Path
import tomllib


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
