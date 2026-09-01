"""Offline tests for the web translation path fixes.

Covers:
- Fix: retranslation runner passes a resolved source language into the
  pipeline context (previously a NameError: transport_config out of scope).
- Fix: embedded-track selection prefers full dialogue tracks over
  forced/signs tracks of the same configured language.
- Fix: V226 materialization honors TRANSLATOR_SOURCE_LANGUAGE from the
  environment when no explicit execution context is provided (v2_3_0 path).

No ffprobe/ffmpeg, no model calls, no Library writes.
"""
from __future__ import annotations

import argparse
import json
import sys
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "subtranslate"))

import web_retranslation_runner as wrr  # noqa: E402
import anime_subtitle_translator as at  # noqa: E402
import production_v2_2_5_adapter as v225  # noqa: E402
import pipeline_orchestrator as orchestrator  # noqa: E402


# ---------------------------------------------------------------------------
# Fix: retranslation runner source language wiring
# ---------------------------------------------------------------------------


def test_legacy_ollama_url_accepts_base_or_chat_endpoint():
    assert at._ollama_endpoint("http://ollama:11434") == "http://ollama:11434/api/chat"
    assert at._ollama_endpoint("http://ollama:11434/api") == "http://ollama:11434/api/chat"
    assert at._ollama_endpoint("http://ollama:11434/api/chat") == "http://ollama:11434/api/chat"


def test_run_pipeline_passes_source_language(tmp_path, monkeypatch):
    source = tmp_path / "ep01.ass"
    source.write_text("[Script Info]\n")
    output = tmp_path / "ep01.pt-BR.ass"
    args = argparse.Namespace(
        source=source, output=output, memory_root=None,
        anime_series_id=None, episode_id=None, job_id="test-job",
        pipeline="v2_3_0", series_title="Show", episode_title="Episode",
    )
    captured = {}

    def fake_execute(plan_id, src, dst, context):
        captured["context"] = context
        return {"ok": True}

    monkeypatch.setattr(wrr, "execute_pipeline_plan", fake_execute)
    result = wrr._run_pipeline(args, "v2_3_0", None, "francês")
    assert result == {"ok": True}
    assert captured["context"]["source_language"] == "francês"
    assert captured["context"]["operation"] == "RETRANSLATE"


def test_orchestrator_passes_context_to_v230_full_adapter(tmp_path, monkeypatch):
    source = tmp_path / "source.ass"
    output = tmp_path / "output.ass"
    captured = {}

    def fake_adapter(*args, **kwargs):
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(
        orchestrator,
        "get_pipeline_plan",
        lambda _plan_id: SimpleNamespace(
            adapter_module="fake_v230_adapter",
            adapter_function="translate_subtitle_file_v2_2_6",
        ),
    )
    monkeypatch.setattr(
        orchestrator.importlib,
        "import_module",
        lambda _name: SimpleNamespace(translate_subtitle_file_v2_2_6=fake_adapter),
    )
    context = {
        "transport": object(),
        "source_language": "francês",
        "model_override": "gemini-3.6-flash",
    }

    orchestrator._call_full_adapter("v2_3_0", source, output, context)

    assert captured["execution_context"] is context
    assert captured["execution_context"]["source_language"] == "francês"
    assert captured["execution_context"]["transport"] is context["transport"]


