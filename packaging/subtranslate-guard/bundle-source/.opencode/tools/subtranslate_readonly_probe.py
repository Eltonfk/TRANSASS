#!/usr/bin/env python3
"""Protected release probe entrypoint.

This is source for the future installed bundle, not a live OpenCode tool.
It has no operational arguments and derives every import/path from its own
protected release.  The manifest is fixed by installation policy.
"""
from __future__ import annotations

import json
import stat
import sys
from pathlib import Path


def _release_root() -> Path:
    entry = Path(__file__)
    path = entry.resolve(strict=True)
    root = path.parents[2]
    info = root.lstat()
    if entry.is_symlink() or root.is_symlink() or not root.is_dir() or info.st_uid != 0 or (info.st_mode & 0o022):
        raise RuntimeError("PROTECTED_RELEASE_ROOT_UNSAFE")
    return root


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    if args:
        print(json.dumps({"schema_version": "0.3.0", "probe_version": "0.3.0",
                          "error": {"code": "UNEXPECTED_ARGUMENT", "arguments_rejected": True},
                          "integrity": {"snapshot_consistent": False, "side_effects_performed": False}},
                         sort_keys=True, separators=(",", ":")))
        return 4
    try:
        release = _release_root()
        src_entry = release / "src"
        src = src_entry.resolve(strict=True)
        src.relative_to(release)
        if src_entry.is_symlink() or not src.is_dir():
            raise RuntimeError("PROTECTED_SOURCE_ROOT_UNSAFE")
        sys.path.insert(0, str(src))
        from subtranslate.recovery_guard.production.manifest import validate_final_manifest
        from subtranslate.recovery_guard.production.probe_engine import production_profile, run_probe

        manifest_path = Path("/etc/subtranslate-guard/manifest.json")
        manifest_info = manifest_path.lstat()
        if manifest_path.is_symlink() or not manifest_path.is_file() or manifest_info.st_uid != 0 or (manifest_info.st_mode & 0o022):
            raise RuntimeError("PROTECTED_MANIFEST_UNAVAILABLE")
        manifest = json.loads(manifest_path.read_bytes())
        validate_final_manifest(manifest, release)
        result = run_probe(production_profile(release_root=release, manifest=manifest))
        print(json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
        return 3 if result["unknowns"] else (2 if result["blockers"] else 0)
    except Exception as exc:
        print(json.dumps({"schema_version": "0.3.0", "probe_version": "0.3.0",
                          "error": {"code": "INTERNAL_ERROR", "type": type(exc).__name__},
                          "integrity": {"snapshot_consistent": False, "side_effects_performed": False}},
                         sort_keys=True, separators=(",", ":")))
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
