"""Offline tests for the V2.3.8 web integration contracts C1, C2, C4.

Deterministic: no model calls, no HTTP, no Library writes.  Uses fake
transports and in-memory configs only.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "subtranslate"))

import web_execution_context as c1  # noqa: E402
import web_durable_provider as c2  # noqa: E402
import transport_config_store as c4  # noqa: E402


# ---------------------------------------------------------------------------
# C1: Execution Context Factory
# ---------------------------------------------------------------------------


def test_c1_builds_required_fields():
    ctx = c1.build_v238_execution_context(
        job={"id": "job-1", "episode_id": 79, "anime_series_id": 3},
        transport_config={"primary": {"provider": "gemini", "model": "gemini-3.6-flash"}},
        source_language="francês",
        operation_id="op-1",
        execution_mode="LIVE_CAPTURED",
        capture_root=Path("/tmp/captures"),
        authorized_primary_models=["qwen", "gemini"],
    )
    assert ctx["operation_id"] == "op-1"
    assert ctx["execution_mode"] == "LIVE_CAPTURED"
    assert ctx["source_language"] == "francês"
    assert ctx["model"] == "gemini-3.6-flash"
    assert ctx["episode_id"] == 79
    assert ctx["anime_series_id"] == 3
    assert ctx["authorized_primary_models"] == ["qwen", "gemini"]
    assert ctx["capture_root"] == Path("/tmp/captures")


def test_c1_default_authorized_models_is_qwen_only():
    ctx = c1.build_v238_execution_context(
        job={"id": "job-1"},
        transport_config={"primary": {"provider": "ollama", "model": "qwen3.5:9b"}},
        source_language="inglês",
        operation_id="op-1",
    )
    assert ctx["authorized_primary_models"] == ["qwen"]


def test_c1_does_not_create_budget_or_materializer():
    """C1 delega budget/materializer ao orchestrator/adapter (contrato)."""
    ctx = c1.build_v238_execution_context(
        job={"id": "job-1"},
        transport_config={"primary": {"provider": "ollama", "model": "qwen3.5:9b"}},
        source_language="inglês",
        operation_id="op-1",
    )
    assert "operation_budget" not in ctx
    assert "base_materializer" not in ctx


# ---------------------------------------------------------------------------
# C2: WebDurableResponseProvider
# ---------------------------------------------------------------------------


def test_c2_subclasses_concrete_provider():
    from v238_response_provider import DurableResponseProvider

    provider = c2.WebDurableResponseProvider(
        {"primary": {"provider": "ollama", "model": "qwen3.5:9b"}},
        mode="TEST_FAKE",
        capture_root=Path("/tmp/captures"),
    )
    assert isinstance(provider, DurableResponseProvider)


def test_c2_project_request_chat_shape():
    provider = c2.WebDurableResponseProvider(
        {"primary": {"provider": "ollama", "model": "qwen3.5:9b"}},
        mode="TEST_FAKE",
        capture_root=Path("/tmp/captures"),
    )
    chat = provider._project_request({"text": "Olá mundo", "event_id": 1})
    assert chat["messages"][-1] == {"role": "user", "content": "Olá mundo"}
    assert chat["messages"][0]["role"] == "system"
    assert "português do Brasil" in chat["messages"][0]["content"]
    assert chat["options"]["temperature"] == 0.0
    assert chat["format"] == "json"


def test_c2_inject_api_key():
    provider = c2.WebDurableResponseProvider(
        {"primary": {"provider": "gemini", "model": "gemini-3.6-flash"},
         "keys": {"gemini": "secret-key"}},
        mode="TEST_FAKE",
        capture_root=Path("/tmp/captures"),
    )
    section = provider._inject_api_key({"provider": "gemini", "model": "gemini-3.6-flash"})
    assert section["api_key"] == "secret-key"


def test_c2_select_section_primary():
    provider = c2.WebDurableResponseProvider(
        {"primary": {"provider": "gemini", "model": "gemini-3.6-flash"},
         "fallback": {"provider": "ollama", "model": "qwen3.5:9b"}},
        mode="TEST_FAKE",
        capture_root=Path("/tmp/captures"),
    )
    section = provider._select_section({})
    assert section["provider"] == "gemini"


def test_c2_decode_model_content_json():
    parsed = c2._decode_model_content('{"translation": "Olá"}')
    assert parsed["translation"] == "Olá"


def test_c2_decode_model_content_plain():
    parsed = c2._decode_model_content("Olá mundo")
    assert parsed["text"] == "Olá mundo"


def test_c2_transport_semantics_by_provider():
    gemini = c2.WebDurableResponseProvider(
        {"primary": {"provider": "gemini", "model": "gemini-3.6-flash"}},
        mode="TEST_FAKE", capture_root=Path("/tmp/captures"),
    )
    ollama = c2.WebDurableResponseProvider(
        {"primary": {"provider": "ollama", "model": "qwen3.5:9b"}},
        mode="TEST_FAKE", capture_root=Path("/tmp/captures"),
    )
    assert gemini.transport_semantics == "NETWORK_NON_MODEL"
    assert ollama.transport_semantics == "OLLAMA_MODEL"


# ---------------------------------------------------------------------------
# M10: TestFixtureMaterializer
# ---------------------------------------------------------------------------


def test_m10_fixture_materializer_source_preserving():
    import tempfile

    import pysubs2

    with tempfile.TemporaryDirectory(prefix="m10-") as raw:
        root = Path(raw)
        source = root / "source.ass"
        source.write_text(
            "[Script Info]\nScriptType: v4.00+\n\n"
            "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
            "Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,10,10,10,1\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
            "Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,Olá mundo\n",
            encoding="utf-8",
        )
        output = root / "base.ass"
        materializer = c1.TestFixtureMaterializer()
        result = materializer.materialize(source, output, context={})
        assert result["mode"] == "TEST_FIXTURE"
        assert output.is_file()
        parsed = pysubs2.load(str(output), format="ass")
        assert len(parsed.events) == 1
        assert result["primary_ledger"][0]["status"] == "RESOLVED"


def test_m10_build_context_injects_fixture_for_test_fake():
    ctx = c1.build_v238_execution_context(
        job={"id": "job-1"},
        transport_config={"primary": {"provider": "ollama", "model": "qwen3.5:9b"}},
        source_language="inglês",
        operation_id="op-1",
        execution_mode="TEST_FAKE",
    )
    assert ctx["base_materializer"] is not None
    assert ctx["base_materializer"].mode == "TEST_FIXTURE"


def test_m10_build_context_does_not_inject_for_live():
    ctx = c1.build_v238_execution_context(
        job={"id": "job-1"},
        transport_config={"primary": {"provider": "ollama", "model": "qwen3.5:9b"}},
        source_language="inglês",
        operation_id="op-1",
        execution_mode="LIVE_CAPTURED",
    )
    assert "base_materializer" not in ctx


# ---------------------------------------------------------------------------
# C4: Pipeline Selection (M5)
# ---------------------------------------------------------------------------


def test_c4_default_pipeline_is_v238():
    assert c4.DEFAULT_PIPELINE == "v2_3_8"
    assert c4.ALLOWED_PIPELINES == {"legacy", "v2_3_0", "v2_3_8"}


def test_c4_generates_model_digest_when_missing():
    with tempfile.TemporaryDirectory(prefix="c4-digest-") as raw:
        path = Path(raw) / "transport_config.json"
        saved = c4.save_transport_config(path, {
            "primary": {"provider": "nvidia", "model": "nvidia/llama-3.1-8b-instruct"},
            "keys": {"nvidia": "nvapi-test"},
        })
        assert saved["model_digest"]
        assert saved["primary_model_digest"] == saved["model_digest"]
        assert c4.load_transport_config(path)["model_digest"] == saved["model_digest"]


def test_c4_save_and_load_pipeline():
    with tempfile.TemporaryDirectory(prefix="c4-") as raw:
        path = Path(raw) / "transport_config.json"
        saved = c4.save_transport_config(path, {
            "primary": {"provider": "ollama", "model": "qwen3.5:9b"},
            "pipeline": "v2_3_8",
            "authorized_primary_models": ["qwen", "gemini"],
            "model_digest": "abc123",
        })
        assert saved["pipeline"] == "v2_3_8"
        assert saved["authorized_primary_models"] == ["qwen", "gemini"]
        assert saved["model_digest"] == "abc123"
        loaded = c4.load_transport_config(path)
        assert loaded["pipeline"] == "v2_3_8"
        assert loaded["model_digest"] == "abc123"


def test_c4_rejects_invalid_pipeline():
    with tempfile.TemporaryDirectory(prefix="c4-") as raw:
        path = Path(raw) / "transport_config.json"
        try:
            c4.save_transport_config(path, {
                "primary": {"provider": "ollama", "model": "qwen3.5:9b"},
                "pipeline": "v9_9_9",
            })
            raise AssertionError("expected TransportConfigError")
        except c4.TransportConfigError:
            pass


def test_c4_public_exposes_pipeline():
    with tempfile.TemporaryDirectory(prefix="c4-") as raw:
        path = Path(raw) / "transport_config.json"
        c4.save_transport_config(path, {
            "primary": {"provider": "ollama", "model": "qwen3.5:9b"},
            "pipeline": "v2_3_0",
        })
        public = c4.public_transport_config(path)
        assert public["pipeline"] == "v2_3_0"
        assert "authorized_primary_models" in public
