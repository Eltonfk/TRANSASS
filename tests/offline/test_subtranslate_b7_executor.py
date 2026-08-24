"""Offline deterministic tests for the AUTO-03D B7 batch executor.

Focus: contract shape, B7-only bindings, state-independent fail-closed
behavior without a canonical authorization object (blocks before any backup
or network), and response validation semantics.  No network, no model, no
writes.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

TOOL_PATH = (
    Path(__file__).resolve().parents[2]
    / ".opencode/tools/subtranslate_b7_executor.py"
)

B7_UNIT_IDS = [66, 67, 68, 69, 70, 71, 72, 73]


def _load_tool():
    spec = importlib.util.spec_from_file_location("subtranslate_b7_executor", TOOL_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError("executor module could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def executor():
    return _load_tool()


def test_plan_contract_without_authorization(executor):
    result = executor.plan(require_authorization=False)
    assert result["status"] == "READY"
    assert result["action_id"] == "B7_BATCH_EXECUTION"
    assert result["executor_id"] == "B7_BATCH_EXECUTOR_V1"
    assert result["max_client_calls"] == 1
    assert result["max_http_posts"] == 1
    assert result["max_retries"] == 0
    assert result["authorization_present"] is False
    assert result["side_effects_performed"] is False
    required = result["required_from_authorization"]
    assert set(required) == {
        "operation_id", "family_id", "episode_id", "unit_ids",
        "unit_membership_sha256", "request_payload_sha256",
        "request_payload_path", "logical_batch_id",
    }


def test_apply_fail_closed_without_authorization(executor, tmp_path, monkeypatch):
    """Without a canonical authorization object, apply blocks before any
    backup or network activity — deterministically, independent of the live
    canonical state."""
    fixture = tmp_path / "PROJECT_STATE.json"
    fixture.write_text(json.dumps({}), encoding="utf-8")
    monkeypatch.setattr(executor, "PROJECT_STATE", fixture)
    with pytest.raises(executor.ExecutionBlocked) as excinfo:
        executor.execute()
    assert "B7_EXECUTION_AUTHORIZATION_ABSENT" in str(excinfo.value)


def test_bindings_are_b7_exclusive():
    source = TOOL_PATH.read_text(encoding="utf-8")
    assert 'AUTHORIZATION_KEY = "auto03d_b7_batch_execution_authorization_r1"' in source
    assert 'AUTHORIZED_NEXT_ACTION = "B7_BATCH_EXECUTION_AUTHORIZED"' in source
    assert 'B7_LOGICAL_BATCH_ID = "v226-initial-000007"' in source
    assert "auto03d_b5" not in source
    assert "auto03d_b6" not in source
    assert '"batch_index": 7' in source


def test_toolchain_fingerprint_is_stable_64hex(executor):
    fingerprint = executor.current_toolchain_fingerprint()
    assert isinstance(fingerprint, str) and len(fingerprint) == 64
    assert all(c in "0123456789abcdef" for c in fingerprint)
    assert executor.current_toolchain_fingerprint() == fingerprint


def test_validate_translation_accepts_exact_membership(executor):
    value = {"translations": [{"id": uid, "text": f"texto {uid}"} for uid in B7_UNIT_IDS]}
    projected, normalized = executor.validate_translation(value, B7_UNIT_IDS)
    assert normalized is False
    assert [row["id"] for row in projected["translations"]] == B7_UNIT_IDS


def test_validate_translation_rejects_wrong_membership(executor):
    swapped = list(reversed(B7_UNIT_IDS))
    value = {"translations": [{"id": uid, "text": f"texto {uid}"} for uid in swapped]}
    with pytest.raises(executor.ExecutionBlocked) as excinfo:
        executor.validate_translation(value, B7_UNIT_IDS)
    assert "B7_RESPONSE_MEMBERSHIP_INVALID" in str(excinfo.value)


def test_validate_translation_rejects_cardinality(executor):
    value = {"translations": [{"id": uid, "text": "x"} for uid in B7_UNIT_IDS[:-1]]}
    with pytest.raises(executor.ExecutionBlocked) as excinfo:
        executor.validate_translation(value, B7_UNIT_IDS)
    assert "B7_RESPONSE_CARDINALITY_INVALID" in str(excinfo.value)


def test_validate_translation_flags_extra_properties(executor):
    rows = [{"id": uid, "text": f"t{uid}", "extra": 1} for uid in B7_UNIT_IDS]
    projected, normalized = executor.validate_translation({"translations": rows}, B7_UNIT_IDS)
    assert normalized is True
    assert all(set(row) == {"id", "text"} for row in projected["translations"])


def test_cli_plan_smoke(executor, capsys):
    exit_code = executor.main(["--plan"])
    captured = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert captured["status"] == "READY"
    assert captured["action_id"] == "B7_BATCH_EXECUTION"
