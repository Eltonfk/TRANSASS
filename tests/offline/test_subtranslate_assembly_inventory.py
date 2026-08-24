"""Offline deterministic tests for the AUTO-03D assembly inventory tool."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

TOOL_PATH = (
    Path(__file__).resolve().parents[2]
    / ".opencode/tools/subtranslate_assembly_inventory.py"
)


def _load_tool():
    spec = importlib.util.spec_from_file_location("subtranslate_assembly_inventory", TOOL_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError("inventory module could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def inventory():
    return _load_tool()


def _envelope(translations: list[dict]) -> bytes:
    return json.dumps({"message": {"content": json.dumps({"translations": translations}, ensure_ascii=False)}},
                      ensure_ascii=False).encode("utf-8")


def test_parse_translations_current_envelope(inventory):
    raw = _envelope([{"id": 50, "text": "oi"}, {"id": 51, "text": "tchau"}])
    assert inventory.parse_translations(raw) == {50: "oi", 51: "tchau"}


def test_parse_translations_raw_array_fallback(inventory):
    raw = json.dumps({"translations": [{"id": 7, "text": "sete"}]}).encode("utf-8")
    assert inventory.parse_translations(raw) == {7: "sete"}


def test_parse_translations_rejects_empty(inventory):
    with pytest.raises(ValueError):
        inventory.parse_translations(json.dumps({"message": {"content": "{\"translations\":[]}"}}).encode("utf-8"))


def test_scan_attempt_parses_and_cross_checks(inventory, tmp_path):
    attempt = tmp_path / "FAM" / "calls" / "attempt-x"
    attempt.mkdir(parents=True)
    (attempt / "response.body").write_bytes(_envelope([{"id": 1, "text": "um"}, {"id": 2, "text": "dois"}]))
    (attempt / "request_metadata.json").write_text(json.dumps({"unit_ids": [1, 2, 3]}), encoding="utf-8")
    entry = inventory.scan_attempt(attempt, "FAM", "attempt-x")
    assert entry["status"] == "PARSED"
    assert entry["translations"] == {1: "um", 2: "dois"}
    assert entry["request_unit_match"]["missing"] == [3]
    assert entry["request_unit_match"]["extra"] == []
    assert len(entry["response_sha256"]) == 64


def test_scan_attempt_records_unparsable(inventory, tmp_path):
    attempt = tmp_path / "FAM" / "calls" / "attempt-y"
    attempt.mkdir(parents=True)
    (attempt / "response.body").write_bytes(b"not json at all {{{")
    entry = inventory.scan_attempt(attempt, "FAM", "attempt-y")
    assert entry["status"].startswith("UNPARSABLE:")
    assert entry["translations"] == {}


def test_scan_attempt_without_response_is_recorded(inventory, tmp_path):
    attempt = tmp_path / "FAM" / "calls" / "attempt-z"
    attempt.mkdir(parents=True)
    entry = inventory.scan_attempt(attempt, "FAM", "attempt-z")
    assert entry["status"] == "NO_RESPONSE_EVIDENCE"


def test_scan_families_sorted_and_deterministic(inventory, tmp_path):
    for family in ("B_BETA", "A_ALPHA"):
        attempt = tmp_path / family / "calls" / "attempt-1"
        attempt.mkdir(parents=True)
        (attempt / "response.body").write_bytes(_envelope([{"id": 9, "text": family}]))
    first = inventory.scan_families(tmp_path)
    second = inventory.scan_families(tmp_path)
    assert first == second
    assert [entry["family"] for entry in first[0]] == ["A_ALPHA", "B_BETA"]


def test_build_inventory_coverage_conflicts_and_gaps(inventory):
    universe = [1, 2, 3, 4, 5]
    entries = [
        {"family": "F1", "attempt_id": "a1", "status": "PARSED",
         "translations": {1: "um", 2: "versao-A"}, "request_unit_match": None,
         "response_sha256": "0" * 64},
        {"family": "F2", "attempt_id": "a2", "status": "PARSED",
         "translations": {2: "versao-B"}, "request_unit_match": None,
         "response_sha256": "1" * 64},
    ]
    built = inventory.build_inventory(universe, entries, [])
    assert built["universe_total"] == 5
    assert built["translated_distinct"] == 2
    assert built["coverage_pct"] == 40.0
    assert built["untranslated_ids"] == [3, 4, 5]
    assert built["conflicting_units"] == {"2": 2}
    assert built["translations"]["2"] == [
        {"family": "F1", "attempt_id": "a1", "text": "versao-A", "response_sha256": "0" * 64, "request_unit_match": None},
        {"family": "F2", "attempt_id": "a2", "text": "versao-B", "response_sha256": "1" * 64, "request_unit_match": None},
    ]


def test_tool_has_no_apply_surface_and_no_transport():
    source = TOOL_PATH.read_text(encoding="utf-8")
    assert "--apply" not in source
    assert "requests.post" not in source
    assert "import requests" not in source
