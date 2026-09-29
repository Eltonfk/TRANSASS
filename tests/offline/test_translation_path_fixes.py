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

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "subtranslate"))

import web_retranslation_runner as wrr  # noqa: E402
import anime_subtitle_translator as at  # noqa: E402
import production_v2_2_1_adapter as v221  # noqa: E402
import production_v2_2_5_adapter as v225  # noqa: E402
import pipeline_orchestrator as orchestrator  # noqa: E402
import pipeline_v2_1_3 as frozen_pipeline  # noqa: E402
import v238_base_materializer as base_materializer  # noqa: E402


# ---------------------------------------------------------------------------
# Fix: retranslation runner source language wiring
# ---------------------------------------------------------------------------


def test_legacy_ollama_url_accepts_base_or_chat_endpoint():
    assert at._ollama_endpoint("http://ollama:11434") == "http://ollama:11434/api/chat"
    assert at._ollama_endpoint("http://ollama:11434/api") == "http://ollama:11434/api/chat"
    assert at._ollama_endpoint("http://ollama:11434/api/chat") == "http://ollama:11434/api/chat"


def test_v221_config_allows_external_transport_without_ollama_env(monkeypatch, tmp_path):
    """Gemini/NVIDIA jobs must not fail in the frozen Ollama preflight."""
    monkeypatch.delenv("TRANSLATOR_OLLAMA_URL", raising=False)
    monkeypatch.delenv("TRANSLATOR_OLLAMA_MODEL", raising=False)
    config, _ = v221._config(
        tmp_path / "Series" / "episode.ass",
        {},
        execution_context={
            "transport": object(),
            "model": "gemini-2.5-flash-lite",
        },
    )
    assert config.model == "gemini-2.5-flash-lite"
    assert config.ollama_url == "http://transport-disabled.invalid/api/chat"


def test_v221_config_keeps_legacy_ollama_preflight(monkeypatch, tmp_path):
    """Direct V2.2.1/Ollama callers retain the historical guard."""
    monkeypatch.delenv("TRANSLATOR_OLLAMA_URL", raising=False)
    with pytest.raises(RuntimeError, match="TRANSLATOR_OLLAMA_URL"):
        v221._config(tmp_path / "Series" / "episode.ass", {})


def test_v226_batch_size_honors_gemini_profile(monkeypatch):
    monkeypatch.delenv("TRANSLATOR_OLLAMA_URL", raising=False)
    assert base_materializer._v226_batch_target_size({"provider": "gemini", "model": "gemini-2.5-flash-lite"}) == 16
    assert base_materializer._v226_batch_target_size({"provider": "gemini", "model": "gemini-2.5-flash-lite", "gemini_profile": {"batch_size": 12}}) == 12
    assert base_materializer._v226_batch_target_size({"model": "qwen3.5:9b"}) == 4


def test_v226_batch_size_honors_groq_profile():
    assert base_materializer._v226_batch_target_size({
        "provider": "groq",
        "model": "openai/gpt-oss-20b",
        "groq_profile": {"batch_size": 8},
    }) == 8


def test_v226_batch_size_does_not_infer_groq_from_model_namespace():
    assert base_materializer._v226_batch_target_size({
        "provider": "openai_compat",
        "model": "openai/gpt-oss-20b",
        "groq_profile": {"batch_size": 8},
    }) == 4


def test_groq_rate_limit_backoff_parses_try_again_hint():
    runner = object.__new__(frozen_pipeline.Runner)
    runner.calls = [{"error": "HTTP_STATUS:429:Please try again in 20.9925s."}]
    assert runner._rate_limit_backoff((1, 2), 0) == pytest.approx(20.9925)
    assert orchestrator.groq_operation_limits({"retry_budget": 16}) == (16, 192)


def test_gemini_retry_budget_is_separate_from_physical_call_ceiling():
    assert orchestrator.gemini_operation_limits({"retry_budget": 32}) == (32, 131)
    assert orchestrator.gemini_operation_limits({"retry_budget": 200}) == (200, 200)


def test_qwen_default_physical_budget_is_aligned_with_docker_and_invalid_env_is_safe(monkeypatch):
    monkeypatch.delenv("V238_QWEN_PHYSICAL_MAXIMUM", raising=False)
    assert orchestrator.qwen_operation_limits() == (0, 256)
    assert orchestrator.qwen_operation_limits("not-a-number") == (0, 256)