def test_main_resolves_source_language_from_config(monkeypatch, tmp_path):
    """main() resolves: per-job env -> transport config -> English."""
    monkeypatch.setattr(wrr, "TRANSPORT_CONFIG_PATH", tmp_path / "missing.json")
    monkeypatch.setattr(wrr, "load_transport_config", lambda path: {"source_language": "espanhol"})
    seen = {}
    def fake_run(args, pipeline, transport, source_language):
        seen["language"] = source_language
        return {}
    monkeypatch.setattr(wrr, "_run_pipeline", fake_run)
    source = tmp_path / "ep01.ass"
    source.write_text("")
    argv = ["prog", "--source", str(source), "--output", str(tmp_path / "out.ass")]
    monkeypatch.setattr(sys, "argv", argv)
    assert wrr.main() == 0
    assert seen["language"] == "espanhol"

    # Per-job env must win over the global transport config: the store always
    # materializes a non-empty default ("inglês") that would mask the job.
    monkeypatch.setenv("TRANSLATOR_SOURCE_LANGUAGE", "francês")
    assert wrr.main() == 0
    assert seen["language"] == "francês"

    monkeypatch.setattr(wrr, "load_transport_config", lambda path: {})
    assert wrr.main() == 0
    assert seen["language"] == "francês"

    monkeypatch.delenv("TRANSLATOR_SOURCE_LANGUAGE")
    assert wrr.main() == 0
    assert seen["language"] == "inglês"


# ---------------------------------------------------------------------------
# Fix: track selection prefers full dialogue over forced/signs
# ---------------------------------------------------------------------------


def _ffprobe_result(streams):
    response = MagicMock()
    response.stdout = json.dumps({"streams": streams})
    return response


def _stream(index, title, default=0):
    return {
        "index": index,
        "codec_name": "ass",
        "tags": {"language": "fre", "title": title},
        "disposition": {"default": default},
    }


def test_track_selection_prefers_full_over_forced(monkeypatch, tmp_path):
    """Regression for Paranoia Agent S01E01: 'French [Forced]' (index 3) was
    chosen over 'French [Full]' (index 4) because both scored equally."""
    streams = [_stream(3, "French [Forced]"), _stream(4, "French [Full]", default=1)]
    monkeypatch.setattr(at, "SOURCE_LANGUAGE", "francês")
    monkeypatch.setattr(at.subprocess, "run", lambda *a, **k: _ffprobe_result(streams))
    idx, lang, ext = at.find_subtitle_stream(tmp_path / "ep01.mkv")
    assert idx == 4
    assert lang == "fre"
    assert ext == ".ass"


def test_track_selection_still_demotes_signs_songs(monkeypatch, tmp_path):
    streams = [
        _stream(2, "Signs & Songs"),
        _stream(3, "Dialogue", default=1),
    ]
    monkeypatch.setattr(at, "SOURCE_LANGUAGE", "francês")
    monkeypatch.setattr(at.subprocess, "run", lambda *a, **k: _ffprobe_result(streams))
    idx, _, _ = at.find_subtitle_stream(tmp_path / "ep01.mkv")
    assert idx == 3


def test_track_selection_tiebreaks_by_default_flag(monkeypatch, tmp_path):
    """Two untitled same-language tracks: the default-flagged one wins."""
    streams = [_stream(5, ""), _stream(6, "", default=1)]
    monkeypatch.setattr(at, "SOURCE_LANGUAGE", "francês")
    monkeypatch.setattr(at.subprocess, "run", lambda *a, **k: _ffprobe_result(streams))
    idx, _, _ = at.find_subtitle_stream(tmp_path / "ep01.mkv")
    assert idx == 6


def test_track_selection_deterministic_on_full_tie(monkeypatch, tmp_path):
    """No flags, no titles: lowest index wins deterministically."""
    streams = [_stream(7, ""), _stream(6, "")]
    monkeypatch.setattr(at, "SOURCE_LANGUAGE", "francês")
    monkeypatch.setattr(at.subprocess, "run", lambda *a, **k: _ffprobe_result(streams))
    idx, _, _ = at.find_subtitle_stream(tmp_path / "ep01.mkv")
    assert idx == 6


# ---------------------------------------------------------------------------
# Fix: environment source-language fallback on the V226 path
# ---------------------------------------------------------------------------


def test_resolve_source_language_context_wins(monkeypatch):
    monkeypatch.setenv("TRANSLATOR_SOURCE_LANGUAGE", "francês")
    assert v225._resolve_source_language({"source_language": "espanhol"}) == "espanhol"


def test_resolve_source_language_env_fallback(monkeypatch):
    monkeypatch.setenv("TRANSLATOR_SOURCE_LANGUAGE", "francês")
    assert v225._resolve_source_language(None) == "francês"
    assert v225._resolve_source_language({}) == "francês"


