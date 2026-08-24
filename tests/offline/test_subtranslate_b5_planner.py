"""Offline deterministic tests for the AUTO-03D B5 batch planner.

The central proof: the planner must reconstruct the already-executed initial
batches 1-4 byte-exactly (unit ids, membership hash and request-payload hash,
as recorded in the family runtime evidence).  Only then is its batch-5
derivation trustworthy.  No network, no model, no writes.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

TOOL_PATH = (
    Path(__file__).resolve().parents[2]
    / ".opencode/tools/subtranslate_b5_planner.py"
)


def _load_tool():
    spec = importlib.util.spec_from_file_location("subtranslate_b5_planner", TOOL_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError("planner module could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def planner():
    return _load_tool()


@pytest.fixture(scope="module")
def plan(planner):
    return planner.plan()


def _is_hex64(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def test_membership_convention_matches_recorded_batch4(planner):
    recorded = planner.PAYLOAD_ORACLE_RECORDED_BATCHES[4]
    assert planner.membership_sha256(recorded["unit_ids"]) == (
        "025b18adf784186c3ee4d0d41faafa85442265615620ffff4f93454b329e107c"
    )


def test_plan_is_ready_with_complete_contract(plan):
    assert plan["status"] == "READY"
    assert plan["mode"] == "PLAN_READ_ONLY"
    assert plan["side_effects_performed"] is False
    target = plan["target"]
    assert target["logical_batch_id"] == "v226-initial-000005"
    assert target["batch_index"] == 5
    assert isinstance(target["unit_ids"], list) and target["unit_ids"]
    assert all(isinstance(unit_id, int) for unit_id in target["unit_ids"])
    assert target["event_count"] == len(target["unit_ids"])
    assert _is_hex64(target["unit_membership_sha256"])
    assert _is_hex64(target["request_payload_sha256"])
    assert target["request_payload_bytes"] > 0
    assert isinstance(target["request_payload_canonical_b64"], str) and target["request_payload_canonical_b64"]
    for fact in plan["required_from_canonical_authorization"]:
        assert isinstance(fact, str) and fact


def test_execution_remains_unauthorized(plan):
    assert plan["b5_execution_authorized"] is False
    assert plan["b6_execution_authorized"] is False
    assert plan["b7_execution_authorized"] is False
    assert plan["b4_reexecution"] is False
    assert plan["model_call"] is False
    assert plan["transport"] is False
    assert plan["runtime_write"] is False


def test_recorded_batches_reconstructed_byte_exact(plan):
    validation = plan["validation"]
    membership = validation["plan_membership_reconstructed"]
    assert sorted(membership) == ["0", "1", "2", "3", "4", "5", "6", "7"]
    assert validation["plan_inventory_entries"] == 8
    assert all(value == "MATCH" for value in membership.values())
    assert validation["packed_initial_batches_total"] == 233
    payloads = validation["payload_oracle_reconstructed"]
    assert sorted(payloads) == ["1", "2", "3", "4"]
    assert all(value == "MATCH" for value in payloads.values())


def test_target_membership_matches_recorded_inventory(plan):
    target = plan["target"]
    assert target["unit_membership_sha256"] == target["recorded_event_id_set_sha256"]
    assert target["recorded_event_id_set_sha256"] == (
        "16700620d17685a17b3b22856588959a503754f8fe67950f8dc2cbcc07f58b37"
    )


def test_plan_is_deterministic(planner, plan):
    assert planner.plan() == plan


def test_fail_closed_when_source_missing(planner, monkeypatch):
    monkeypatch.setattr(
        planner,
        "SOURCE_CANDIDATES",
        (planner.AUTHORITY_ROOT / "does-not-exist/e07.ass",),
    )
    with pytest.raises(planner.Blocked) as excinfo:
        planner.plan()
    assert "B5_SOURCE_NOT_FOUND" in str(excinfo.value)


def test_fail_closed_when_batch_index_out_of_range(planner, monkeypatch):
    monkeypatch.setattr(planner, "BATCH_INDEX", 999)
    with pytest.raises(planner.Blocked) as excinfo:
        planner.plan()
    assert "B5_BATCH_INDEX_OUT_OF_RANGE" in str(excinfo.value)


def test_tool_has_no_apply_surface_and_no_transport():
    source = TOOL_PATH.read_text(encoding="utf-8")
    assert "--apply" not in source
    assert "requests.post" not in source
    assert "import requests" not in source