def test_split_isolation_does_not_consume_retry_budget():
    config = frozen_pipeline.Config("http://local.invalid", retry_budget_calls=1)
    runner = frozen_pipeline.Runner([], {}, config, {})
    runner.client.call = lambda *args, **kwargs: ({}, [], {})

    runner._attempt([], phase="split_isolation", attempt_type="RETRY", logical_batch_id="split")
    assert runner.retry_budget.consumed == 0

    runner._attempt([], phase="retry_local", attempt_type="RETRY", logical_batch_id="retry")
    assert runner.retry_budget.consumed == 1


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


@pytest.mark.parametrize(
    ("provider_name", "model_name", "transport_name", "uses_local_thermal_guard"),
    [
        ("ollama", "qwen2.5:14b", "ollama", True),
        ("deepseek", "deepseek-v4-flash", "deepseek", False),
        # The config is reloaded after the transport object is created; the
        # selected config determines the actual transport built by V3.
        ("deepseek", "deepseek-v4-flash", "ollama", False),
    ],
)
def test_v3_retranslation_runner_builds_stable_operation_identity(
    tmp_path, monkeypatch, provider_name, model_name, transport_name,
    uses_local_thermal_guard, capsys,
):
    """The V3 runner must not depend on entering the retired V2.3.8 branch."""
    import hashlib

    import gpu_thermal_guard
    import pipeline_v3
    import v3_runtime

    source = tmp_path / "source.ass"
    source.write_text("[Script Info]\n", encoding="utf-8")
    args = argparse.Namespace(
        source=source,
        output=tmp_path / "output.pt-BR.ass",
        memory_root=None,
        anime_series_id=9,
        episode_id=295,
        job_id="test-job",
        v3_run_id="stable-replay-id",
        series_title="Shiki",
        episode_title="S01E08",
    )
    config = {
        "pipeline": "v3",
        "primary": {"provider": provider_name, "model": model_name},
        "fallback": None,
    }
    active_transport = SimpleNamespace(name=transport_name, model=model_name)
    fake_response_provider = object()
    fake_transport_call = object()
    captured = {}
    guard_state = {"started": False, "stopped": False, "waits": 0, "cooled": True}

    class FakeThermalGuard:
        def __init__(self, **callbacks):
            guard_state["callbacks"] = callbacks

        def start(self):
            guard_state["started"] = True
            return True

        def wait_for_cooling(self):
            guard_state["waits"] += 1
            return guard_state["cooled"]

        def stop(self):
            guard_state["stopped"] = True

    monkeypatch.setattr(wrr, "load_transport_config", lambda _path: config)
    monkeypatch.setattr(wrr, "default_state_dir", lambda: tmp_path / "state")
    monkeypatch.setattr(gpu_thermal_guard, "GpuThermalGuard", FakeThermalGuard)

    def fake_create_transport(*_args, **kwargs):
        captured["create_transport_kwargs"] = kwargs
        return SimpleNamespace(name=provider_name, model=model_name), fake_response_provider

    monkeypatch.setattr(
        v3_runtime,
        "create_v3_live_transport",
        fake_create_transport,
    )
    monkeypatch.setattr(v3_runtime, "v3_batch_size", lambda *_args: 8)

    def fake_make_transport_call(*_args, **kwargs):
        captured["transport_call_kwargs"] = kwargs
        return fake_transport_call

    def fake_execute(plan_id, semantic_source, output, context):
        captured.update(
            plan_id=plan_id,
            semantic_source=semantic_source,
            output=output,
            context=context,
        )
        return {"status": "COMPLETED", "pipeline": "v3"}

    monkeypatch.setattr(pipeline_v3, "make_v3_transport_call", fake_make_transport_call)
    monkeypatch.setattr(wrr, "execute_pipeline_plan", fake_execute)

    result = wrr._run_pipeline(args, "v3", active_transport, "francês")

    expected_operation_id = hashlib.sha256(
        f"v3:stable-replay-id:{provider_name}:{model_name}".encode("utf-8")
    ).hexdigest()[:32]
    assert result == {"status": "COMPLETED", "pipeline": "v3"}
    assert captured["plan_id"] == "v3"
    assert captured["context"]["transport_call"] is fake_transport_call
    assert captured["context"]["source_language"] == "francês"
    assert captured["context"]["target_batch_size"] == 8
    assert captured["transport_call_kwargs"]["operation_id"] == expected_operation_id
    before_call = captured["create_transport_kwargs"]["before_call"]
    transport_gate = captured["transport_call_kwargs"]["thermal_gate"]
    if uses_local_thermal_guard:
        assert callable(before_call)
        assert before_call() is False
        assert callable(transport_gate)
        assert transport_gate() is False
        assert guard_state["started"] is True
        assert guard_state["stopped"] is True
        assert guard_state["waits"] == 2
        guard_state["cooled"] = False
        with pytest.raises(RuntimeError, match="V3_THERMAL_GUARD_TRIPPED"):
            transport_gate()
        sample = gpu_thermal_guard.ThermalSnapshot(
            available=True,
            temperatures_c={"junction": 64.0},
            fan_rpm=1200.0,
            power_w=95.0,
        )
        config_sample = gpu_thermal_guard.ThermalGuardConfig(interval_s=1.0)
        guard_state["callbacks"]["on_sample"](sample, config_sample)
        emitted = capsys.readouterr().err.strip()
        assert emitted.startswith("V3_THERMAL_SAMPLE ")
        import json

        sample_payload = json.loads(emitted.split(" ", 1)[1])
        assert sample_payload["hottest_c"] == 64.0
        assert sample_payload["fan_rpm"] == 1200.0
        assert sample_payload["power_w"] == 95.0
    else:
        assert before_call is None
        assert transport_gate is None
        assert guard_state["started"] is False


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