def test_resolve_source_language_defaults_to_english(monkeypatch):
    monkeypatch.delenv("TRANSLATOR_SOURCE_LANGUAGE", raising=False)
    assert v225._resolve_source_language(None) == "inglês"
    assert v225._resolve_source_language({"source_language": ""}) == "inglês"


# ---------------------------------------------------------------------------
# Fix: retranslation preflight/queue honor per-episode source language
# ---------------------------------------------------------------------------


def test_preflight_uses_per_episode_language(monkeypatch):
    import os
    import tempfile
    import types as _types

    _fake_flask = _types.ModuleType("flask")
    _fake_flask.Flask = MagicMock()
    _fake_flask.Flask.return_value.route = lambda *a, **k: (lambda f: f)
    _fake_flask.Response = MagicMock()
    _fake_flask.jsonify = MagicMock()
    _fake_flask.request = MagicMock()
    _fake_flask.send_file = MagicMock()
    sys.modules.setdefault("flask", _fake_flask)

    # app.py materializes its state dir at import time; keep it off /app.
    os.environ.setdefault("TRANSLATOR_WEB_STATE_DIR", tempfile.mkdtemp(prefix="st-"))
    os.environ.setdefault("ANIME_SUBTITLE_LIBRARY_ROOT", tempfile.mkdtemp(prefix="lib-"))
    os.environ.setdefault("ANIME_LIBRARY_ROOTS", tempfile.mkdtemp(prefix="media-"))

    import app as app_module

    captured = {}

    def fake_resolve(library, episode_id, record_id=None, materialize=False,
                     job_id=None, source_language="inglês"):
        captured["source_language"] = source_language
        return {"available": True, "record_id": record_id, "status": "SOURCE_AVAILABLE_LIBRARY"}

    episode = {"id": 85, "classification": "ANIME", "episode": "01",
               "media_filename": "ep01.mkv", "series_title": "Show"}
    monkeypatch.setattr(app_module, "_episode_row", lambda eid: episode)
    monkeypatch.setattr(app_module, "_current_validated_record", lambda eid: None)
    monkeypatch.setattr(app_module, "_preferred_library_record", lambda eid: {"id": 146})
    monkeypatch.setattr(app_module, "resolve_episode_source", fake_resolve)
    app_module.state.setdefault("source_status", {})

    result = app_module._retranslation_preflight(
        [85], bulk=False, source_languages={85: "francês"})
    assert result["counts"]["eligible"] == 1
    assert captured["source_language"] == "francês"

    # Sem seleção explícita: cai no idioma global configurado.
    app_module._retranslation_preflight([85], bulk=False)
    assert captured["source_language"] == app_module._global_source_language()


