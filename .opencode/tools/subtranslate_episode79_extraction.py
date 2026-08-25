#!/usr/bin/env python3
"""AUTO-03D episode-79 Library extraction (strictly read-only).

Route (b) "recover before translating", step 2: extracts everything the
Library databases hold for episode 79 — review sessions, their segment
reviews (source/generated text per event index), publications, subtitle
records and the referenced subtitle objects.

Guarantees:

  * identical database copies are de-duplicated by SHA-256;
  * every database is opened with ``file:...?immutable=1`` — zero writes;
  * output is deterministic JSON; corrupt/unreadable databases are recorded,
    never aborting the scan.

The tool has no apply surface and never invokes a model, transport or retry.
The assembler scope (phase D2) is decided from this extraction in a separate
gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import stat as _stat
from pathlib import Path
from typing import Any

CANDIDATE_ROOT = Path("/home/palhacinho/codex-projects/subtranslate-v238-candidate")
AUTHORITY_ROOT = Path("/home/palhacinho/codex-projects/anime-subtitle-translator-review")
RUNTIME_EVIDENCE_ROOT = AUTHORITY_ROOT / "runtime-evidence"

ACTION_ID = "E79_LIBRARY_EXTRACTION"
EXECUTOR_ID = "E79_LIBRARY_EXTRACTION_V1"
EPISODE_ID = "79"
DB_SUFFIXES = (".sqlite3", ".db")
EXCLUDED_SUFFIXES = ("-wal", "-shm", "-journal")


class Blocked(RuntimeError):
    """Fail-closed extraction abort; never a transport or write condition."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def discover_databases(root: Path) -> list[Path]:
    if not root.is_dir():
        raise Blocked("EXTRACTION_RUNTIME_ROOT_MISSING")
    found: list[Path] = []
    seen_hashes: set[str] = set()
    for path in sorted(root.rglob("*")):
        try:
            info = path.lstat()
        except OSError:
            continue
        if not _stat.S_ISREG(info.st_mode):
            continue
        if path.suffix.lower() not in DB_SUFFIXES:
            continue
        if any(path.name.lower().endswith(suffix) for suffix in EXCLUDED_SUFFIXES):
            continue
        digest = sha256_bytes(path.read_bytes())
        if digest in seen_hashes:
            continue
        seen_hashes.add(digest)
        found.append(path)
    return found


def open_ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?immutable=1", uri=True)


def _is_episode(value: Any) -> bool:
    return value is not None and str(value) == EPISODE_ID


def extract_episode79(path: Path) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "path": str(path),
        "sha256": sha256_bytes(path.read_bytes()),
        "status": "PARSED",
        "error": None,
        "review_sessions": [],
        "segments": [],
        "publications": [],
        "subtitle_records": [],
        "subtitle_objects": [],
    }
    try:
        connection = open_ro(path)
    except Exception as exc:
        entry["status"] = f"UNREADABLE:{type(exc).__name__}"
        entry["error"] = str(exc)
        return entry

    def rows(query: str) -> list[sqlite3.Row]:
        cursor = connection.execute(query)
        cursor.row_factory = sqlite3.Row
        return cursor.fetchall()

    try:
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}

        session_rows = []
        if "review_session" in tables:
            session_rows = rows('SELECT * FROM "review_session"')
        for row in session_rows:
            record = dict(row)
            if _is_episode(record.get("episode_id")):
                entry["review_sessions"].append(record)
        session_ids = [str(s["id"]) for s in entry["review_sessions"]]

        if "segment_review" in tables and session_ids:
            placeholders = ",".join("?" for _ in session_ids)
            parameterized = connection.execute(
                f'SELECT * FROM "segment_review" WHERE review_session_id IN ({placeholders}) '
                'ORDER BY review_session_id, event_index',
                session_ids,
            )
            parameterized.row_factory = sqlite3.Row
            for row in parameterized.fetchall():
                entry["segments"].append(dict(row))

        publication_rows = []
        if "publication" in tables:
            publication_rows = rows('SELECT * FROM "publication"')
        for row in publication_rows:
            record = dict(row)
            if _is_episode(record.get("episode_id")):
                entry["publications"].append(record)

        record_rows = []
        if "subtitle_record" in tables:
            record_rows = rows('SELECT * FROM "subtitle_record"')
        object_ids: list[str] = []
        for row in record_rows:
            record = dict(row)
            if not _is_episode(record.get("episode_id")):
                continue
            entry["subtitle_records"].append(record)
            object_id = record.get("object_id")
            if object_id is not None and str(object_id) not in object_ids:
                object_ids.append(str(object_id))

        if "subtitle_object" in tables and object_ids:
            placeholders = ",".join("?" for _ in object_ids)
            cursor = connection.execute(
                f'SELECT * FROM "subtitle_object" WHERE id IN ({placeholders})', object_ids
            )
            cursor.row_factory = sqlite3.Row
            for row in cursor.fetchall():
                entry["subtitle_objects"].append(dict(row))
    except Exception as exc:
        entry["status"] = f"EXTRACTION_FAILED:{type(exc).__name__}"
        entry["error"] = str(exc)
    finally:
        connection.close()
    return entry


def plan() -> dict[str, Any]:
    databases = [extract_episode79(path) for path in discover_databases(RUNTIME_EVIDENCE_ROOT)]
    parsed = [db for db in databases if db["status"] == "PARSED"]
    if not parsed:
        raise Blocked("E79_NO_PARSED_DATABASE")
    primary = parsed[0]
    others_identical = all(db["sha256"] == primary["sha256"] for db in parsed)
    segments = primary["segments"]
    event_indexes = sorted({seg["event_index"] for seg in segments if seg.get("event_index") is not None})
    return {
        "status": "READY",
        "mode": "EXTRACTION_READ_ONLY",
        "action_id": ACTION_ID,
        "executor_id": EXECUTOR_ID,
        "side_effects_performed": False,
        "episode_id": EPISODE_ID,
        "databases_scanned": len(databases),
        "databases_parsed": len(parsed),
        "identical_copies": others_identical,
        "primary_database": primary["path"],
        "summary": {
            "review_sessions": len(primary["review_sessions"]),
            "segments": len(segments),
            "distinct_event_indexes": len(event_indexes),
            "event_index_min": event_indexes[0] if event_indexes else None,
            "event_index_max": event_indexes[-1] if event_indexes else None,
            "publications": len(primary["publications"]),
            "subtitle_records": len(primary["subtitle_records"]),
            "subtitle_objects": len(primary["subtitle_objects"]),
        },
        "extraction": primary,
        "model_call": False,
        "transport": False,
        "runtime_write": False,
        "next_gate": "ASSEMBLY_D2_SCOPING_REQUIRED",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", action="store_true", required=True)
    args = parser.parse_args(argv)
    try:
        result = plan()
        print(json.dumps(result, sort_keys=True, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "FAIL_STOP", "action_id": ACTION_ID,
                          "side_effects_performed": False,
                          "blocker": f"{type(exc).__name__}:{exc}"}, sort_keys=True, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
