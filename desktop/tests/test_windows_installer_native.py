"""Opt-in native Inno proof, rebased to temporary paths and a unique AppId.

Never installs/uninstalls a real user installation. The fixture with the old
recursive-delete rule is installed to reproduce its log, but never uninstalled.
"""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

import pytest


ROOT = Path(__file__).parents[2]
pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or os.environ.get("TRANSASS_TEST_WINDOWS_INSTALLER") != "1",
    reason="native Inno proof requires Windows and TRANSASS_TEST_WINDOWS_INSTALLER=1",
)


def run_installer(executable, *arguments):
    return subprocess.run(
        [str(executable), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", *arguments],
        capture_output=True, text=True, timeout=90,
    )


@pytest.mark.parametrize("legacy_location", ["default", "custom"])
def test_native_upgrade_and_uninstall_preserve_all_sentinels(tmp_path, monkeypatch, legacy_location):
    compiler = shutil.which("iscc")
    assert compiler, "Install Inno Setup to run the opted-in native installer proof"
    for name in (
        "TRANSASS_DATA_DIR", "TRANSASS_CONFIG_DIR", "STATE_DIR", "MEDIA_ROOT",
        "TRANSLATOR_WEB_STATE_DIR", "TRANSLATOR_BASE_LIBRARY",
        "ANIME_SUBTITLE_LIBRARY_ROOT", "TRANSLATOR_FAILURE_LEDGER_ROOT",
    ):
        monkeypatch.delenv(name, raising=False)

    checkout = tmp_path / "checkout"
    windows = checkout / "desktop/packaging/windows"
    windows.mkdir(parents=True)
    bundle = checkout / "build/desktop/Transass"
    (bundle / "_internal").mkdir(parents=True)
    (bundle / "Transass.exe").write_bytes(b"synthetic application; never executed")
    (bundle / "_internal/runtime.dll").write_bytes(b"synthetic runtime")
    icon = subprocess.run(
        [sys.executable, str(ROOT / "desktop/packaging/make_icon.py"), str(checkout / "build/transass.ico")],
        capture_output=True, text=True, timeout=30,
    )
    assert icon.returncode == 0, icon.stdout + icon.stderr
    shutil.copy2(ROOT / "desktop/packaging/windows/data-safe-installer.txt", windows)

    local = tmp_path / "synthetic-profile/Local"
    roaming = tmp_path / "synthetic-profile/Roaming"
    data = local / "Transass"
    app_dir = local / "Programs/Transass"
    legacy = data if legacy_location == "default" else tmp_path / "custom legacy app"
    # Preserve the production structure/code. Rebase only OS paths and AppId,
    # so this test cannot collide with a real installation or registration.
    app_id = "{{" + str(uuid.uuid4()).upper() + "}}"
    manifest = (ROOT / "desktop/packaging/windows/installer.iss").read_text(encoding="utf-8")
    manifest = manifest.replace("{{B13A4F17-4A7C-4A85-9A3C-250000000000}}", app_id)
    manifest = manifest.replace("{localappdata}", str(local)).replace("{userappdata}", str(roaming))
    # Shortcuts must also remain inside the temporary fixture.
    manifest = manifest.replace("{userprograms}", str(tmp_path / "shortcuts"))
    manifest = manifest.replace("{userdesktop}", str(tmp_path / "desktop-shortcuts"))
    script = windows / "installer.iss"
    script.write_text(manifest, encoding="utf-8")
    compile_new = subprocess.run([compiler, "/Q", str(script)], capture_output=True, text=True, timeout=60)
    assert compile_new.returncode == 0, compile_new.stdout + compile_new.stderr
    installers = list((checkout / "Output").glob("Transass-Setup-*.exe"))
    assert len(installers) == 1
    installer = installers[0]

    old_script = tmp_path / "legacy.iss"
    old_script.write_text(
        "[Setup]\nAppName=Transass offline legacy fixture\n"
        f"AppId={app_id}\nAppVersion=2.5.2\nDefaultDirName={legacy}\n"
        f"OutputDir={tmp_path / 'legacy-output'}\nOutputBaseFilename=legacy\n"
        "PrivilegesRequired=lowest\nDisableProgramGroupPage=yes\n"
        "[Files]\n"
        f'Source: "{bundle / "Transass.exe"}"; DestDir: "{{app}}"\n'
        '[UninstallDelete]\nType: filesandordirs; Name: "{app}"\n',
        encoding="utf-8",
    )
    compile_old = subprocess.run([compiler, "/Q", str(old_script)], capture_output=True, text=True, timeout=60)
    assert compile_old.returncode == 0, compile_old.stdout + compile_old.stderr
    assert run_installer(tmp_path / "legacy-output/legacy.exe").returncode == 0

    sentinels = {
        data / "state/jobs.json": b"history",
        data / "state/anime-subtitle-library/episode.pt-BR.ass": b"library",
        data / "media/episode.mkv": b"media",
    }
    for path, content in sentinels.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    old_files = {p: p.read_bytes() for p in legacy.glob("unins*.*") if p.is_file()}
    assert old_files

    rejected = run_installer(installer, f"/DIR={legacy}")
    assert rejected.returncode != 0, "legacy directory must fail before installation"
    assert not (legacy / "data-safe-installer.txt").exists()
    for path, content in old_files.items():
        assert path.read_bytes() == content

    # No /DIR: verify that the old AppId registration cannot restore the old
    # directory. Repeat the safe install to exercise uninstall log append.
    for _ in range(2):
        installed = run_installer(installer)
        assert installed.returncode == 0, installed.stdout + installed.stderr
        assert (app_dir / "Transass.exe").is_file()
    sentinels[app_dir / "state/user-history.json"] = b"user data inside application folder"
    sentinels[app_dir / "media/episode.mkv"] = b"user media inside application folder"
    for path, content in sentinels.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    uninstallers = list(app_dir.glob("unins*.exe"))
    assert len(uninstallers) == 1
    removed = run_installer(uninstallers[0])
    assert removed.returncode == 0, removed.stdout + removed.stderr
    assert not (app_dir / "Transass.exe").exists()
    for path, content in {**sentinels, **old_files}.items():
        assert path.read_bytes() == content
