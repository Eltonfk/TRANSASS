from pathlib import Path
import shutil

from scripts.check_release import ROOT, __version__, check_release


def test_active_release_manifests_match_canonical_version() -> None:
    assert check_release(ref=f"v{__version__}") == []


def test_tag_drift_and_image_drift_are_blocked(tmp_path: Path) -> None:
    paths = ["pyproject.toml", "desktop/src/transass_desktop/__init__.py", "desktop/src/transass_desktop/launcher.py",
             "desktop/packaging/windows/installer.iss", ".env.example", "deploy/compose.yaml",
             "desktop/packaging/linux/io.github.Eltonfk.Transass.appdata.xml"]
    for path in paths:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / path, target)
    (tmp_path / ".env.example").write_text("TRANSASS_IMAGE=transass:v0.0.0\n", encoding="utf-8")
    errors = check_release(tmp_path, ref="v0.0.0")
    assert any(".env.example" in error for error in errors)
    assert any("tag" in error for error in errors)
