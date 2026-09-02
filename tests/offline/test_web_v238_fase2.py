"""Offline tests for the V2.3.8 web integration Fase 2 (C5, C6, C7).

Deterministic: no model calls, no HTTP, no Library writes.  Mocks flask so
app.py can be imported without the container dependency.
"""
from __future__ import annotations

import sys
import types
import hashlib
import json
from pathlib import Path

# Mock flask before importing app only when the optional dependency is absent.
# A process-global fake otherwise contaminates unrelated app tests.
import os
import tempfile

_TMP_STATE = tempfile.mkdtemp(prefix="v238-fase2-state-")
os.environ["TRANSLATOR_WEB_STATE_DIR"] = _TMP_STATE
os.environ["TRANSLATOR_BASE_LIBRARY"] = _TMP_STATE

try:
    import flask as _flask  # noqa: F401
except ImportError:
    flask_mock = types.ModuleType("flask")

    class _Flask:
        def __init__(self, *args, **kwargs):
            self.routes = {}

        def route(self, rule, **options):
            def decorator(fn):
                self.routes[rule] = fn
                return fn
            return decorator

        def test_client(self):
            return None

    flask_mock.Flask = _Flask
    flask_mock.Response = type("Response", (), {})
    flask_mock.jsonify = lambda *a, **k: {"_jsonify": True}
    flask_mock.request = types.SimpleNamespace(get_json=lambda silent=False: {})
    flask_mock.send_file = lambda *a, **k: None
    sys.modules["flask"] = flask_mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "subtranslate"))

import app as web  # noqa: E402


# ---------------------------------------------------------------------------
# M8: _project_v238_summary
# ---------------------------------------------------------------------------


def test_m8_projects_completed_summary():
    result = {
        "stages": [{"id": "FULL_TRANSLATION_V238", "result": {}}],
        "karaoke": {"song_units": 8, "translated_units": 8, "failures": [], "structural_failures": []},
        "primary_ledger": [
            {"event_id": 1, "status": "RESOLVED"},
            {"event_id": 2, "status": "RESOLVED"},
        ],
        "calls": 2,
        "retry_calls": 0,
        "pipeline_wall_seconds": 12.5,
        "operation_budget": {"qwen_physical_maximum": 131, "qwen_reserved": 2, "total_reserved": 2},
    }
    projected = web._project_v238_summary(result)
    assert projected["status"] == "COMPLETED"
    assert projected["events"] == 2
    assert projected["resolved"] == 2
    assert projected["unresolved"] == 0
    assert projected["v238_metrics"]["calls"] == 2
    assert projected["v238_metrics"]["budget_remaining"] == 129


def test_m8_projects_measured_model_calls_when_legacy_calls_is_zero():
    projected = web._project_v238_summary({
        "stages": [],
        "karaoke": {"song_units": 1, "translated_units": 1, "failures": [], "structural_failures": []},
        "primary_ledger": [{"event_id": 1, "status": "RESOLVED"}],
        "calls": 0,
        "model_calls": 146,
        "retry_calls": 0,
        "aggregated_metrics": {"model_calls_total": 146, "v226_retries": 2},
    })
    assert projected["calls"] == 146
    assert projected["retry_calls"] == 0


def test_m8_projects_failed_with_unresolved():
    result = {
        "stages": [{"id": "FULL_TRANSLATION_V238", "result": {}}],
        "karaoke": {"song_units": 8, "translated_units": 8, "failures": [], "structural_failures": []},
        "primary_ledger": [
            {"event_id": 1, "status": "RESOLVED"},
            {"event_id": 2, "status": "BLOCKED", "objective_reason_code": "PRIMARY_SCHEMA_REJECTED"},
        ],
        "calls": 3,
        "retry_calls": 1,
        "pipeline_wall_seconds": 20.0,
        "operation_budget": {"qwen_physical_maximum": 131, "qwen_reserved": 3, "total_reserved": 3},
    }
    projected = web._project_v238_summary(result)
    assert projected["status"] == "FAILED"
    assert projected["unresolved"] == 1
    assert "v238_unresolved_units" in projected["critical_flags"]


def test_m8_does_not_allow_skipped_unresolved_units_to_complete():
    result = {
        "stages": [{"id": "FULL_TRANSLATION_V238", "result": {}}],
        "karaoke": {"song_units": 2, "translated_units": 2, "failures": [], "structural_failures": []},
        "primary_ledger": [
            {"event_id": 1, "status": "RESOLVED"},
            {"event_id": 2, "status": "BLOCKED"},
        ],
        "llama_phase": {"state": "SKIPPED_ALLOWED"},
    }
    projected = web._project_v238_summary(result)
    assert projected["status"] == "FAILED"
    assert projected["critical_flags"] == ["v238_unresolved_units"]


def test_m8_projects_failed_with_karaoke_failures():
    result = {
        "stages": [{"id": "FULL_TRANSLATION_V238", "result": {}}],
        "karaoke": {"song_units": 8, "translated_units": 7, "failures": [{"reason": "x"}], "structural_failures": []},
        "primary_ledger": [],
        "calls": 2,
        "retry_calls": 0,
        "pipeline_wall_seconds": 10.0,
        "operation_budget": {},
    }
    projected = web._project_v238_summary(result)
    assert projected["status"] == "FAILED"


# ---------------------------------------------------------------------------
# M9: _new_operation_id
# ---------------------------------------------------------------------------


def test_m9_new_operation_id_is_unique():
    a = web._new_operation_id({})
    b = web._new_operation_id({})
    assert a != b
    assert len(a) == 32  # uuid4().hex