def test_track_selection_rejects_signs_only_configured_language(monkeypatch, tmp_path):
    """Do not treat an English Titles/Signs track as a full episode source."""
    streams = [
        _stream(3, "Titles/Signs", default=1),
        _stream(4, "Full"),
    ]
    streams[0]["tags"]["language"] = "eng"
    streams[1]["tags"]["language"] = "jpn"
    monkeypatch.setattr(at, "SOURCE_LANGUAGE", "inglês")
    monkeypatch.setattr(at.subprocess, "run", lambda *a, **k: _ffprobe_result(streams))

    assert at.find_subtitle_stream(tmp_path / "ep01.mkv") is None


def test_content_language_evidence_recognizes_english_text_with_wrong_label():
    evidence = at._content_language_evidence(
        "Ah, a shooting star! Huh? Where? That's a superstition they have in Japan.",
        "inglês",
    )
    assert evidence["language"] == "inglês"
    assert evidence["strong"] is True
    assert evidence["marker_hits"] >= 3


def test_track_selection_overrides_wrong_japanese_label_when_content_is_english(monkeypatch, tmp_path):
    """A jpn-labelled Full track must win over eng Titles/Signs when its text is English."""
    video = tmp_path / "ep01.mkv"
    video.write_bytes(b"placeholder")
    streams = [
        _stream(3, "Titles/Signs", default=1),
        _stream(4, "Full"),
    ]
    streams[0]["tags"]["language"] = "eng"
    streams[1]["tags"]["language"] = "jpn"

    header = (
        "[Script Info]\nScriptType: v4.00+\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, "
        "Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    def run(command, *args, **kwargs):
        if command[0] == "ffprobe":
            return _ffprobe_result(streams)
        stream_index = int(command[command.index("-map") + 1].split(":", 1)[1])
        output = Path(command[-1])
        if stream_index == 3:
            output.write_text(header, encoding="utf-8")
        else:
            output.write_text(
                header
                + "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,Ah, a shooting star!\n"
                + "Dialogue: 0,0:00:03.00,0:00:04.00,Default,,0,0,0,,Huh? Where? Where?\n"
                + "Dialogue: 0,0:00:05.00,0:00:07.00,Default,,0,0,0,,That's a superstition they have in Japan.\n"
                + "Dialogue: 0,0:00:08.00,0:00:10.00,Default,,0,0,0,,Don't worry, everything will be fine.\n",
                encoding="utf-8",
            )
        return _ffprobe_result([])

    monkeypatch.setattr(at, "SOURCE_LANGUAGE", "inglês")
    monkeypatch.setattr(at.subprocess, "run", run)

    idx, lang, ext = at.find_subtitle_stream(video)

    assert (idx, lang, ext) == (4, "jpn", ".ass")


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


def test_track_selection_retries_compatible_ffprobe_probe(monkeypatch, tmp_path):
    """An empty quiet probe is retried with the portable ffprobe query."""
    streams = [_stream(2, "Subtitles")]
    responses = iter([_ffprobe_result([]), _ffprobe_result(streams)])
    monkeypatch.setattr(at, "SOURCE_LANGUAGE", "inglês")
    monkeypatch.setattr(at.subprocess, "run", lambda *a, **k: next(responses))
    idx, lang, ext = at.find_subtitle_stream(tmp_path / "ep01.mkv")
    assert idx == 2
    assert lang == "fre"
    assert ext == ".ass"


def test_track_selection_prefers_substantial_dialogue_content(monkeypatch, tmp_path):
    """Two same-language tracks may have identical metadata; choose the full
    dialogue track instead of a nearly empty companion track."""
    video = tmp_path / "ep01.mkv"
    video.write_bytes(b"placeholder")
    streams = [_stream(2, "English"), _stream(3, "English")]
    for stream in streams:
        stream["tags"]["language"] = "eng"

    def run(command, *args, **kwargs):
        if command[0] == "ffprobe":
            return _ffprobe_result(streams)
        stream_index = int(command[command.index("-map") + 1].split(":", 1)[1])
        output = Path(command[-1])
        header = (
            "[Script Info]\nScriptType: v4.00+\n\n"
            "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, "
            "Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
            "Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        )
        if stream_index == 2:
            output.write_text(header + "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,Hi\n", encoding="utf-8")
        else:
            output.write_text(
                header
                + "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,Hello there\n"
                + "Dialogue: 0,0:00:03.00,0:00:04.00,Default,,0,0,0,,How are you?\n"
                + "Dialogue: 0,0:00:05.00,0:00:06.00,Default,,0,0,0,,I am fine, thank you.\n",
                encoding="utf-8",
            )
        return _ffprobe_result([])

    monkeypatch.setattr(at, "SOURCE_LANGUAGE", "inglês")
    monkeypatch.setattr(at.subprocess, "run", run)

    idx, lang, ext = at.find_subtitle_stream(video)

    assert idx == 3
    assert lang == "eng"
    assert ext == ".ass"


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
    _fake_flask.stream_with_context = lambda iterable: iterable
    sys.modules.setdefault("flask", _fake_flask)

    # app.py materializes its state dir at import time; keep it off /app.
    os.environ.setdefault("TRANSLATOR_WEB_STATE_DIR", tempfile.mkdtemp(prefix="st-"))
    os.environ.setdefault("ANIME_SUBTITLE_LIBRARY_ROOT", tempfile.mkdtemp(prefix="lib-"))
    os.environ.setdefault("ANIME_LIBRARY_ROOTS", tempfile.mkdtemp(prefix="media-"))

    import app as app_module

    captured = {}

    def fake_resolve(library, episode_id, record_id=None, materialize=False,
                     job_id=None, source_language="inglês", refresh_from_media=False):
        captured["source_language"] = source_language
        captured["refresh_from_media"] = refresh_from_media
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
    assert captured["refresh_from_media"] is True

    # Sem seleção explícita: cai no idioma global configurado.
    app_module._retranslation_preflight([85], bulk=False)
    assert captured["source_language"] == app_module._global_source_language()
    assert captured["refresh_from_media"] is True


def test_retranslation_accepts_unregistered_ptbr_sidecar(monkeypatch, tmp_path):
    """A published legacy PT-BR sidecar can be reconciled before retranslation."""
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
    _fake_flask.stream_with_context = lambda iterable: iterable
    sys.modules.setdefault("flask", _fake_flask)
    os.environ.setdefault("TRANSLATOR_WEB_STATE_DIR", tempfile.mkdtemp(prefix="st-"))
    os.environ.setdefault("ANIME_SUBTITLE_LIBRARY_ROOT", tempfile.mkdtemp(prefix="lib-"))
    os.environ.setdefault("ANIME_LIBRARY_ROOTS", tempfile.mkdtemp(prefix="media-"))

    import app as app_module

    target = tmp_path / "Shiki - S01E05.pt-BR.ass"
    target.write_text("[Script Info]\n", encoding="utf-8")
    episode = {"id": 292, "classification": "ANIME", "episode": "05", "media_filename": "Shiki E05.mkv"}
    monkeypatch.setattr(app_module, "_episode_row", lambda eid: episode)
    monkeypatch.setattr(app_module, "_current_validated_record", lambda eid: None)
    monkeypatch.setattr(app_module, "_preferred_library_record", lambda eid: None)
    monkeypatch.setattr(app_module, "_existing_ptbr_sidecar_for_episode", lambda eid: target)
    monkeypatch.setattr(
        app_module, "resolve_episode_source",
        lambda *args, **kwargs: {"available": True, "status": "SOURCE_AVAILABLE_SIDECAR", "record_id": None},
    )

    result = app_module._retranslation_preflight([292], source_languages={292: "francês"})

    assert result["counts"]["eligible"] == 1
    assert result["results"][0]["legacy_target"] is True
    assert result["eligible"][0]["old"] is None
    assert result["eligible"][0]["legacy_target_path"] == str(target)


def test_retranslation_autodetects_single_french_source(monkeypatch, tmp_path):
    """A sole French source is selected when the global default is English."""
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
    _fake_flask.stream_with_context = lambda iterable: iterable
    sys.modules.setdefault("flask", _fake_flask)
    os.environ.setdefault("TRANSLATOR_WEB_STATE_DIR", tempfile.mkdtemp(prefix="st-"))
    os.environ.setdefault("ANIME_SUBTITLE_LIBRARY_ROOT", tempfile.mkdtemp(prefix="lib-"))
    os.environ.setdefault("ANIME_LIBRARY_ROOTS", tempfile.mkdtemp(prefix="media-"))

    import app as app_module

    target = tmp_path / "Shiki - S01E06.pt-BR.ass"
    target.write_text("[Script Info]\n", encoding="utf-8")
    episode = {"id": 293, "classification": "ANIME", "episode": "06", "media_filename": "Shiki E06.mkv"}
    calls = []
    monkeypatch.setattr(app_module, "_episode_row", lambda eid: episode)
    monkeypatch.setattr(app_module, "_current_validated_record", lambda eid: None)
    monkeypatch.setattr(app_module, "_preferred_library_record", lambda eid: None)
    monkeypatch.setattr(app_module, "_existing_ptbr_sidecar_for_episode", lambda eid: target)
    monkeypatch.setattr(app_module, "_auto_source_language_for_episode", lambda eid, configured: "francês")

    def fake_resolve(*args, **kwargs):
        calls.append(kwargs["source_language"])
        return {
            "available": kwargs["source_language"] == "francês",
            "status": "SOURCE_AVAILABLE_SIDECAR" if kwargs["source_language"] == "francês" else "SOURCE_NOT_FOUND",
            "record_id": None,
        }

    monkeypatch.setattr(app_module, "resolve_episode_source", fake_resolve)
    result = app_module._retranslation_preflight([293])

    assert result["counts"]["eligible"] == 1
    assert result["results"][0]["source_language"] == "francês"
    assert calls == ["francês"]


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
    _fake_flask.stream_with_context = lambda iterable: iterable
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


def test_candidate_only_queue_stages_source_without_library_import(tmp_path, monkeypatch):
    """Candidate-only queues need neither source nor previous-target Library IDs."""
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
    _fake_flask.stream_with_context = lambda iterable: iterable
    sys.modules.setdefault("flask", _fake_flask)
    os.environ.setdefault("TRANSLATOR_WEB_STATE_DIR", tempfile.mkdtemp(prefix="st-"))
    os.environ.setdefault("ANIME_SUBTITLE_LIBRARY_ROOT", tempfile.mkdtemp(prefix="lib-"))
    os.environ.setdefault("ANIME_LIBRARY_ROOTS", tempfile.mkdtemp(prefix="media-"))

    import app as app_module

    state_dir = tmp_path / "state"
    monkeypatch.setattr(app_module, "STATE_DIR", state_dir)
    episode = {
        "id": 297, "series_id": 9, "classification": "ANIME", "episode": "10",
        "media_filename": "Shiki E10.mkv", "series_title": "Shiki",
    }
    legacy_target = tmp_path / "Shiki E10.pt-BR.ass"
    legacy_target.write_text("[Script Info]\n", encoding="utf-8")
    preflight = {
        "ok": True, "bulk": False, "force_current": False,
        "pipeline": "v3", "model": "qwen2.5:14b", "total": 1,
        "results": [], "skipped": [], "blocked": [],
        "eligible": [{
            "episode": episode,
            "old": None,
            "legacy_target_path": str(legacy_target),
            "preflight": {"episode_id": 297, "status": "ELIGIBLE", "source_language": "francês"},
        }],
        "counts": {"eligible": 1, "skipped_current_validated": 0, "blocked": 0},
    }
    monkeypatch.setattr(app_module, "_retranslation_preflight", lambda *a, **k: preflight)
    monkeypatch.setattr(app_module, "_episode_row", lambda _episode_id: episode)
    monkeypatch.setattr(app_module, "_existing_ptbr_sidecar_for_episode", lambda _episode_id: legacy_target)
    monkeypatch.setattr(
        app_module,
        "_import_existing_ptbr_record",
        lambda *a, **k: pytest.fail("candidate-only must not import the legacy sidecar"),
    )
    resolved = {}

    def fake_resolve(_library, episode_id, _record_id=None, **kwargs):
        root = Path(kwargs["staging_root"])
        staged_dir = root / f"source-{episode_id}-test"
        staged_dir.mkdir(parents=True)
        source = staged_dir / f"source-{episode_id}.ass"
        source.write_text("[Script Info]\nTitle: French\n", encoding="utf-8")
        resolved.update(kwargs)
        return {
            "available": True, "status": "SOURCE_AVAILABLE_INTERNAL_TEXT",
            "record_id": None, "path": str(source), "staging_path": str(staged_dir),
        }

    monkeypatch.setattr(app_module, "resolve_episode_source", fake_resolve)
    monkeypatch.setattr(app_module, "_persist_locked", lambda: None)
    monkeypatch.setattr(app_module, "_start_worker_locked", MagicMock())

    state_snapshot = dict(app_module.state)
    with app_module.state_lock:
        app_module.state.update({
            "running": False, "jobs": [], "session_id": None,
            "log": app_module.deque(maxlen=app_module.MAX_LOGS), "log_sequence": 0,
        })
    try:
        result = app_module._queue_retranslation(
            [297], source_languages={297: "francês"}, candidate_only=True, ollama_only=True,
        )

        assert result["queued"] == 1
        assert resolved["materialize"] is True
        assert resolved["source_language"] == "francês"
        assert Path(resolved["staging_root"]).is_relative_to(state_dir / "candidate-source-staging")
        job = app_module.state["jobs"][0]
        assert job["candidate_only"] is True
        assert job["source_record_id"] is None
        assert job["old_record_id"] is None
        assert job["published"] is False
        assert job["source_sha256"]
        assert job["source_size_bytes"] > 0

        Path(job["source_abs"]).write_text("tampered", encoding="utf-8")
        with pytest.raises(app_module.LineageContractError, match="candidate_source_hash_mismatch"):
            app_module._validate_candidate_source_integrity(job)
        outside = tmp_path / "must-preserve"
        outside.mkdir()
        app_module._cleanup_candidate_source_staging({
            "candidate_only": True, "source_staging_path": str(outside),
        })
        assert outside.is_dir()
        app_module._cleanup_candidate_source_staging(job)
        assert not Path(job["source_staging_path"]).exists()
    finally:
        with app_module.state_lock:
            app_module.state.clear()
            app_module.state.update(state_snapshot)


def test_candidate_only_worker_completes_without_library_or_publication(tmp_path, monkeypatch):
    """The candidate worker validates staged bytes and returns before archival."""
    import hashlib
    import io
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
    _fake_flask.stream_with_context = lambda iterable: iterable
    sys.modules.setdefault("flask", _fake_flask)
    os.environ.setdefault("TRANSLATOR_WEB_STATE_DIR", tempfile.mkdtemp(prefix="st-"))
    os.environ.setdefault("ANIME_SUBTITLE_LIBRARY_ROOT", tempfile.mkdtemp(prefix="lib-"))
    os.environ.setdefault("ANIME_LIBRARY_ROOTS", tempfile.mkdtemp(prefix="media-"))

    import app as app_module

    state_dir = tmp_path / "state"
    source_dir = state_dir / "candidate-source-staging" / "job-1" / "source-297-unit"
    source_dir.mkdir(parents=True)
    source = source_dir / "source.ass"
    source.write_text("[Script Info]\nTitle: source\n", encoding="utf-8")
    source_bytes = source.read_bytes()
    job = {
        "id": "job-1", "session_id": "session-1", "operation": "RETRANSLATE",
        "episode_id": 297, "series_id": 9, "episode": "10", "name": "Shiki E10.mkv",
        "source_abs": str(source), "source_staging_path": str(source_dir),
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "source_size_bytes": len(source_bytes), "source_record_id": None,
        "old_record_id": None, "source_language": "francês", "candidate_only": True,
        "ollama_only": True,
    }
    monkeypatch.setattr(app_module, "STATE_DIR", state_dir)
    monkeypatch.setattr(app_module, "_episode_row", lambda _episode_id: {
        "id": 297, "series_id": 9, "classification": "ANIME",
    })
    monkeypatch.setattr(app_module, "_effective_pipeline", lambda: "v3")
    monkeypatch.setattr(app_module, "_validate_retranslation_job_integrity", lambda _job: pytest.fail("candidate must not require Library record IDs"))
    monkeypatch.setattr(app_module, "audit_record", lambda *_a, **_k: {
        "status": "VALID", "flags": [], "blocking_flags": [], "review_flags": [],
        "eligible_for_archive": True, "output_events": 1,
    })
    monkeypatch.setattr(app_module, "archive_eligibility", lambda _audit: {
        "eligible_for_archive": True, "blocking_flags": [], "review_flags": [],
    })

    output_bytes = b"[Script Info]\nTitle: translated\n"

    class FakeProcess:
        def __init__(self, command, **_kwargs):
            self.command = command
            self.stdout = io.StringIO("")
            Path(command[command.index("--output") + 1]).write_bytes(output_bytes)

        def wait(self):
            return 0

    monkeypatch.setattr(app_module.subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(app_module, "_persist_locked", lambda: None)
    monkeypatch.setattr(app_module, "_append_log", lambda *a, **k: None)
    monkeypatch.setattr(app_module.subtitle_library, "ingest_file", MagicMock(side_effect=AssertionError("Library ingest")))
    monkeypatch.setattr(app_module.subtitle_library, "publish", MagicMock(side_effect=AssertionError("Library publish")))

    state_snapshot = dict(app_module.state)
    with app_module.state_lock:
        app_module.state.update({
            "process": None, "stop_requested": False, "cancel_requested": False,
            "thermal_stop_requested": False, "log": app_module.deque(maxlen=app_module.MAX_LOGS),
        })
    try:
        app_module._run_retranslation_episode(job)

        assert job["status"] == "COMPLETED"
        assert job["stage"] == "CANDIDATE_READY"
        assert job["published"] is False
        assert job["library_record_created"] is False
        assert job["source_record_id"] is None
        assert job["old_record_id"] is None
        assert app_module.subtitle_library.ingest_file.call_count == 0
        assert app_module.subtitle_library.publish.call_count == 0
        candidate = state_dir / "staging" / "retranslation-job-1" / job["candidate_output_name"]
        assert candidate.read_bytes() == output_bytes
        assert not source_dir.exists()
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
    _fake_flask.stream_with_context = lambda iterable: iterable
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
    _fake_flask.stream_with_context = lambda iterable: iterable
    sys.modules.setdefault("flask", _fake_flask)
    os.environ.setdefault("TRANSLATOR_WEB_STATE_DIR", tempfile.mkdtemp(prefix="st-"))
    os.environ.setdefault("ANIME_SUBTITLE_LIBRARY_ROOT", tempfile.mkdtemp(prefix="lib-"))
    os.environ.setdefault("ANIME_LIBRARY_ROOTS", tempfile.mkdtemp(prefix="media-"))

    import app as app_module

    state_file = tmp_path / "jobs.json"
    state_file.write_text(_json.dumps({"jobs": [
        {"id": "job-1", "status": "PUBLISHING"},
        {"id": "job-2", "status": "WAITING"},
    ]}), encoding="utf-8")
    old_file = app_module.STATE_FILE
    try:
        app_module.STATE_FILE = state_file
        loaded = app_module._load_state()
        assert loaded["recovery_needed"] is True
        assert all(job["status"] == "FAILED" for job in loaded["jobs"])
        assert all(job["reason"] == "service_restarted" for job in loaded["jobs"])
        assert all("retomada automática" in job["error"] for job in loaded["jobs"])
    finally:
        app_module.STATE_FILE = old_file