def test_retranslation_can_queue_only_eligible_selected_episodes(tmp_path, monkeypatch):
    """Mixed selections may explicitly queue eligible items only.

    The default remains fail-closed; the partial behavior is opt-in from the
    selected-episodes UI action and must report the blocked items instead of
    silently treating them as retried jobs.
    """
    import os
    import tempfile
    import types as _types

    _fake_flask = _types.ModuleType("flask")
    _fake_flask.Flask = MagicMock()
    _fake_flask.Flask.return_value.route = lambda *a, **k: (lambda f: f)
    _fake_flask.Response = MagicMock()
    _fake_flask.jsonify = MagicMock()
    _fake_flask.request = MagicMock()
    _fake_flask.send_file = MagicMock()
    sys.modules.setdefault("flask", _fake_flask)
    os.environ.setdefault("TRANSLATOR_WEB_STATE_DIR", tempfile.mkdtemp(prefix="st-"))
    os.environ.setdefault("ANIME_SUBTITLE_LIBRARY_ROOT", tempfile.mkdtemp(prefix="lib-"))
    os.environ.setdefault("ANIME_LIBRARY_ROOTS", tempfile.mkdtemp(prefix="media-"))

    import app as app_module

    source = tmp_path / "source.ass"
    source.write_text("[Script Info]\n", encoding="utf-8")
    preflight = {
        "ok": False, "bulk": False, "force_current": False,
        "pipeline": "v2_3_0", "model": "qwen3.5:9b", "total": 3,
        "results": [], "skipped": [],
        "eligible": [{
            "episode": {"id": 85, "series_id": 1, "episode": "01", "media_filename": "ep01.mkv"},
            "old": {"id": 153},
            "source_status": {"available": True, "record_id": 151, "path": str(source)},
            "preflight": {"episode_id": 85, "status": "ELIGIBLE"},
        }, {
            "episode": {"id": 86, "series_id": 1, "episode": "02", "media_filename": "ep02.mkv"},
            "old": {"id": 154},
            "source_status": {"available": True, "record_id": 152, "path": str(source)},
            "preflight": {"episode_id": 86, "status": "ELIGIBLE"},
        }],
        "blocked": [{"episode_id": 87, "status": "SOURCE_NOT_FOUND", "reason": "sem versão"}],
        "counts": {"eligible": 2, "skipped_current_validated": 0, "blocked": 1},
    }
    monkeypatch.setattr(app_module, "_retranslation_preflight", lambda *a, **k: preflight)
    captured_languages = []
    monkeypatch.setattr(
        app_module, "resolve_episode_source",
        lambda *a, **k: captured_languages.append(k["source_language"]) or {
            "available": True, "record_id": 151, "path": str(source),
        },
    )
    monkeypatch.setattr(app_module, "_persist_locked", lambda: None)
    monkeypatch.setattr(app_module, "_start_worker_locked", MagicMock())

    state_snapshot = dict(app_module.state)
    with app_module.state_lock:
        app_module.state.update({
            "running": False, "jobs": [], "session_id": None,
            "log": app_module.deque(maxlen=app_module.MAX_LOGS), "log_sequence": 0,
        })

    try:
        try:
            app_module._queue_retranslation(
                [85, 86, 87], source_languages={85: "francês", 86: "espanhol"},
            )
        except app_module.LibraryError as exc:
            assert "retranslation_preflight_blocked" in str(exc)
        else:
            raise AssertionError("default queue must remain fail-closed")

        # Bulk remains fail-closed even if a caller sends the partial flag.
        try:
            app_module._queue_retranslation(
                [85, 86, 87], confirm=True,
                source_languages={85: "francês", 86: "espanhol"},
                process_eligible_only=True,
            )
        except app_module.LibraryError as exc:
            assert "retranslation_preflight_blocked" in str(exc)
        else:
            raise AssertionError("bulk queue must remain fail-closed")

        # A partial selection with no eligible item must not create a session.
        no_eligible = dict(preflight, eligible=[], counts={"eligible": 0, "skipped_current_validated": 0, "blocked": 3})
        monkeypatch.setattr(app_module, "_retranslation_preflight", lambda *a, **k: no_eligible)
        try:
            app_module._queue_retranslation(
                [87], process_eligible_only=True,
            )
        except app_module.LibraryError as exc:
            assert "não encontrou episódio elegível" in str(exc)
        else:
            raise AssertionError("an empty partial queue must fail closed")

        monkeypatch.setattr(app_module, "_retranslation_preflight", lambda *a, **k: preflight)
        result = app_module._queue_retranslation(
            [85, 86, 87], source_languages={85: "francês", 86: "espanhol"}, process_eligible_only=True,
        )
        assert result["queued"] == 2
        assert result["not_eligible"] == 1
        assert len(app_module.state["jobs"]) == 2
        assert [job["episode_id"] for job in app_module.state["jobs"]] == [85, 86]
        assert [job["source_language"] for job in app_module.state["jobs"]] == ["francês", "espanhol"]
        assert captured_languages == ["francês", "espanhol"]
    finally:
        with app_module.state_lock:
            app_module.state.clear()
            app_module.state.update(state_snapshot)


