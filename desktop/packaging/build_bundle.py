"""Build a reproducible onedir Desktop bundle with PyInstaller."""

from __future__ import annotations

import argparse
import os
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", type=Path, default=Path("build/desktop"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    dist = (root / args.dist).resolve() if not args.dist.is_absolute() else args.dist.resolve()
    work = root / "build" / "pyinstaller"
    dist.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    sep = os.pathsep
    datas = [
        (root / "src/subtranslate/templates", "templates"),
        (root / "src/subtranslate/static", "static"),
        (root / "src/subtranslate/transass_logo.png", "."),
        (root / "resources/glossaries", "glossaries"),
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
    # The core intentionally uses flat imports and several modules are loaded
    # only after a provider/pipeline is selected. Include the complete module
    # set so a frozen build does not silently lose a runtime branch.
    for module in sorted((root / "src/subtranslate").glob("*.py")):
        if module.name != "__init__.py":
            args_list.extend(["--hidden-import", module.stem])
    args_list.append(str(root / "desktop/packaging/launcher_entry.py"))
    from PyInstaller.__main__ import run

    run(args_list)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
