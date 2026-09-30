#!/usr/bin/env python3
"""Fail closed when active manifests disagree with the canonical release."""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys
import tomllib
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src/subtranslate"))
from _version import __version__


def check_release(root: Path = ROOT, ref: str = "") -> list[str]:
    errors = []
    canonical = __version__
    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    versions = {"pyproject.toml": metadata["project"]["version"]}
    for path, pattern in (
        ("desktop/src/transass_desktop/__init__.py", r'__version__\s*=\s*"([^"]+)"'),
        ("desktop/src/transass_desktop/launcher.py", r'__version__\s*=\s*"([^"]+)"'),
        ("desktop/packaging/windows/installer.iss", r'#define AppVersion "([^"]+)"'),
        (".env.example", r'TRANSASS_IMAGE=transass:v([^\s]+)'),
        ("deploy/compose.yaml", r'TRANSASS_IMAGE:-transass:v([^}]+)'),
    ):
        match = re.search(pattern, (root / path).read_text(encoding="utf-8"))
        versions[path] = match.group(1) if match else "MISSING"
    appdata = ET.parse(root / "desktop/packaging/linux/io.github.Eltonfk.Transass.appdata.xml")
    release = appdata.find("releases/release")
    versions["AppStream latest release"] = release.get("version") if release is not None else "MISSING"
    for path, version in versions.items():
        if version != canonical:
            errors.append(f"{path}: {version} != {canonical}")
    if ref.startswith("v") and ref != f"v{canonical}":
        errors.append(f"tag {ref} != v{canonical}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default="")
    arguments = parser.parse_args()
    errors = check_release(ref=arguments.ref)
    if errors:
        print("RELEASE_IDENTITY_FAILED\n" + "\n".join(errors), file=sys.stderr)
        return 1
    print(f"RELEASE_IDENTITY_OK {__version__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