# ---------------------------------------------------------------------------
# M6: _project_primary_ledger_to_units
# ---------------------------------------------------------------------------


def test_m6_projects_ledger_to_units():
    ledger = [
        {"event_id": 1, "canonical_unit_id": "u1", "status": "RESOLVED",
         "objective_reason_code": None, "flags": [], "failure_reason": None,
         "primary_model_tag": "qwen3.5:9b", "primary_attempts": 1},
        {"event_id": 2, "canonical_unit_id": "u2", "status": "BLOCKED",
         "objective_reason_code": "PRIMARY_SCHEMA_REJECTED", "flags": ["SCHEMA"],
         "failure_reason": "bad json", "primary_model_tag": "qwen3.5:9b", "primary_attempts": 3},
    ]
    units = web._project_primary_ledger_to_units(ledger)
    assert units[0]["status"] == "resolved"
    assert units[1]["status"] == "failed"
    assert units[1]["reason_code"] == "PRIMARY_SCHEMA_REJECTED"


# ---------------------------------------------------------------------------
# C5: roteamento _effective_pipeline
# ---------------------------------------------------------------------------


def test_c5_effective_pipeline_from_transport_config():
    import tempfile

    from transport_config_store import save_transport_config

    with tempfile.TemporaryDirectory(prefix="c5-") as raw:
        cfg_path = Path(raw) / "transport_config.json"
        save_transport_config(cfg_path, {
            "primary": {"provider": "ollama", "model": "qwen3.5:9b"},
            "pipeline": "v2_3_8",
        })
        old_path = web.TRANSPORT_CONFIG_PATH
        web.TRANSPORT_CONFIG_PATH = cfg_path
        try:
            assert web._effective_pipeline() == "v2_3_8"
        finally:
            web.TRANSPORT_CONFIG_PATH = old_path


def test_c5_effective_pipeline_falls_back_to_env():
    import os

    old_env = os.environ.get("TRANSLATOR_PIPELINE")
    os.environ["TRANSLATOR_PIPELINE"] = "legacy"
    try:
        assert web._effective_pipeline() == "legacy"
    finally:
        if old_env is None:
            os.environ.pop("TRANSLATOR_PIPELINE", None)
        else:
            os.environ["TRANSLATOR_PIPELINE"] = old_env


# ---------------------------------------------------------------------------
# C7: _job_telemetry com v238_metrics
# ---------------------------------------------------------------------------


def test_c7_telemetry_includes_v238_metrics():
    job = {
        "id": "job-1", "status": "COMPLETED", "stage": "COMPLETED",
        "summary": {
            "events": 2, "resolved": 2, "unresolved": 0,
            "v238_metrics": {"calls": 2, "budget_remaining": 129},
        },
    }
    telemetry = web._job_telemetry(job)
    assert telemetry["v238_metrics"]["calls"] == 2
    assert telemetry["v238_metrics"]["budget_remaining"] == 129


def test_c7_telemetry_reports_live_semantic_reconstruction(tmp_path):
    old_state_dir = web.STATE_DIR
    web.STATE_DIR = tmp_path
    try:
        capture_root = tmp_path / "v238-runs" / "job-semantic" / "captures"
        completed = capture_root / "v238-ownership-event-341"
        active = capture_root / "v238-ownership-event-342"
        completed.mkdir(parents=True)
        active.mkdir(parents=True)
        (completed / "capture_state.json").write_text(json.dumps({
            "call_id": completed.name, "state": "RESPONSE_DURABLE",
        }), encoding="utf-8")
        (active / "capture_state.json").write_text(json.dumps({
            "call_id": active.name, "state": "TRANSPORT_IN_PROGRESS",
        }), encoding="utf-8")
        telemetry = web._job_telemetry({
            "id": "job-semantic", "status": "TRANSLATING", "stage": "TRANSLATING",
            "summary": {"events": 8, "resolved": 8, "unresolved": 0},
        })
    finally:
        web.STATE_DIR = old_state_dir

    assert telemetry["stage"] == "SEMANTIC_RECONSTRUCTION"
    assert telemetry["semantic_calls"] == 2
    assert telemetry["semantic_completed"] == 1
    assert telemetry["semantic_in_progress"] == 1
    assert telemetry["semantic_incomplete"] == 0
    assert telemetry["current_event_id"] == 342
    assert telemetry["last_activity_at"]


def test_candidate_artifact_is_retained_downloadable_and_hash_bound(tmp_path):
    old_state_dir = web.STATE_DIR
    old_jobs = web.state["jobs"]
    web.STATE_DIR = tmp_path
    job_id = "candidate-job"
    name = "episode.pt-BR.ass"
    payload = b"[Script Info]\n"
    candidate_dir = tmp_path / "staging" / f"retranslation-{job_id}"
    candidate_dir.mkdir(parents=True)
    candidate = candidate_dir / name
    candidate.write_bytes(payload)
    job = {
        "id": job_id,
        "status": "COMPLETED",
        "stage": "CANDIDATE_READY",
        "candidate_output_name": name,
        "candidate_output_sha256": hashlib.sha256(payload).hexdigest(),
        "candidate_download_url": f"/retranslation/candidates/{job_id}/download",
    }
    web.state["jobs"] = [job]
    try:
        assert web._candidate_artifact(job) == candidate
        response = web.app.test_client().get(job["candidate_download_url"])
        assert response.status_code == 200
        assert response.data == payload
        candidate.write_bytes(b"tampered")
        rejected = web.app.test_client().get(job["candidate_download_url"])
        assert rejected.status_code == 404
    finally:
        web.state["jobs"] = old_jobs
        web.STATE_DIR = old_state_dir
