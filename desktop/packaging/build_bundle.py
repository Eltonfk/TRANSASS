"""Build a reproducible onedir Desktop bundle with PyInstaller."""

from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path

from media_tools import MediaToolsError, resolve_media_tools, write_manifest


_REQUIRED_BUILD_MODULES = {
    "PyInstaller": "PyInstaller",
    "PySide6": "PySide6",
    "QtWebEngine": "PySide6.QtWebEngineWidgets",
    "keyring": "keyring",
    "Flask": "flask",
    "Werkzeug": "werkzeug",
    "requests": "requests",
    "pysubs2": "pysubs2",
}


def missing_build_dependencies() -> tuple[str, ...]:
    """Return required modules missing from the environment running the build."""

    missing = []
    for label, module_name in _REQUIRED_BUILD_MODULES.items():
        try:
            available = importlib.util.find_spec(module_name) is not None
        except (ImportError, ModuleNotFoundError):
            available = False
        if not available:
            missing.append(label)
    return tuple(missing)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", type=Path, default=Path("build/desktop"))
    parser.add_argument(
        "--media-bin-dir",
        type=Path,
        default=None,
        help="directory containing audited ffmpeg/ffprobe executables",
    )
    args = parser.parse_args()
    missing = missing_build_dependencies()
    if missing:
        parser.error(
            "dependências do Desktop ausentes no ambiente de build: "
            + ", ".join(missing)
            + ". Instale requirements.lock e desktop/packaging/requirements-desktop.txt."
        )
    root = Path(__file__).resolve().parents[2]
    dist = (root / args.dist).resolve() if not args.dist.is_absolute() else args.dist.resolve()
    work = root / "build" / "pyinstaller"
    dist.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    sep = os.pathsep
    media_bin_dir = args.media_bin_dir or os.environ.get("TRANSASS_MEDIA_BIN_DIR")
    try:
        media_tools = resolve_media_tools(media_bin_dir)
    except MediaToolsError as error:
        parser.error(str(error))
    datas = [
        (root / "src/subtranslate/templates", "templates"),
        (root / "src/subtranslate/static", "static"),
        (root / "src/subtranslate/transass_logo.png", "."),
        (root / "resources/glossaries", "glossaries"),
        (root / "desktop/packaging/FFMPEG-LICENSE-NOTICE.txt", "licenses"),
    ]
    args_list = [
        "--noconfirm", "--clean", "--onedir", "--name", "Transass",
        "--distpath", str(dist), "--workpath", str(work), "--specpath", str(work),
        "--paths", str(root / "desktop/src"), "--paths", str(root / "src/subtranslate"),
    ]
    # Windows embeds the canonical app mark in Transass.exe.  Linux uses the
    # same PNG through the .desktop/AppImage integration and sets the window
    # icon at runtime (PyInstaller does not embed icons in Linux binaries).
    if os.name == "nt":
        args_list.extend(["--icon", str(root / "src/subtranslate/transass_logo.png")])
    for source, target in datas:
        args_list.extend(["--add-data", f"{source}{sep}{target}"])
    for source in media_tools.values():
        args_list.extend(["--add-binary", f"{source}{sep}bin"])
    # The core intentionally uses flat imports and several modules are loaded
    # only after a provider/pipeline is selected. Include the complete module
    # set so a frozen build does not silently lose a runtime branch.
    for module in sorted((root / "src/subtranslate").glob("*.py")):
        if module.name != "__init__.py":
            args_list.extend(["--hidden-import", module.stem])
    for module in ("PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineCore"):
        args_list.extend(["--hidden-import", module])
    args_list.append(str(root / "desktop/packaging/launcher_entry.py"))
    from PyInstaller.__main__ import run

    run(args_list)
    bundle_root = dist / "Transass"
    resource_root = next(
        (
            candidate
            for candidate in (bundle_root / "_internal", bundle_root)
            if (candidate / "bin").is_dir()
        ),
        None,
    )
    if resource_root is None:
        raise RuntimeError("PyInstaller bundle has no resource bin directory")
    write_manifest(
        resource_root / "bin",
        media_tools,
        source="explicit-directory" if media_bin_dir else "PATH",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
