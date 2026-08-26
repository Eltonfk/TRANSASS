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
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "subtranslate"))

import web_retranslation_runner as wrr  # noqa: E402
import anime_subtitle_translator as at  # noqa: E402
import production_v2_2_5_adapter as v225  # noqa: E402


# ---------------------------------------------------------------------------
# Fix: retranslation runner source language wiring
# ---------------------------------------------------------------------------


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
