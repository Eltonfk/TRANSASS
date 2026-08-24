"""Offline deterministic tests for the AUTO-03D generalized batch planner."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

TOOL_PATH = (
    Path(__file__).resolve().parents[2]
    / ".opencode/tools/subtranslate_batch_planner.py"
)


def _load_tool():
    spec = importlib.util.spec_from_file_location("subtranslate_batch_planner", TOOL_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError("planner module could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def planner():
    return _load_tool()


@pytest.fixture(scope="module")
def plan8(planner):
    return planner.plan(8)


def _is_hex64(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def test_logical_batch_id_convention(planner):
    assert planner.logical_batch_id(8) == "v226-initial-000008"
    assert planner.logical_batch_id(232) == "v226-initial-000232"


def test_membership_convention_matches_recorded_batch4(planner):
    recorded = planner.PAYLOAD_ORACLE_RECORDED_BATCHES[4]
    assert planner.membership_sha256(recorded["unit_ids"]) == (
        "025b18adf784186c3ee4d0d41faafa85442265615620ffff4f93454b329e107c"
    )


def test_plan_ready_for_batch_8(plan8):
    assert plan8["status"] == "READY"
    assert plan8["mode"] == "PLAN_READ_ONLY"
    target = plan8["target"]
    assert target["logical_batch_id"] == "v226-initial-000008"
    assert target["batch_index"] == 8
    assert isinstance(target["unit_ids"], list) and target["unit_ids"]
    assert target["event_count"] == len(target["unit_ids"])
    assert _is_hex64(target["unit_membership_sha256"])
    assert _is_hex64(target["request_payload_sha256"])
    assert target["request_payload_bytes"] > 0
    # Batch 8 has no recorded anchor: correctness rests on the engine proof.
    assert target["anchor_status"] == "ENGINE_PROOF_ONLY_NO_RECORDED_ANCHOR"
    assert target["recorded_event_id_set_sha256"] is None


def test_engine_proof_oracles_all_match(plan8):
    validation = plan8["validation"]
    membership = validation["plan_membership_reconstructed"]
    assert sorted(membership) == ["0", "1", "2", "3", "4", "5", "6", "7"]
    assert all(value == "MATCH" for value in membership.values())
    payloads = validation["payload_oracle_reconstructed"]
    assert sorted(payloads) == ["1", "2", "3", "4"]
    assert all(value == "MATCH" for value in payloads.values())
    assert validation["packed_initial_batches_total"] == 233


def test_plan_ready_for_batch_9(planner):
    result = planner.plan(9)
    assert result["status"] == "READY"
    assert result["target"]["logical_batch_id"] == "v226-initial-000009"
    assert result["target"]["batch_index"] == 9
    assert _is_hex64(result["target"]["unit_membership_sha256"])


def test_plan_is_deterministic(planner, plan8):
    assert planner.plan(8) == plan8


def test_range_guard_below_minimum(planner):
    with pytest.raises(planner.Blocked) as excinfo:
        planner.plan(5)
    assert "BATCH_INDEX_OUT_OF_RANGE" in str(excinfo.value)


def test_range_guard_above_maximum(planner):
    with pytest.raises(planner.Blocked) as excinfo:
        planner.plan(233)
    assert "BATCH_INDEX_OUT_OF_RANGE" in str(excinfo.value)


def test_fail_closed_when_source_missing(planner, monkeypatch):
    monkeypatch.setattr(
        planner,
        "SOURCE_CANDIDATES",
        (planner.AUTHORITY_ROOT / "does-not-exist/e07.ass",),
    )
    with pytest.raises(planner.Blocked) as excinfo:
        planner.plan(8)
    assert "BATCH_SOURCE_NOT_FOUND" in str(excinfo.value)


def test_tool_has_no_apply_surface_and_no_transport():
    source = TOOL_PATH.read_text(encoding="utf-8")
    assert "--apply" not in source
    assert "requests.post" not in source
    assert "import requests" not in source
