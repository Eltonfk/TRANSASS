"""Offline deterministic tests for the AUTO-03D episode-79 Library extraction."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

TOOL_PATH = (
    Path(__file__).resolve().parents[2]
    / ".opencode/tools/subtranslate_episode79_extraction.py"
)


def _load_tool():
    spec = importlib.util.spec_from_file_location("subtranslate_episode79_extraction", TOOL_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError("extraction module could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _build_library(db_path: Path) -> None:
    connection = sqlite3.connect(str(db_path))
    connection.execute("CREATE TABLE review_session (id INTEGER PRIMARY KEY, episode_id INTEGER, status TEXT)")
    connection.execute(
        "CREATE TABLE segment_review (id INTEGER PRIMARY KEY, review_session_id INTEGER, "
        "event_index INTEGER, source_text TEXT, generated_text TEXT, status TEXT)"
    )
    connection.execute("CREATE TABLE publication (id INTEGER PRIMARY KEY, episode_id INTEGER, target_relative_path TEXT, target_sha256 TEXT)")
    connection.execute("CREATE TABLE subtitle_record (id INTEGER PRIMARY KEY, episode_id INTEGER, object_id INTEGER, events_total INTEGER)")
    connection.execute("CREATE TABLE subtitle_object (id INTEGER PRIMARY KEY, sha256 TEXT, storage_path TEXT)")
    # Episode 79 session + segments
    connection.execute("INSERT INTO review_session VALUES (1, 79, 'COMPLETED')")
    connection.execute("INSERT INTO segment_review VALUES (100, 1, 50, 'Figure it out', 'Resolva isso', 'REVIEWED')")
    connection.execute("INSERT INTO segment_review VALUES (101, 1, 51, 'Hello there', 'Olá', 'REVIEWED')")
    # Episode 80 session + segment (must be filtered out)
    connection.execute("INSERT INTO review_session VALUES (2, 80, 'COMPLETED')")
    connection.execute("INSERT INTO segment_review VALUES (200, 2, 1, 'Other ep', 'Outro ep', 'REVIEWED')")
    # Publications / records / objects
    connection.execute("INSERT INTO publication VALUES (10, 79, 'e79/final.ass', 'abc')")
    connection.execute("INSERT INTO publication VALUES (11, 80, 'e80/final.ass', 'def')")
    connection.execute("INSERT INTO subtitle_record VALUES (20, 79, 30, 1808)")
    connection.execute("INSERT INTO subtitle_object VALUES (30, 'deadbeef', 'objects/deadbeef')")
    connection.commit()
    connection.close()


@pytest.fixture(scope="module")
def inventory():
    return _load_tool()


def test_extract_filters_episode_79_only(inventory, tmp_path):
    db = tmp_path / "library.sqlite3"
    _build_library(db)
    entry = inventory.extract_episode79(db)
    assert entry["status"] == "PARSED"
    assert [s["id"] for s in entry["review_sessions"]] == [1]
    assert [seg["event_index"] for seg in entry["segments"]] == [50, 51]
    assert all(seg["review_session_id"] == 1 for seg in entry["segments"])
    assert [p["id"] for p in entry["publications"]] == [10]
    assert [r["id"] for r in entry["subtitle_records"]] == [20]
    assert [o["id"] for o in entry["subtitle_objects"]] == [30]


def test_plan_dedupes_identical_copies(inventory, tmp_path, monkeypatch):
    db = tmp_path / "a" / "library.sqlite3"
    db.parent.mkdir(parents=True)
    _build_library(db)
    copy_dir = tmp_path / "b" / "memory" / "db"
    copy_dir.mkdir(parents=True)
    shutil_copy = Path(str(db) + ".copy")
    shutil_copy.write_bytes(db.read_bytes())
    shutil_copy.rename(copy_dir / "subtitle_library.sqlite3")
    monkeypatch.setattr(inventory, "RUNTIME_EVIDENCE_ROOT", tmp_path)
    result = inventory.plan()
    assert result["status"] == "READY"
    assert result["databases_scanned"] == 1
    assert result["identical_copies"] is True
    assert result["summary"]["segments"] == 2
    assert result["summary"]["distinct_event_indexes"] == 2


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


def test_tool_has_no_apply_surface_and_enforces_immutable():
    source = TOOL_PATH.read_text(encoding="utf-8")
    assert "--apply" not in source
    assert "immutable=1" in source
    assert "requests.post" not in source
