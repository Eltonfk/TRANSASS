"""Offline tests for the multi-episode executor."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

TOOL_PATH = (
    Path(__file__).resolve().parents[2]
    / ".opencode/tools/subtranslate_episode_executor.py"
)


def _load_tool():
    spec = importlib.util.spec_from_file_location("subtranslate_episode_executor", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def executor():
    return _load_tool()


def test_plan_contract(executor):
    result = executor.plan(require_authorization=False, batch_index=1)
    assert result["status"] == "READY"
    assert result["action_id"] == "EPISODE_BATCH_EXECUTION"
    assert result["executor_id"] == "EPISODE_EXECUTOR_V1"
    assert result["max_client_calls"] == 1
    assert result["max_http_posts"] == 1
    assert result["max_retries"] == 0
    assert result["authorization_present"] is False
    assert result["side_effects_performed"] is False


def test_authorization_key_template(executor):
    assert executor.authorization_key(1) == "auto03d_b1_batch_execution_authorization_r1"
    assert executor.authorization_key(12) == "auto03d_b12_batch_execution_authorization_r1"


def test_authorized_next_action_template(executor):
    assert executor.authorized_next_action(1) == "B1_BATCH_EXECUTION_AUTHORIZED"


def test_logical_batch_id(executor):
    assert executor.logical_batch_id(1) == "v226-initial-000001"


def test_apply_fail_closed_without_authorization(executor, tmp_path, monkeypatch):
    fixture = tmp_path / "PROJECT_STATE.json"
    fixture.write_text(json.dumps({}), encoding="utf-8")
    monkeypatch.setattr(executor, "PROJECT_STATE", fixture)
    with pytest.raises(executor.ExecutionBlocked) as excinfo:
        executor.execute(1)
    assert "BATCH_EXECUTION_AUTHORIZATION_ABSENT" in str(excinfo.value)


def test_no_hardcoded_episode_bindings():
    source = TOOL_PATH.read_text(encoding="utf-8")
    assert "auto03d_b5" not in source
    assert "auto03d_b6" not in source
    assert "auto03d_b7" not in source
    assert "auto03d_b10" not in source
    assert '"episode_id": 79' not in source


def test_range_guard_in_main(executor, capsys):
    exit_code = executor.main(["--plan", "--batch", "0"])
    captured = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert captured["status"] == "FAIL_STOP"
    assert "BATCH_INDEX_OUT_OF_RANGE" in captured["blocker"]
