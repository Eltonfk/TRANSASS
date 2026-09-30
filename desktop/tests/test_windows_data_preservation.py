"""Offline manifest/layout proof. Native Inno uninstall remains a Windows check."""

from pathlib import Path
import re
import shutil
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from transass_desktop import paths as paths_module

ROOT = Path(__file__).parents[2]
INSTALLER = ROOT / "desktop/packaging/windows/installer.iss"


def installer_sections():
    sections = {}
    current = "preprocessor"
    for raw in INSTALLER.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
        else:
            sections.setdefault(current, []).append(line)
    return sections


def test_windows_default_install_is_separate_and_appid_is_continuous(tmp_path, monkeypatch):
    local = tmp_path / "AppData" / "Local"
    roaming = tmp_path / "AppData" / "Roaming"
    for name in ("TRANSASS_DATA_DIR", "TRANSASS_CONFIG_DIR", "STATE_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(paths_module.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("APPDATA", str(roaming))
    paths = paths_module.default_paths()
    setup = dict(line.split("=", 1) for line in installer_sections()["Setup"])
    directory = setup["DefaultDirName"].replace("{localappdata}", str(local)).replace("\\", "/")
    app_dir = Path(directory)

    assert app_dir == local / "Programs" / "Transass"
    assert paths.data_root == local / "Transass"
    assert not app_dir.is_relative_to(paths.data_root)
    assert not paths.data_root.is_relative_to(app_dir)
    assert setup["AppId"] == "{{B13A4F17-4A7C-4A85-9A3C-250000000000}}"
    assert setup["UsePreviousAppDir"] == "no"


@pytest.mark.parametrize("legacy_location", ["default", "custom"])
def test_only_manifest_owned_files_are_removed_from_synthetic_layout(tmp_path, legacy_location):
    sections = installer_sections()
    # Reject every mechanism that can schedule deletion/execute an old
    # uninstaller, rather than making a sentinel simulation silently ignore it.
    assert not ({"UninstallDelete", "InstallDelete", "Run", "UninstallRun"} & sections.keys())
    code = "\n".join(sections["Code"])
    assert not re.search(r"\b(DelTree|DeleteFile|RemoveDir|Exec|ShellExec)\s*\(", code, re.I)
    assert "function PrepareToInstall" in code
    assert "HasOldUninstaller(Directory)" in code
    assert "transass-user-data-preserving-installer-v1" in code
    assert "unins*.dat" in code and "unins*.exe" in code
    local = tmp_path / "AppData" / "Local"
    data = local / "Transass"
    legacy = data if legacy_location == "default" else tmp_path / "custom legacy app"
    app = local / "Programs" / "Transass"
    sentinels = {
        data / "state/jobs.json": b"history",
        data / "state/anime-subtitle-library/episode.pt-BR.ass": b"library",
        data / "media/episode.mkv": b"media",
        legacy / "unins000.dat": b"legacy uninstall evidence",
        legacy / "Transass.exe": b"legacy application",
        app / "state/history.json": b"user data in custom application folder",
        app / "media/episode.mkv": b"media in application folder",
    }
    for path, content in sentinels.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    bundle = tmp_path / "build/desktop/Transass"
    (bundle / "_internal").mkdir(parents=True)
    (bundle / "Transass.exe").write_bytes(b"new application")
    (bundle / "_internal/runtime.dll").write_bytes(b"runtime")

    owned = []
    for entry in sections["Files"]:
        parameters = dict(
            (key.lower(), value.strip('"'))
            for key, value in re.findall(r'(\w+):\s*("[^"]*"|[^;]+)', entry)
        )
        assert parameters["destdir"] == "{app}"
        assert "unins" not in parameters["source"]
        if parameters["source"].endswith("build\\desktop\\Transass\\*"):
            sources = [(p, p.relative_to(bundle)) for p in bundle.rglob("*") if p.is_file()]
        else:
            source = INSTALLER.parent / parameters["source"]
            assert source.name == "data-safe-installer.txt"
            sources = [(source, Path(source.name))]
        for source, relative in sources:
            target = app / relative
            assert target not in sentinels
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            owned.append(target)
    assert app / "Transass.exe" in owned
    # Model the documented removal of registered files, not a native Inno run.
    for target in owned:
        assert target.is_file()
        target.unlink()
    for path, content in sentinels.items():
        assert path.read_bytes() == content
