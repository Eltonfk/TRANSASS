"""Offline races, durable timeout evidence and service lifetime exclusion."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import pytest

import web_retranslation_runner as runner
from v238_response_provider import DurableResponseProvider
from web_fallback_safety import safe_fallback_eligible


@pytest.fixture(autouse=True)
def forbid_real_http(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("real HTTP is forbidden in job safety regressions")
    monkeypatch.setattr("web_durable_provider._http_post", forbidden)


@pytest.fixture
def web_fixture(tmp_path, monkeypatch):
    # Also safe when this file alone is collected, before other app fixtures.
    if "app" not in sys.modules:
        monkeypatch.setenv("TRANSASS_DATA_DIR", str(tmp_path / "app-data"))
        monkeypatch.setenv("TRANSLATOR_WEB_STATE_DIR", str(tmp_path / "import-state"))
        monkeypatch.setenv("ANIME_SUBTITLE_LIBRARY_ROOT", str(tmp_path / "import-library"))
        monkeypatch.setenv("TRANSLATOR_BASE_LIBRARY", str(tmp_path / "media"))
    import app as web

    state = {
        "running": True, "paused": False, "pause_requested": False,
        "stop_requested": False, "cancel_requested": False,
        "stopped_by_user": False, "thermal_stop_requested": False,
        "process": None, "worker": None, "session_id": "fixture-session",
        "current_job_id": "fixture-job", "jobs": [],
        "log": deque(), "log_sequence": 0,
    }
    monkeypatch.setattr(web, "state", state)
    monkeypatch.setattr(web, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(web, "_persist_locked", lambda: None)
    monkeypatch.setattr(web, "_effective_pipeline", lambda: "v3")
    monkeypatch.setattr(web, "_model", lambda: "synthetic-model")
    monkeypatch.setattr(web, "_validate_retranslation_job_integrity", lambda _job: None)
    monkeypatch.setattr(web, "_validate_candidate_source_integrity", lambda _job: None)
    return web


@pytest.mark.parametrize("boundary,expected_calls,expected_phase", [
    ("audit", [], None),
    ("ingest", ["ingest"], "ARCHIVING"),
    ("lineage-1", ["ingest", "lineage-1"], "LINEAGE"),
    ("lineage-2", ["ingest", "lineage-1", "lineage-2"], "LINEAGE"),
    ("publish", ["ingest", "lineage-1", "lineage-2", "publish"], "PUBLISHING"),
    ("candidate-audit", [], None),
])
def test_stop_coordinates_retranslation_boundaries(
    web_fixture, tmp_path, monkeypatch, boundary, expected_calls, expected_phase,
):
    web = web_fixture
    source = tmp_path / "source.ass"
    source.write_bytes(b"synthetic source")
    job = {
        "id": "fixture-job", "session_id": "fixture-session", "name": "fixture",
        "source_abs": str(source), "episode_id": 1, "source_record_id": 1,
        "old_record_id": 2, "source_language": "inglês", "status": "WAITING",
        "published": False, "candidate_only": boundary == "candidate-audit",
    }
    tail = {"id": "tail", "session_id": "fixture-session", "status": "WAITING"}
    web.state["jobs"] = [job, tail]
    entered, proceed = threading.Event(), threading.Event()
    calls, errors = [], []

    def pause_at(name):
        if name == boundary or (name == "audit" and boundary == "candidate-audit"):
            entered.set()
            assert proceed.wait(5), "fixture stop request did not arrive"

    class Library:
        def get_record(self, record_id):
            return {"id": record_id, "language": "eng"}

        def ingest_file(self, *_args, **_kwargs):
            calls.append("ingest")
            pause_at("ingest")
            return {"id": 42}

        def add_lineage(self, _child, parent, _relation):
            name = f"lineage-{parent}"
            calls.append(name)
            pause_at(name)

        def publish(self, *_args, **_kwargs):
            calls.append("publish")
            pause_at("publish")
            (tmp_path / "published.ass").write_bytes(b"synthetic publication")
            return {"status": "PUBLISHED"}

    class Process:
        def __init__(self, command, **_kwargs):
            Path(command[command.index("--output") + 1]).write_bytes(b"synthetic candidate")
            self.stdout = iter([])

        def wait(self):
            return 0

    def audit(*_args, **_kwargs):
        pause_at("audit")
        return {"status": "VALID", "eligible_for_archive": True}

    monkeypatch.setattr(web, "subtitle_library", Library())
    monkeypatch.setattr(web.subprocess, "Popen", Process)
    monkeypatch.setattr(web, "audit_record", audit)
    monkeypatch.setattr(web, "archive_eligibility", lambda _audit: {"eligible_for_archive": True})

    def work():
        try:
            web._run_retranslation_episode(job)
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=work)
    web.state["worker"] = worker
    worker.start()
    try:
        assert entered.wait(5), "worker did not reach fixture boundary"
        response = web.app.test_client().post("/stop")
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["action"] == "stopping"
        assert payload["in_progress_operation"] == expected_phase
        assert payload["cancellation_deferred"] == bool(expected_phase)
        assert web.state["running"] is True
        assert tail["status"] == "CANCELLED"
        assert tail["cancel_requested"] is True
        # Even a later reset of queue flags cannot revoke this job's stop.
        with web.state_lock:
            web.state["stop_requested"] = False
            web.state["cancel_requested"] = False
    finally:
        proceed.set()
        worker.join(timeout=5)
    assert not worker.is_alive()
    assert not errors
    assert calls == expected_calls
    assert "active_side_effect" not in job
    if boundary == "publish":
        assert job["status"] == "COMPLETED"
        assert job["published"] is True
        assert job["reason"] == "publication_completed_after_stop_request"
        assert (tmp_path / "published.ass").is_file()
    else:
        assert job["status"] == "CANCELLED"
        assert job["reason"] == "stopped_by_user"
        assert job["published"] is False
        assert not (tmp_path / "published.ass").exists()
        assert Path(job["failure_staging_path"]).is_dir()
        if "ingest" in calls:
            assert job["new_record_id"] == 42
            assert job["library_record_created"] is True


def test_cancellation_gate_rejects_thermal_stop(web_fixture):
    web = web_fixture
    web.state["thermal_stop_requested"] = True
    with pytest.raises(web.JobCancellationRequested, match="THERMAL_GUARD_TRIPPED"):
        with web._job_side_effect({}, "ARCHIVING"):
            pytest.fail("thermal stop crossed the mutation gate")


def test_latched_job_stop_rejects_further_transport_requests(web_fixture):
    gate = web_fixture._v3_thermal_gate_for_provider(
        "openai", None, job={"cancel_requested": True},
    )
    assert gate() is True


def test_cancellation_gate_refuses_mutation_when_state_persistence_fails(web_fixture, monkeypatch):
    web = web_fixture
    job = {}
    def fail():
        raise web.StatePersistenceError("synthetic persistence failure")
    monkeypatch.setattr(web, "_persist_locked", fail)
    with pytest.raises(web.StatePersistenceError):
        with web._job_side_effect(job, "ARCHIVING"):
            pytest.fail("mutation proceeded without durable state")
    assert "active_side_effect" not in job


@pytest.mark.parametrize("scenario", ["physical-timeout", "pre-client-timeout", "cancel-validation", "cancel-exception"])
def test_app_v3_uses_shared_fallback_and_cancel_rules(
    web_fixture, tmp_path, monkeypatch, scenario,
):
    import pipeline_v3
    import transport_config_store
    import v3_runtime

    web = web_fixture
    source = tmp_path / "source.ass"
    source.write_bytes(b"synthetic source")
    job = {"id": "fixture-job", "session_id": "fixture-session", "name": "fixture",
        "source_abs": str(source), "source_language": "inglês", "status": "WAITING"}
    web.state["jobs"] = [job]
    config = {"primary": {"provider": "openai", "model": "primary"},
        "fallback": {"provider": "openai", "model": "fallback"},
        "keys": {"openai": "offline-fixture-key"}}
    monkeypatch.setattr(transport_config_store, "load_transport_config", lambda _path: config)
    monkeypatch.setattr(web, "_require_v3_library_episode", lambda *_args: {"id": 1})
    monkeypatch.setattr(web, "_v3_source_record", lambda *_args: {"id": 1})
    monkeypatch.setattr(web, "archive_eligibility", lambda _audit: {"eligible_for_archive": True,
        "blocking_flags": [], "review_flags": []})
    attempts, effects = [], []

    def create(_config, section, *, capture_root, **_kwargs):
        attempts.append(section["model"])
        provider = DurableResponseProvider("LIVE_CAPTURED", capture_root=capture_root,
            client=lambda _payload: (_ for _ in ()).throw(TimeoutError("synthetic client timeout")))
        return SimpleNamespace(name="openai", model=section["model"]), provider

    monkeypatch.setattr(v3_runtime, "create_v3_live_transport", create)
    monkeypatch.setattr(pipeline_v3, "make_v3_transport_call",
        lambda _transport, *, response_provider, **_kwargs: response_provider.respond)

    def cancel():
        assert web.app.test_client().post("/stop").get_json()["action"] == "stopping"
        # A stale queue reset cannot turn this job into a publishable result.
        web.state["cancel_requested"] = False
        web.state["stop_requested"] = False

    def translate(_source, output, *, transport_call, **_kwargs):
        if scenario == "physical-timeout":
            transport_call({"operation": "translation", "text": "source"})
        elif scenario == "pre-client-timeout" and attempts[-1] == "primary":
            raise TimeoutError("synthetic pre-client timeout")
        elif scenario == "cancel-exception":
            cancel()
            raise RuntimeError("synthetic cooperative cancellation")
        output.write_bytes(b"synthetic translation")
        return {"pipeline": "v3_0_0", "elapsed_seconds": 0}

    def audit(*_args, **_kwargs):
        if scenario == "cancel-validation":
            cancel()
        return {"status": "VALID"}

    class Library:
        def ingest_file(self, *_args, **_kwargs):
            effects.append("ingest")
            return {"id": 42}
        def add_lineage(self, *_args):
            effects.append("lineage")
        def publish(self, *_args, **_kwargs):
            effects.append("publish")
        def set_record_review_status(self, *_args, **_kwargs):
            effects.append("review")

    monkeypatch.setattr(pipeline_v3, "translate_subtitle_file_v3", translate)
    monkeypatch.setattr(web, "audit_record", audit)
    monkeypatch.setattr(web, "subtitle_library", Library())
    web._run_episode_v3(job)
    if scenario == "pre-client-timeout":
        assert attempts == ["primary", "fallback"]
        assert job["status"] == "COMPLETED"
        assert job["published"] is True
        assert effects == ["ingest", "lineage", "publish", "review"]
    else:
        assert attempts == ["primary"]
        assert effects == []
        assert job["status"] == ("FAILED" if scenario == "physical-timeout" else "CANCELLED")
        if scenario.startswith("cancel"):
            assert job["reason"] == "stopped_by_user"


@pytest.mark.parametrize("metrics", [None, {}, {"physical_client_calls": 1},
    {"physical_client_calls": "0"}, {"physical_client_calls": False},
    {"physical_client_calls": -1}])
def test_fallback_requires_explicit_zero_physical_calls(tmp_path, metrics):
    provider = SimpleNamespace(metrics=metrics, capture_root=tmp_path / "captures")
    assert not safe_fallback_eligible(
        TimeoutError("synthetic timeout"), response_provider=provider,
        output_path=tmp_path / "output.ass",
    )


@pytest.mark.parametrize("artifact", ["none", "output", "capture", "capture-symlink", "root-file"])
def test_fallback_rejects_persisted_or_partial_primary_evidence(tmp_path, artifact):
    root, output = tmp_path / "captures", tmp_path / "output.ass"
    provider = SimpleNamespace(metrics={"physical_client_calls": 0}, capture_root=root)
    if artifact == "output":
        output.write_bytes(b"partial output")
    elif artifact == "capture":
        (root / "old-call").mkdir(parents=True)
        (root / "old-call" / "capture_state.json").write_text('{"state":"TRANSPORT_IN_PROGRESS"}')
    elif artifact == "capture-symlink":
        root.symlink_to(tmp_path / "missing", target_is_directory=True)
    elif artifact == "root-file":
        root.write_bytes(b"invalid root")
    assert safe_fallback_eligible(
        TimeoutError("synthetic timeout"), response_provider=provider, output_path=output,
    ) is (artifact == "none")


@pytest.fixture
def runner_fixture(tmp_path, monkeypatch):
    import v3_runtime
    import pipeline_v3

    config = {
        "primary": {"provider": "openai", "model": "primary"},
        "fallback": {"provider": "openai", "model": "fallback"},
    }
    source = tmp_path / "source.ass"
    source.write_bytes(b"synthetic source")
    monkeypatch.setattr(sys, "argv", ["runner", "--source", str(source),
        "--output", str(tmp_path / "output.ass"), "--pipeline", "v3"])
    monkeypatch.setattr(runner, "load_transport_config", lambda _path: config)
    monkeypatch.setattr(runner, "default_state_dir", lambda: tmp_path / "state")
    monkeypatch.setattr(runner, "_provider_for", lambda engine, *_args: SimpleNamespace(
        name=engine["provider"], model=engine["model"],
    ))
    monkeypatch.setattr(runner, "public_summary", lambda result, **_kwargs: result)
    providers, attempts = [], []

    def create(_config, section, *, capture_root, **_kwargs):
        provider = DurableResponseProvider(
            "LIVE_CAPTURED", capture_root=capture_root,
            client=lambda _payload: (_ for _ in ()).throw(TimeoutError("synthetic client timeout")),
        )
        providers.append(provider)
        attempts.append(section["model"])
        return SimpleNamespace(name="openai", model=section["model"]), provider

    monkeypatch.setattr(v3_runtime, "create_v3_live_transport", create)
    monkeypatch.setattr(pipeline_v3, "make_v3_transport_call",
        lambda _transport, *, response_provider, **_kwargs: response_provider.respond)
    return providers, attempts


def test_runner_timeout_after_physical_client_never_falls_back(runner_fixture, monkeypatch):
    providers, attempts = runner_fixture

    def execute(_pipeline, _source, _output, context):
        context["transport_call"]({"operation": "translation", "text": "source"})

    monkeypatch.setattr(runner, "execute_pipeline_plan", execute)
    with pytest.raises(TimeoutError, match="synthetic client timeout"):
        runner.main()
    assert attempts == ["primary"]
    assert providers[0].metrics["physical_client_calls"] == 1
    states = list(providers[0].capture_root.glob("*/capture_state.json"))
    assert len(states) == 1
    assert json.loads(states[0].read_text())["state"] not in {"RESPONSE_DURABLE", "VALIDATED_PASS"}


def test_runner_safe_failure_before_physical_client_can_fallback(runner_fixture, monkeypatch, capsys):
    providers, attempts = runner_fixture

    def execute(_pipeline, _source, output, _context):
        if attempts[-1] == "primary":
            raise TimeoutError("synthetic pre-client timeout")
        output.write_bytes(b"synthetic fallback artifact")
        return {"status": "COMPLETED"}

    monkeypatch.setattr(runner, "execute_pipeline_plan", execute)
    assert runner.main() == 0
    assert attempts == ["primary", "fallback"]
    assert providers[0].metrics["physical_client_calls"] == 0
    assert '"transport_used": "fallback"' in capsys.readouterr().out


def test_runner_missing_artifact_does_not_repeat_primary(runner_fixture, monkeypatch):
    _providers, attempts = runner_fixture
    monkeypatch.setattr(runner, "execute_pipeline_plan", lambda *_args: {"status": "COMPLETED"})
    with pytest.raises(RuntimeError, match="WEB_RETRANSLATION_OUTPUT_REQUIRED"):
        runner.main()
    assert attempts == ["primary"]


def test_imported_idle_service_holds_lease_until_process_exit(tmp_path):
    from state_access import StateAccessBusy, StateAccessLease

    state_root = tmp_path / "state"
    env = {**os.environ, "TRANSASS_DATA_DIR": str(tmp_path / "app-data"),
        "TRANSLATOR_WEB_STATE_DIR": str(state_root),
        "ANIME_SUBTITLE_LIBRARY_ROOT": str(tmp_path / "library"),
        "TRANSLATOR_BASE_LIBRARY": str(tmp_path / "media"),
        "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src" / "subtranslate")}
    script = (
        "import app, importlib; saved = app._service_state_lease; "
        "importlib.reload(app); assert saved is app._service_state_lease; "
        "print('idle', flush=True); input()"
    )
    child = subprocess.Popen([sys.executable, "-c", script], env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "idle"
        with pytest.raises(StateAccessBusy):
            StateAccessLease(state_root).acquire()
        child.communicate("exit\n", timeout=10)
        assert child.returncode == 0
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=10)
    with StateAccessLease(state_root):
        assert (state_root / ".state-access.lock").is_file()


def test_busy_lease_refuses_import_before_library_initialization(tmp_path):
    from state_access import StateAccessLease

    state_root = tmp_path / "state"
    library_root = tmp_path / "library"
    env = {**os.environ, "TRANSASS_DATA_DIR": str(tmp_path / "app-data"),
        "TRANSLATOR_WEB_STATE_DIR": str(state_root),
        "ANIME_SUBTITLE_LIBRARY_ROOT": str(library_root),
        "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src" / "subtranslate")}
    with StateAccessLease(state_root):
        result = subprocess.run([sys.executable, "-c", "import app"], env=env,
            capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert "StateAccessBusy" in result.stderr
    assert not library_root.exists()
