"""Offline tests for the V2.3.8 web integration Fase 2 (C5, C6, C7).

Deterministic: no model calls, no HTTP, no Library writes.  Mocks flask so
app.py can be imported without the container dependency.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

# Mock flask before importing app
import os
import tempfile

_TMP_STATE = tempfile.mkdtemp(prefix="v238-fase2-state-")
os.environ["TRANSLATOR_WEB_STATE_DIR"] = _TMP_STATE
os.environ["TRANSLATOR_BASE_LIBRARY"] = _TMP_STATE

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
