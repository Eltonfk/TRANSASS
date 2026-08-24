"""Offline deterministic tests for the AUTO-03D batch range runner."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

TOOL_PATH = (
    Path(__file__).resolve().parents[2]
    / ".opencode/tools/subtranslate_batch_range_runner.py"
)


def _load_tool():
    spec = importlib.util.spec_from_file_location("subtranslate_batch_range_runner", TOOL_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError("runner module could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def runner():
    return _load_tool()


def test_validate_range_accepts_valid_span(runner):
    runner.validate_range(10, 24)
    runner.validate_range(10, 10)  # single-batch range is valid


@pytest.mark.parametrize("frm,to", [(5, 20), (10, 233), (20, 10), (-1, 10)])
def test_validate_range_rejects_invalid(runner, frm, to):
    with pytest.raises(runner.RangeBlocked) as excinfo:
        runner.validate_range(frm, to)
    assert "BATCH_RANGE_INVALID" in str(excinfo.value)


def test_range_authorization_key_template(runner):
    assert runner.range_authorization_key(10, 24) == (
        "auto03d_batch_range_10_24_execution_authorization_r1"
    )


def test_pointer_templates(runner):
    assert runner.logical_batch_id(11) == "v226-initial-000011"
    assert runner.authorized_next_action(11) == "B11_BATCH_EXECUTION_AUTHORIZED"
    assert runner.decision_required_next_action(11) == (
        "B11_EXTERNAL_DECISION_REQUIRED_NO_AUTOMATIC_RESEND"
    )


def test_classify_batch_skip_when_reconciled(runner):
    state = {
        "next_action": "B10_EXTERNAL_DECISION_REQUIRED_NO_AUTOMATIC_RESEND",
        "auto03d_b10_post_execution_reconciliation_r1": {"any": True},
    }
    assert runner.classify_batch(state, 10) == "SKIP"


def test_classify_batch_run_when_expected_pointer(runner):
    state = {"next_action": "B10_EXTERNAL_DECISION_REQUIRED_NO_AUTOMATIC_RESEND"}
    assert runner.classify_batch(state, 10) == "RUN"


def test_classify_batch_refuses_mid_cycle_interrupted(runner):
    state = {
        "next_action": "B10_BATCH_EXECUTION_AUTHORIZED",
        "auto03d_b10_batch_execution_authorization_r1": {"any": True},
    }
    with pytest.raises(runner.RangeBlocked) as excinfo:
        runner.classify_batch(state, 10)
    assert "BATCH_MID_CYCLE_INTERRUPTED" in str(excinfo.value)


def test_classify_batch_refuses_unexpected_pointer(runner):
    state = {"next_action": "SOMETHING_ELSE"}
    with pytest.raises(runner.RangeBlocked) as excinfo:
        runner.classify_batch(state, 10)
    assert "BATCH_POINTER_UNEXPECTED" in str(excinfo.value)


def test_progress_report_structure(runner):
    report = runner.progress_report(
        10, 14,
        [
            {"batch_index": 10, "status": "COMPLETED"},
            {"batch_index": 11, "status": "SKIPPED_ALREADY_RECONCILED"},
        ],
        stopped=False,
        blocker=None,
    )
    assert report["range"] == "10-14"
    assert report["batches_completed"] == [10]
    assert report["batches_skipped_resumed"] == [11]
    assert report["completed_count"] == 1
    assert report["stopped_early"] is False
    assert report["blocker"] is None


def test_backup_dir_for_returns_path_without_concatenation_error(runner):
    """Regression: backup_dir construction must not mix Path '/' with '+'."""
    path = runner.backup_dir_for(10, "20260823T213324Z")
    assert isinstance(path, Path)
    assert str(path).endswith("subtranslate-b10-documentary-write-20260823T213324Z")
    assert runner.BACKUP_PARENT.name in str(path)


def test_executor_fingerprint_namespace_differs_from_runner(runner):
    """Regression (FINGERPRINT-NAMESPACE-FIX): the per-batch object must carry
    the EXECUTOR's toolchain fingerprint, not the runner's — the executor is
    who revalidates it at apply time."""
    executor_fp = runner.executor_toolchain_fingerprint()
    runner_fp = runner.current_toolchain_fingerprint()
    assert len(executor_fp) == 64 and len(runner_fp) == 64
    assert executor_fp != runner_fp  # different component lists => different digests


def test_no_placeholder_snapshot_in_generator(runner):
    """Regression (SNAPSHOT-PLACEHOLDER-FIX): the per-batch object generator
    must propagate the REAL range snapshot, never a literal placeholder."""
    source = TOOL_PATH.read_text(encoding="utf-8")
    assert '"snapshot_fingerprint": "BOUND_BY_RANGE_AUTHORIZATION"' not in source
    assert "range_record[\"snapshot_fingerprint\"]" in source


def test_runner_delegates_transport_to_proven_executor_and_has_no_direct_network():
    source = TOOL_PATH.read_text(encoding="utf-8")
    assert "subtranslate_batch_planner.py" in source
    assert "subtranslate_batch_executor.py" in source
    assert "--apply" in source  # delegated via subprocess to the proven executor
    assert "import requests" not in source
    assert "requests.post" not in source
