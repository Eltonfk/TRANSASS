#!/usr/bin/env python3
"""AUTO-03D memory recovery inventory (strictly read-only).

Phase of route (b) "recover before translating": discovers SQLite memory
databases left by prior runs under runtime-evidence and dumps their structure
and contents so previously-invisible translations can be recovered.

Guarantees:

  * every database is opened with ``file:...?immutable=1`` — SQLite is
    forbidden from writing anything (no journal, no WAL, no locking files);
    any write attempt on such a connection raises;
  * no user-controlled paths; only databases discovered under the authority
    runtime-evidence tree are read;
  * row dumps are capped per table to keep the output bounded;
  * corrupt or unreadable databases are recorded and never abort the scan.

The tool has no apply surface and never invokes a model, transport or retry.
Mapping database rows to unit ids happens AFTER this inventory reveals the
real schemas — in a separate gate.
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

ACTION_ID = "MEMORY_RECOVERY_INVENTORY"
EXECUTOR_ID = "MEMORY_RECOVERY_INVENTORY_V1"
DB_SUFFIXES = (".sqlite3", ".db")
EXCLUDED_SUFFIXES = ("-wal", "-shm", "-journal")
MAX_ROWS_PER_TABLE = 10000


class Blocked(RuntimeError):
    """Fail-closed inventory abort; never a transport or write condition."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def discover_databases(root: Path) -> list[Path]:
    if not root.is_dir():
        raise Blocked("INVENTORY_RUNTIME_ROOT_MISSING")
    found: list[Path] = []
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
        found.append(path)
    return found


def open_ro(path: Path) -> sqlite3.Connection:
    # immutable=1: SQLite opens the file strictly read-only and creates no
    # journal/WAL/locking side files. Write attempts raise.
    return sqlite3.connect(f"file:{path}?immutable=1", uri=True)


def introspect_database(path: Path) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "sha256": sha256_bytes(path.read_bytes()),
        "status": "PARSED",
        "tables": [],
        "error": None,
    }
    try:
        connection = open_ro(path)
    except Exception as exc:
        entry["status"] = f"UNREADABLE:{type(exc).__name__}"
        entry["error"] = str(exc)
        return entry
    try:
        cursor = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        table_names = [row[0] for row in cursor.fetchall()]
        for table_name in table_names:
            safe_name = table_name.replace('"', '""')
            columns = [
                {"name": col[1], "type": col[2]}
                for col in connection.execute(f'PRAGMA table_info("{safe_name}")').fetchall()
            ]
            row_count = connection.execute(f'SELECT COUNT(*) FROM "{safe_name}"').fetchone()[0]
            rows: list[dict[str, Any]] = []
            if row_count <= MAX_ROWS_PER_TABLE and columns:
                column_names = [col["name"] for col in columns]
                quoted = ", ".join(f'"{name.replace(chr(34), chr(34) * 2)}"' for name in column_names)
                for row in connection.execute(f'SELECT {quoted} FROM "{safe_name}"'):
                    rows.append(dict(zip(column_names, row)))
            entry["tables"].append({
                "name": table_name,
                "columns": columns,
                "row_count": row_count,
                "rows_dumped": len(rows),
                "rows": rows,
            })
    except Exception as exc:
        entry["status"] = f"INTROSPECTION_FAILED:{type(exc).__name__}"
        entry["error"] = str(exc)
    finally:
        connection.close()
    return entry


def plan() -> dict[str, Any]:
    databases = [introspect_database(path) for path in discover_databases(RUNTIME_EVIDENCE_ROOT)]
    parsed = sum(1 for db in databases if db["status"] == "PARSED")
    total_rows = sum(
        table["row_count"]
        for db in databases
        for table in db["tables"]
    )
    return {
        "status": "READY",
        "mode": "INVENTORY_READ_ONLY",
        "action_id": ACTION_ID,
        "executor_id": EXECUTOR_ID,
        "side_effects_performed": False,
        "open_mode": "sqlite_immutable_read_only",
        "databases_scanned": len(databases),
        "databases_parsed": parsed,
        "total_rows_across_tables": total_rows,
        "databases": databases,
        "model_call": False,
        "transport": False,
        "runtime_write": False,
        "next_gate": "MEMORY_SCHEMA_MAPPING_REQUIRED",
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
