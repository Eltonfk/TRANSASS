#!/usr/bin/env python3
"""Create the exact documentary backups for AUTO-03C.

This helper deliberately has no user-controlled paths and no cleanup or
fallback behavior.  It is invoked only through the exact OpenCode allowlist.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path


PROJECT_STATE = Path(
    "/home/palhacinho/codex-projects/anime-subtitle-translator-review/PROJECT_STATE.json"
)
HANDOFF = Path(
    "/home/palhacinho/codex-projects/anime-subtitle-translator-review/HANDOFF_CHATGPT.md"
)
BACKUP_ROOT = Path(
    "/home/palhacinho/opencode-backups/subtranslate-auto03c-documentary-write-20260822"
)


class BackupFailure(Exception):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_regular(path: Path) -> None:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise BackupFailure(f"non-regular source: {path}")


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def backup_one(source: Path) -> dict[str, object]:
    require_regular(source)
    source_hash = sha256(source)
    destination = BACKUP_ROOT / source.name

    if destination.exists():
        require_regular(destination)
        destination_hash = sha256(destination)
        if destination_hash != source_hash:
            raise BackupFailure(f"existing backup hash mismatch: {destination}")
        return {
            "source": str(source),
            "backup": str(destination),
            "sha256": source_hash,
            "status": "REUSED_VALID",
        }

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{source.name}.backup-", dir=str(BACKUP_ROOT)
    )
    temporary = Path(temporary_name)
    try:
        with source.open("rb") as source_stream:
            while True:
                chunk = source_stream.read(1024 * 1024)
                if not chunk:
                    break
                os.write(descriptor, chunk)
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, destination)
        fsync_directory(BACKUP_ROOT)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)

    destination_hash = sha256(destination)
    if destination_hash != source_hash:
        raise BackupFailure(f"post-backup hash mismatch: {destination}")
    return {
        "source": str(source),
        "backup": str(destination),
        "sha256": source_hash,
        "status": "CREATED",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    if not args.run:
        raise SystemExit("FAIL_STOP: --run required")

    report: dict[str, object] = {
        "backup_root": str(BACKUP_ROOT),
        "attempt_budget_per_file": 1,
        "files": [],
        "status": "FAIL",
    }
    try:
        if not BACKUP_ROOT.parent.is_dir():
            raise BackupFailure(f"backup parent missing: {BACKUP_ROOT.parent}")
        if BACKUP_ROOT.exists() and not BACKUP_ROOT.is_dir():
            raise BackupFailure(f"backup root is not a directory: {BACKUP_ROOT}")
        BACKUP_ROOT.mkdir(mode=0o700, exist_ok=True)
        # The order is intentional and must remain sequential.
        report["files"].append(backup_one(HANDOFF))
        report["files"].append(backup_one(PROJECT_STATE))
        report["status"] = "PASS"
    except Exception as exc:
        report["blocker"] = f"{type(exc).__name__}: {exc}"
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


raise SystemExit(main())
