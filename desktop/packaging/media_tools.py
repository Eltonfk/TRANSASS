"""Validate and fingerprint the media tools bundled with Desktop builds."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import sys
from pathlib import Path
from typing import Mapping

MEDIA_TOOL_NAMES = ("ffmpeg", "ffprobe")
MEDIA_TOOLS_MANIFEST = "MEDIA_TOOLS.json"


class MediaToolsError(RuntimeError):
    """Raised when a Desktop build cannot provide its media dependencies."""


def _validate_candidate(path: Path, name: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise MediaToolsError(f"{name} is not a regular file: {path}")
    if os.name != "nt" and not path.stat().st_mode & stat.S_IXUSR:
        raise MediaToolsError(f"{name} is not executable: {path}")
    return path.resolve()


def resolve_media_tools(source_dir: Path | str | None = None) -> dict[str, Path]:
    """Resolve both tools from an explicit directory or the developer PATH.

    An explicit directory is authoritative: a missing tool fails the build
    instead of silently falling back to a different host installation.
    """
    resolved: dict[str, Path] = {}
    if source_dir is not None:
        root = Path(source_dir).expanduser().resolve()
        if not root.is_dir():
            raise MediaToolsError(f"media tools directory not found: {root}")
        for name in MEDIA_TOOL_NAMES:
            candidate = next(
                (
                    root / filename
                    for filename in (name, f"{name}.exe")
                    if (root / filename).exists()
                ),
                None,
            )
            if candidate is None:
                raise MediaToolsError(f"missing {name} in media tools directory: {root}")
            resolved[name] = _validate_candidate(candidate, name)
        return resolved

    for name in MEDIA_TOOL_NAMES:
        candidate = shutil.which(name)
        if not candidate:
            raise MediaToolsError(
                f"{name} not found on PATH; provide --media-bin-dir or TRANSASS_MEDIA_BIN_DIR"
            )
        resolved[name] = _validate_candidate(Path(candidate), name)
    return resolved


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(tools: Mapping[str, Path], *, source: str) -> dict:
    """Build a non-secret integrity manifest for the bundled executables."""
    if set(tools) != set(MEDIA_TOOL_NAMES):
        raise MediaToolsError("the Desktop bundle must contain exactly ffmpeg and ffprobe")
    return {
        "schema_version": 1,
        "platform": sys.platform,
        "source": source,
        "tools": {
            name: {
                "filename": str(path.name),
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for name, path in tools.items()
        },
        "license_notice": "licenses/FFMPEG-LICENSE-NOTICE.txt",
    }


def write_manifest(bundle_bin_dir: Path, tools: Mapping[str, Path], *, source: str) -> Path:
    """Write the manifest inside the frozen bundle and return its path."""
    bundle_bin_dir.mkdir(parents=True, exist_ok=True)
    target = bundle_bin_dir / MEDIA_TOOLS_MANIFEST
    target.write_text(
        json.dumps(build_manifest(tools, source=source), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target