def test_state_persistence_is_durable_and_surfaces_write_failure(tmp_path, monkeypatch):
    """State commits sync both the file and its containing directory."""
    import os
    import tempfile
    import types as _types

    _fake_flask = _types.ModuleType("flask")
    _fake_flask.Flask = MagicMock()
    _fake_flask.Flask.return_value.route = lambda *a, **k: (lambda f: f)
    _fake_flask.Response = MagicMock()
    _fake_flask.jsonify = MagicMock()
    _fake_flask.request = MagicMock()
    _fake_flask.send_file = MagicMock()
    sys.modules.setdefault("flask", _fake_flask)
    os.environ.setdefault("TRANSLATOR_WEB_STATE_DIR", tempfile.mkdtemp(prefix="st-"))
    os.environ.setdefault("ANIME_SUBTITLE_LIBRARY_ROOT", tempfile.mkdtemp(prefix="lib-"))
    os.environ.setdefault("ANIME_LIBRARY_ROOTS", tempfile.mkdtemp(prefix="media-"))

    import app as app_module

    old_dir, old_file = app_module.STATE_DIR, app_module.STATE_FILE
    old_state = dict(app_module.state)
    try:
        app_module.STATE_DIR = tmp_path
        app_module.STATE_FILE = tmp_path / "jobs.json"
        app_module.state["jobs"] = [{"id": "job-1", "status": "COMPLETED"}]
        app_module.state["history"] = []
        app_module.state["audits"] = {}
        fsync_calls = []
        real_fsync = os.fsync
        monkeypatch.setattr(app_module.os, "fsync", lambda fd: fsync_calls.append(fd) or real_fsync(fd))

        app_module._persist_locked()

        assert app_module.STATE_FILE.is_file()
        assert len(fsync_calls) >= 2
        assert json.loads(app_module.STATE_FILE.read_text(encoding="utf-8"))["jobs"][0]["id"] == "job-1"

        monkeypatch.setattr(app_module.os, "fsync", MagicMock(side_effect=OSError("disk full")))
        try:
            app_module._persist_locked()
        except app_module.StatePersistenceError as exc:
            assert "falha ao persistir estado" in str(exc)
        else:
            raise AssertionError("persistence failure must be fail-closed")
    finally:
        app_module.STATE_DIR = old_dir
        app_module.STATE_FILE = old_file
        app_module.state.clear()
        app_module.state.update(old_state)


def test_load_state_marks_inflight_jobs_for_persisted_recovery(tmp_path, monkeypatch):
    """Restart recovery is explicit and never silently resumes an active job."""
    import json as _json
    import os
    import tempfile
    import types as _types

    _fake_flask = _types.ModuleType("flask")
    _fake_flask.Flask = MagicMock()
    _fake_flask.Flask.return_value.route = lambda *a, **k: (lambda f: f)
    _fake_flask.Response = MagicMock()
    _fake_flask.jsonify = MagicMock()
    _fake_flask.request = MagicMock()
    _fake_flask.send_file = MagicMock()
    sys.modules.setdefault("flask", _fake_flask)
    os.environ.setdefault("TRANSLATOR_WEB_STATE_DIR", tempfile.mkdtemp(prefix="st-"))
    os.environ.setdefault("ANIME_SUBTITLE_LIBRARY_ROOT", tempfile.mkdtemp(prefix="lib-"))
    os.environ.setdefault("ANIME_LIBRARY_ROOTS", tempfile.mkdtemp(prefix="media-"))

    import app as app_module

    state_file = tmp_path / "jobs.json"
    state_file.write_text(_json.dumps({"jobs": [{"id": "job-1", "status": "PUBLISHING"}]}), encoding="utf-8")
    old_file = app_module.STATE_FILE
    try:
        app_module.STATE_FILE = state_file
        loaded = app_module._load_state()
        assert loaded["recovery_needed"] is True
        assert loaded["jobs"][0]["status"] == "FAILED"
        assert loaded["jobs"][0]["reason"] == "service_restarted"
    finally:
        app_module.STATE_FILE = old_file
