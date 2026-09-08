import importlib.util
import json
from pathlib import Path
from unittest import mock

import pytest

SRC = Path(__file__).resolve().parents[2] / "src/subtranslate"
spec = importlib.util.spec_from_file_location("transport_providers", SRC / "transport_providers.py")
tp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tp)

CANONICAL = {
    "model": "qwen3.5:9b",
    "messages": [
        {"role": "system", "content": "SYSTEM PROMPT"},
        {"role": "user", "content": "TARGET: [...] GLOSSARY: {} SCHEMA: {...}"},
    ],
    "options": {"num_ctx": 2560, "num_predict": 1024, "temperature": 0.0},
    "format": {
        "type": "object",
        "properties": {
            "translations": {
                "type": "array",
                "minItems": 1,
                "maxItems": 1,
                "items": {
                    "type": "object",
                    "properties": {"id": {"type": "integer"}, "text": {"type": "string"}},
                    "required": ["id", "text"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["translations"],
        "additionalProperties": False,
    },
    "stream": False,
    "think": False,
}


def test_ollama_passthrough_and_extract():
    t = tp.OllamaTransport(model="qwen3.5:9b")
    assert t.endpoint() == "http://127.0.0.1:11434/api/chat"
    request = t.build_request(CANONICAL)
    assert request["model"] == "qwen3.5:9b"
    assert request["stream"] is False
    body = json.dumps({"message": {"content": "{\"translations\": []}"}}).encode()
    assert t.extract_content(body) == "{\"translations\": []}"


def test_ollama_karaoke_plain_text_mode_removes_json_contract_and_thinking():
    t = tp.OllamaTransport(model="qwen3.5:9b")
    request = t.build_request({
        "messages": [{"role": "user", "content": "linha"}],
        "options": {"temperature": 0.0, "num_predict": 1024},
        "format": "json",
        "response_mode": "text",
    })
    assert "format" not in request
    assert "response_mode" not in request
    assert request["think"] is False


def test_ollama_empty_content_is_blocked_with_reason():
    t = tp.OllamaTransport(model="qwen3.5:9b")
    with pytest.raises(tp.TransportBlocked, match="OLLAMA_EMPTY_CONTENT:done_reason=length"):
        t.extract_content(json.dumps({
            "done_reason": "length",
            "message": {"content": "", "thinking": ""},
        }).encode())


def test_openai_compat_wire_format_and_auth():
    t = tp.OpenAICompatTransport(model="llama-3.3-70b-versatile",
                                 base_url="https://api.groq.com/openai/v1",
                                 api_key="gsk_test")
    assert t.endpoint() == "https://api.groq.com/openai/v1/chat/completions"
    headers = t.headers()
    assert headers["Authorization"] == "Bearer gsk_test"
    request = t.build_request(CANONICAL)
    assert request["model"] == "llama-3.3-70b-versatile"
    assert request["temperature"] == 0.0
    assert request["max_tokens"] == 1024
    assert request["messages"][1]["content"].startswith("TARGET:")
    body = json.dumps({"choices": [{"message": {"content": "OK"}}]}).encode()
    assert t.extract_content(body) == "OK"


def test_groq_transport_uses_default_endpoint_auth_and_floor_delay():
    t = tp.transport_from_config(
        {"provider": "groq", "model": "openai/gpt-oss-20b", "api_key": "gsk_test", "delay_between_calls": 0.1},
        CANONICAL,
    )
    assert isinstance(t, tp.GroqTransport)
    assert t.endpoint() == "https://api.groq.com/openai/v1/chat/completions"
    assert t.headers()["Authorization"] == "Bearer gsk_test"
    assert t.delay_between_calls == tp.GROQ_MIN_DELAY_SECONDS
    request = t.build_request(CANONICAL)
    assert request["model"] == "openai/gpt-oss-20b"
    assert request["stream"] is False
    assert t.extract_content(json.dumps({"choices": [{"message": {"content": "OK"}}]}).encode()) == "OK"


def test_groq_catalog_filters_non_translation_models():
    models = tp.groq_models_from_api({"data": [
        {"id": "openai/gpt-oss-20b"},
        {"id": "llama-guard-4-12b"},
        {"id": "whisper-large-v3"},
        {"id": "qwen/qwen3.6-27b"},
    ]})
    ids = [item["id"] for item in models]
    assert "openai/gpt-oss-20b" in ids
    assert "qwen/qwen3.6-27b" in ids
    assert "llama-guard-4-12b" not in ids
    assert "whisper-large-v3" not in ids


def test_nvidia_nim_uses_openai_compat_wire_format_and_default_endpoint():
    t = tp.NvidiaTransport(model="meta/llama-3.1-8b-instruct", api_key="nvapi-test")
    assert t.endpoint() == "https://integrate.api.nvidia.com/v1/chat/completions"
    assert t.headers()["Authorization"] == "Bearer nvapi-test"
    request = t.build_request(CANONICAL)
    assert request["model"] == "meta/llama-3.1-8b-instruct"
    assert request["stream"] is False
    body = json.dumps({"choices": [{"message": {"content": "OK"}}]}).encode()
    assert t.extract_content(body) == "OK"


def test_gemini_wire_format_and_extract():
    t = tp.GeminiTransport(model="gemini-2.0-flash",
                           base_url="https://generativelanguage.googleapis.com/v1beta",
                           api_key="AIza_test")
    assert t.endpoint() == ("https://generativelanguage.googleapis.com/v1beta"
                            "/models/gemini-2.0-flash:generateContent")
    assert t.headers()["x-goog-api-key"] == "AIza_test"
    request = t.build_request(CANONICAL)
    assert request["systemInstruction"]["parts"][0]["text"] == "SYSTEM PROMPT"
    assert request["contents"][0]["parts"][0]["text"].startswith("TARGET:")
    assert request["generationConfig"]["responseMimeType"] == "application/json"
    schema = request["generationConfig"]["responseSchema"]
    assert schema["type"] == "OBJECT"
    assert schema["properties"]["translations"]["items"]["type"] == "OBJECT"
    # Gemini's endpoint is stricter than the canonical JSON-Schema validator
    # used locally.  Cardinality and additional-properties constraints stay in
    # the canonical contract but are intentionally omitted from the wire
    # responseSchema for compatibility with the live generateContent API.
    assert schema["properties"]["translations"]["minItems"] == 1
    assert schema["properties"]["translations"]["maxItems"] == 1
    assert "additionalProperties" not in schema["properties"]["translations"]["items"]
    body = json.dumps({"candidates": [{"content": {"parts": [{"text": "RESPOSTA"}]}}]}).encode()
    assert t.extract_content(body) == "RESPOSTA"


def test_gemini_plain_text_mode_does_not_send_json_schema():
    t = tp.GeminiTransport(model="gemini-3.5-flash-lite", api_key="AIza_test")
    request = t.build_request({**CANONICAL, "response_mode": "text"})
    assert request["generationConfig"]["responseMimeType"] == "text/plain"
    assert "responseSchema" not in request["generationConfig"]


def test_gemini_error_and_block_are_blocked():
    t = tp.GeminiTransport(model="gemini-2.0-flash")
    with pytest.raises(tp.TransportBlocked, match="GEMINI_ERROR"):
        t.extract_content(json.dumps({"error": {"message": "quota"}}).encode())
    with pytest.raises(tp.TransportBlocked, match="GEMINI_NO_CANDIDATES"):
        t.extract_content(json.dumps({"promptFeedback": {"blockReason": "SAFETY"}}).encode())


def test_factory_defaults_to_ollama_and_validates():
    t = tp.transport_from_config(None, CANONICAL)
    assert isinstance(t, tp.OllamaTransport)
    with pytest.raises(tp.TransportBlocked):
        tp.transport_from_config({"provider": "telepathy"}, CANONICAL)
    with pytest.raises(tp.TransportBlocked):
        tp.transport_from_config({"provider": "openai_compat"}, {})  # sem options/messages


def test_factory_builds_nvidia_without_manual_base_url():
    t = tp.transport_from_config(
        {"provider": "nvidia", "model": "meta/llama-3.1-8b-instruct", "api_key": "nvapi-test"},
        CANONICAL,
    )
    assert isinstance(t, tp.NvidiaTransport)
    assert t.base_url == "https://integrate.api.nvidia.com/v1"
    assert t.headers()["Authorization"] == "Bearer nvapi-test"


def test_factory_migrates_retired_gemini_model_before_endpoint_creation():
    t = tp.transport_from_config(
        {"provider": "gemini", "model": "gemini-2.5-flash-lite", "api_key": "AIza_test", "delay_between_calls": 0.5},
        CANONICAL,
    )
    assert isinstance(t, tp.GeminiTransport)
    assert t.model == "gemini-3.5-flash-lite"
    assert t.delay_between_calls == tp.GEMINI_MIN_DELAY_SECONDS


def test_deepseek_transport_enforces_shared_delay_floor():
    t = tp.transport_from_config(
        {"provider": "deepseek", "model": "deepseek-chat", "api_key": "sk-test", "delay_between_calls": 0.1},
        CANONICAL,
    )
    assert isinstance(t, tp.DeepseekTransport)
    assert t.delay_between_calls == tp.DEEPSEEK_MIN_DELAY_SECONDS


def test_deepseek_transport_disables_default_thinking_for_translation():
    t = tp.DeepseekTransport(model="deepseek-v4-flash", api_key="sk-test")
    request = t.build_request(CANONICAL)
    assert request["thinking"] == {"type": "disabled"}


def test_deepseek_transport_honors_explicit_thinking_opt_in():
    t = tp.DeepseekTransport(model="deepseek-v4-flash", api_key="sk-test")
    request = t.build_request({**CANONICAL, "think": True})
    assert request["thinking"] == {"type": "enabled"}


def test_cloud_transport_resolves_api_key_from_environment(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "env-secret")
    t = tp.transport_from_config(
        {"provider": "deepseek", "model": "deepseek-chat"},
        CANONICAL,
    )
    assert t.api_key == "env-secret"


def test_nvidia_rejects_local_model_identifier_before_network():
    with pytest.raises(tp.TransportBlocked, match="NVIDIA_MODEL_AUTHORITY_MISMATCH"):
        tp.transport_from_config(
            {"provider": "nvidia", "model": "qwen3.5:9b", "api_key": "nvapi-test"},
            CANONICAL,
        )


def test_nvidia_model_authority_is_explicit():
    assert tp.nvidia_model_is_supported("meta/llama-3.1-8b-instruct")
    assert not tp.nvidia_model_is_supported("qwen3.5:9b")


def test_api_key_env_resolution():
    env = {"GEMINI_API_KEY": "key-from-env"}
    value = tp.api_key_from_env("gemini", environ_getter=env.get)
    assert value == "key-from-env"
    assert tp.api_key_from_env("gemini", environ_getter={}.get) is None


def test_gemini_catalog_is_stable_and_translation_safe():
    models = tp.gemini_model_catalog()

    assert models[0]["id"] == "gemini-3.5-flash-lite"
    assert all(item["stable"] for item in models)
    assert not any("image" in item["id"] or "audio" in item["id"] for item in models)
    assert tp.migrate_gemini_model("gemini-1.5-flash-001") == "gemini-3.5-flash-lite"
    assert tp.migrate_gemini_model("gemini-2.0-flash-exp") == "gemini-3.5-flash-lite"
    assert tp.migrate_gemini_model("gemini-2.5-flash-lite") == "gemini-3.5-flash-lite"


def test_gemini_api_catalog_filters_non_text_and_marks_known_models():
    models = tp.gemini_models_from_api({
        "models": [
            {"name": "models/gemini-3.5-flash-lite", "displayName": "Flash-Lite", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-2.5-flash-image", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-embedding-001", "supportedGenerationMethods": ["embedContent"]},
            {"name": "models/gemini-3.5-flash", "supportedGenerationMethods": ["countTokens"]},
        ],
    })

    assert [item["id"] for item in models] == ["gemini-3.5-flash-lite"]
    assert models[0]["recommended"] is True


def test_nvidia_api_key_env_resolution():
    env = {"NVIDIA_API_KEY": "nvapi-from-env"}
    assert tp.api_key_from_env("nvidia", environ_getter=env.get) == "nvapi-from-env"
    assert tp.api_key_from_env("nvidia", environ_getter={}.get) is None
