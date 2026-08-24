"""Offline deterministic tests for the AUTO-03D memory recovery inventory."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

TOOL_PATH = (
    Path(__file__).resolve().parents[2]
    / ".opencode/tools/subtranslate_memory_inventory.py"
)


def _load_tool():
    spec = importlib.util.spec_from_file_location("subtranslate_memory_inventory", TOOL_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError("inventory module could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def inventory():
    return _load_tool()


def test_discover_databases_finds_sqlite_and_ignores_sidecars(inventory, tmp_path):
    (tmp_path / "memory" / "db").mkdir(parents=True)
    good = tmp_path / "memory" / "db" / "subtitle_library.sqlite3"
    other = tmp_path / "memory" / "db" / "cache.db"
    sidecar = tmp_path / "memory" / "db" / "subtitle_library.sqlite3-wal"
    for path in (good, other, sidecar):
        path.write_bytes(b"x")
    found = inventory.discover_databases(tmp_path)
    assert good in found and other in found
    assert sidecar not in found


def test_discover_fails_closed_without_root(inventory, tmp_path):
    with pytest.raises(inventory.Blocked) as excinfo:
        inventory.discover_databases(tmp_path / "missing")
    assert "INVENTORY_RUNTIME_ROOT_MISSING" in str(excinfo.value)


def test_introspect_reveals_schema_and_rows(inventory, tmp_path):
    db = tmp_path / "mem.sqlite3"
    connection = sqlite3.connect(str(db))
    connection.execute("CREATE TABLE translations (unit_id INTEGER, text TEXT)")
    connection.execute("INSERT INTO translations VALUES (50, 'oi')")
    connection.execute("INSERT INTO translations VALUES (51, 'tchau')")
    connection.commit()
    connection.close()
    entry = inventory.introspect_database(db)
    assert entry["status"] == "PARSED"
    assert entry["tables"][0]["name"] == "translations"
    assert entry["tables"][0]["row_count"] == 2
    assert entry["tables"][0]["rows"] == [
        {"unit_id": 50, "text": "oi"},
        {"unit_id": 51, "text": "tchau"},
    ]


def test_open_ro_is_immutable_writes_fail(inventory, tmp_path):
    db = tmp_path / "mem.sqlite3"
    connection = sqlite3.connect(str(db))
    connection.execute("CREATE TABLE t (a INTEGER)")
    connection.commit()
    connection.close()
    ro = inventory.open_ro(db)
    with pytest.raises(sqlite3.OperationalError):
        ro.execute("INSERT INTO t VALUES (1)")
    ro.close()


def test_introspect_records_corrupt_database(inventory, tmp_path):
    db = tmp_path / "broken.sqlite3"
    db.write_bytes(b"this is not a sqlite database" * 10)
    entry = inventory.introspect_database(db)
    assert entry["status"].startswith(("UNREADABLE:", "INTROSPECTION_FAILED:"))
    assert entry["error"]


def test_plan_smoke_on_synthetic_tree(inventory, tmp_path, monkeypatch):
    family = tmp_path / "V238_TEST_FAMILY" / "memory" / "db"
    family.mkdir(parents=True)
    db = family / "subtitle_library.sqlite3"
    connection = sqlite3.connect(str(db))
    connection.execute("CREATE TABLE translations (unit_id INTEGER, text TEXT)")
    connection.execute("INSERT INTO translations VALUES (1, 'um')")
    connection.commit()
    connection.close()
    monkeypatch.setattr(inventory, "RUNTIME_EVIDENCE_ROOT", tmp_path)
    result = inventory.plan()
    assert result["status"] == "READY"
    assert result["databases_scanned"] == 1
    assert result["databases_parsed"] == 1
    assert result["total_rows_across_tables"] == 1
    assert result["side_effects_performed"] is False


def test_tool_has_no_apply_surface_and_enforces_immutable():
    source = TOOL_PATH.read_text(encoding="utf-8")
    assert "--apply" not in source
    assert "immutable=1" in source
    assert "requests.post" not in source
