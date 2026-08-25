#!/usr/bin/env python3
"""AUTO-03D HANDOFF R2 detach — archive the append-only HANDOFF into the
history root and start a fresh, compact HANDOFF that points to the archive.

The archived file is byte-preserved (never edited).  The new HANDOFF carries a
summary addendum plus the canonical pointer.  Backs up both documents before
writing and records an additive canonical object.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat as _stat
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

AUTHORITY_ROOT = Path("/home/palhacinho/codex-projects/anime-subtitle-translator-review")
HISTORY_ROOT = Path("/home/palhacinho/codex-projects/anime-subtitle-translator-review-history")
PROJECT = AUTHORITY_ROOT / "PROJECT_STATE.json"
HANDOFF = AUTHORITY_ROOT / "HANDOFF_CHATGPT.md"
BACKUP_PARENT = Path("/home/palhacinho/opencode-backups")
HANDOFF_HISTORY_DIR = HISTORY_ROOT / "handoff"

ACTION_ID = "HANDOFF_R2_DETACH"
RUNNER_ID = "HANDOFF_R2_DETACH_V1"
RECON_KEY = "auto03e_handoff_r2_detach_r1"


class Blocked(RuntimeError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def regular(path: Path) -> os.stat_result:
    info = path.lstat()
    if _stat.S_ISLNK(info.st_mode) or not _stat.S_ISREG(info.st_mode):
        raise Blocked(f"UNSAFE_FILE:{path}")
    return info


def fsync_dir(path: Path) -> None:
    fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def publish(path: Path, data: bytes, mode: int) -> None:
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.hdet-", dir=path.parent)
    tmp = Path(raw)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        fsync_dir(path.parent)
    finally:
        tmp.unlink(missing_ok=True)


def main() -> int:
    regular(PROJECT)
    regular(HANDOFF)
    before_handoff = HANDOFF.read_bytes()
    before_project = PROJECT.read_bytes()
    state = json.loads(before_project)
    if RECON_KEY in state:
        raise Blocked("HANDOFF_R2_DETACH_ALREADY_APPLIED")

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = BACKUP_PARENT / f"subtranslate-handoff-r2-detach-{stamp}"
    backup_dir.mkdir(mode=0o700)
    (backup_dir / "HANDOFF_CHATGPT.md.before").write_bytes(before_handoff)
    (backup_dir / "PROJECT_STATE.json.before").write_bytes(before_project)
    fsync_dir(backup_dir)

    HANDOFF_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    archive = HANDOFF_HISTORY_DIR / f"HANDOFF_CHATGPT-{stamp}.md"
    if archive.exists():
        raise Blocked("HANDOFF_ARCHIVE_ALREADY_EXISTS")
    archive.write_bytes(before_handoff)
    fsync_dir(HANDOFF_HISTORY_DIR)

    summary = (
        "HANDOFF arquivado integralmente (append-only preservado) em "
        f"{archive.name} ({len(before_handoff.splitlines())} linhas, "
        f"sha256 {sha256_bytes(before_handoff)[:16]}...).\n"
        "Estado consolidado: temporada E07-E12 traduzida e publicada (v2.4.0); "
        "retries Gemini resolveram E09 B150/B194; pendentes de revisao humana: "
        "E08 B210 (evento 1486), E10 B2, E11 B96, E12 B47. "
        "Proxima fase: revisao humana + release por episodio."
    )
    new_handoff = (
        "# HANDOFF_CHATGPT\n\n"
        "## Addendum AUTO-03D-HANDOFF-R2-DETACH-R1\n\n"
        f"{summary}\n\n"
        f"ARCHIVE={archive}\n"
        f"ARCHIVE_SHA256={sha256_bytes(before_handoff)}\n"
        f"ARCHIVED_AT={stamp}\n"
    ).encode("utf-8")

    after = json.loads(before_project)
    after[RECON_KEY] = {
        "action_id": ACTION_ID, "runner_id": RUNNER_ID, "applied_at": datetime.now(UTC).isoformat(),
        "archive": str(archive), "archive_sha256": sha256_bytes(before_handoff),
        "archived_lines": len(before_handoff.splitlines()),
        "new_handoff_lines": len(new_handoff.splitlines()),
        "backup_root": str(backup_dir),
        "future_side_effects_authorized": False,
    }
    after_project = (json.dumps(after, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()

    project_info = regular(PROJECT)
    handoff_info = regular(HANDOFF)
    published: list[tuple[Path, bytes, int]] = []
    try:
        publish(PROJECT, after_project, _stat.S_IMODE(project_info.st_mode))
        published.append((PROJECT, before_project, _stat.S_IMODE(project_info.st_mode)))
        publish(HANDOFF, new_handoff, _stat.S_IMODE(handoff_info.st_mode))
        published.append((HANDOFF, before_handoff, _stat.S_IMODE(handoff_info.st_mode)))
        if json.loads(PROJECT.read_text(encoding="utf-8")) != json.loads(after_project):
            raise Blocked("POST_PUBLISH_VERIFICATION_FAILED")
    except Exception:
        for path, data, mode_bits in reversed(published):
            publish(path, data, mode_bits)
        raise

    print(json.dumps({
        "status": "PASS", "transition": "handoff-r2-detach",
        "archive": str(archive), "archive_sha256": sha256_bytes(before_handoff),
        "archived_lines": len(before_handoff.splitlines()),
        "new_handoff_lines": len(new_handoff.splitlines()),
        "backup_root": str(backup_dir),
        "project_state_sha256": sha256_bytes(after_project),
    }, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())