import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / "desktop/packaging"))

from media_tools import build_manifest, resolve_media_tools  # noqa: E402


def test_checksum_manifest_is_reproducible(tmp_path):
    artifact = tmp_path / "bundle" / "Transass"
    artifact.mkdir(parents=True)
    payload = artifact / "hello.txt"
    payload.write_text("transass", encoding="utf-8")
    subprocess.run([sys.executable, str(ROOT / "desktop/packaging/checksums.py"), str(tmp_path / "bundle")], check=True)
    line = (tmp_path / "bundle" / "SHA256SUMS").read_text(encoding="utf-8").strip()
    expected = hashlib.sha256(payload.read_bytes()).hexdigest()
    assert line == f"{expected}  Transass/hello.txt"


def test_media_tools_manifest_records_both_executables(tmp_path):
    media_bin = tmp_path / "media-bin"
    media_bin.mkdir()
    for name in ("ffmpeg", "ffprobe"):
        executable = media_bin / name
        executable.write_bytes(name.encode("ascii"))
        executable.chmod(0o755)

    tools = resolve_media_tools(media_bin)
    manifest = build_manifest(tools, source="test")

    assert set(manifest["tools"]) == {"ffmpeg", "ffprobe"}
    assert all(entry["sha256"] for entry in manifest["tools"].values())
    assert manifest["license_notice"] == "licenses/FFMPEG-LICENSE-NOTICE.txt"


def test_sbom_has_cyclonedx_shape(tmp_path):
    subprocess.run([sys.executable, str(ROOT / "desktop/packaging/generate_sbom.py"), str(tmp_path / "bundle")], check=True)
    report = json.loads((tmp_path / "bundle" / "sbom.cdx.json").read_text(encoding="utf-8"))
    assert report["bomFormat"] == "CycloneDX"
    assert report["specVersion"] == "1.5"
    assert isinstance(report["components"], list)


def test_distribution_manifests_preserve_user_data():
    installer = (ROOT / "desktop/packaging/windows/installer.iss").read_text(encoding="utf-8")
    linux_entry = (
        ROOT / "desktop/packaging/linux/io.github.Eltonfk.Transass.desktop"
    ).read_text(encoding="utf-8")
    assert "PrivilegesRequired=lowest" in installer
    assert "{localappdata}\\Transass" in installer
    assert "OutputDir=..\\..\\..\\Output" in installer
    assert "Exec=Transass" in linux_entry
    assert "Icon=transass" in linux_entry
    assert "Categories=AudioVideo;" in linux_entry
    appimage_builder = (ROOT / "desktop/packaging/linux/build_appimage.sh").read_text(encoding="utf-8")
    assert 'usr/share/icons/hicolor/256x256/apps/transass.png' in appimage_builder
    assert 'cp "$root/src/subtranslate/transass_logo.png"' in appimage_builder
    assert 'io.github.Eltonfk.Transass.appdata.xml' in appimage_builder
    assert 'Bundle desatualizado' in appimage_builder


def test_desktop_launcher_exposes_menu_visibility_and_help_links():
    launcher = (ROOT / "desktop/src/transass_desktop/launcher.py").read_text(encoding="utf-8")
    assert 'addMenu("Arquivo")' in launcher
    assert 'addMenu("Exibir")' in launcher
    assert 'addAction("Mostrar barra de menus")' in launcher
    assert 'QKeySequence("Ctrl+Shift+M")' in launcher
    assert 'addMenu("Ajuda")' in launcher
    assert 'addAction("Tutorial: chaves de API")' in launcher
    assert 'https://aistudio.google.com/app/apikey' in launcher
    assert 'https://ai.google.dev/gemini-api/docs/api-key' in launcher
    assert 'https://build.nvidia.com/explore/discover?api-key=true' in launcher
    assert 'https://docs.nvidia.com/nim/large-language-models/latest/get-started/' in launcher
    assert 'show_api_key_tutorial' in launcher
    assert 'add_provider_card(' in launcher
    assert 'Ollama local' in launcher
    assert 'ollama pull qwen3.5:9b' in launcher
    assert 'https://ollama.com/download' in launcher
    assert 'https://docs.ollama.com/quickstart' in launcher
    assert 'https://ollama.com/library/qwen3.5' in launcher
    assert 'https://github.com/Eltonfk/TRANSASS' in launcher
    assert "QWebEngineView" in launcher
    assert "web_view.loadFinished.connect(handle_page_loaded)" in launcher
    assert "web_view.setUrl(QUrl(url))" in launcher
    assert 'webbrowser.open(url, new=2)' not in launcher
    assert 'interface/menu_bar_visible' in launcher
    assert 'QMenuBar {' in launcher
    assert 'background: #0e1722' in launcher
    assert 'QMenu::item:selected' in launcher
    assert 'QMessageBox {' in launcher
    assert 'QMessageBox QPushButton' in launcher
    assert 'webbrowser.open' in launcher
    assert 'lambda checked=False: open_url' in launcher
    assert 'QIcon' in launcher
    assert 'transass_logo.png' in launcher

    app_css = (ROOT / "src/subtranslate/static/app.css").read_text(encoding="utf-8")
    assert "scrollbar-width:thin" in app_css
    assert "::-webkit-scrollbar-thumb" in app_css

    page = (ROOT / "src/subtranslate/templates/index.html").read_text(encoding="utf-8")
    app_js = (ROOT / "src/subtranslate/static/app.js").read_text(encoding="utf-8")
    assert 'id="tcTest"' in page
    assert 'id="tcPrimaryModelSelect"' in page
    assert 'id="tcGeminiModelsRefresh"' in page
    assert "testTransportConfig" in app_js
    assert "/onboarding/provider-test" in app_js
    assert "/onboarding/provider-models?provider=" in app_js
    assert "provider==='gemini'" in app_js
    assert "gemini-3.5-flash-lite" in app_js


def test_bundle_uses_canonical_icon_on_windows():
    builder = (ROOT / "desktop/packaging/build_bundle.py").read_text(encoding="utf-8")
    assert 'if os.name == "nt"' in builder
    assert 'transass_logo.png' in builder
    assert '"--icon"' in builder


def test_bundle_checks_runtime_dependencies_before_pyinstaller():
    builder = (ROOT / "desktop/packaging/build_bundle.py").read_text(encoding="utf-8")
    assert "missing_build_dependencies" in builder
    assert '"Werkzeug": "werkzeug"' in builder
    assert '"QtWebEngine": "PySide6.QtWebEngineWidgets"' in builder
    assert '"PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineCore"' in builder
    assert 'requirements.lock e desktop/packaging/requirements-desktop.txt' in builder


def test_frozen_launcher_supports_version_probe_without_qt():
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        (str(ROOT / "desktop/src"), str(ROOT / "src/subtranslate"))
    )
    result = subprocess.run(
        [sys.executable, str(ROOT / "desktop/packaging/launcher_entry.py"), "--version"],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert result.stdout.strip() == "Transass 2.5.2"
