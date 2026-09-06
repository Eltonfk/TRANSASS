"""One-time, non-destructive migration of state between installations."""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def migrate_state(legacy_root: Path, target_root: Path, backup_root: Path | None = None) -> dict[str, object]:
    """Copy missing state files only, after taking a complete source backup.

    Existing target files (especially ``anime-subtitle-library``) are never
    overwritten. The operation is therefore safe to run during every launch.
    """
    legacy_root, target_root = Path(legacy_root), Path(target_root)
    if not legacy_root.is_dir() or legacy_root.resolve() == target_root.resolve():
        return {"migrated": False, "reason": "legacy_missing_or_same_root", "files": []}
    target_root.mkdir(parents=True, exist_ok=True)
    entries = [p for p in legacy_root.rglob("*") if p.is_file()]
    if not entries:
        return {"migrated": False, "reason": "legacy_empty", "files": []}
    backup_base = Path(backup_root or target_root.parent / "migration-backups")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup = backup_base / f"state-{stamp}"
    suffix = 1
    while backup.exists():
        backup = backup_base / f"state-{stamp}-{suffix}"
        suffix += 1
    backup.mkdir(parents=True, exist_ok=False)
    copied: list[str] = []
    manifest: list[dict[str, str]] = []
    for source in entries:
        relative = source.relative_to(legacy_root)
        backup_path = backup / relative
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, backup_path)
        target = target_root / relative
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied.append(relative.as_posix())
        manifest.append({"path": relative.as_posix(), "sha256": _sha256(source)})
    (backup / "manifest.json").write_text(json.dumps({"files": manifest}, indent=2), encoding="utf-8")
    return {"migrated": bool(copied), "reason": "completed", "files": copied, "backup": str(backup)}


def migrate_from_candidates(project_root: Path, target_root: Path) -> dict[str, object]:
    """Use the old repository ``deploy/state`` location when target is empty."""
    target_root = Path(target_root)
    if any(target_root.iterdir()) if target_root.is_dir() else False:
        return {"migrated": False, "reason": "target_not_empty", "files": []}
    legacy = Path(project_root) / "deploy" / "state"
    return migrate_state(legacy, target_root)
