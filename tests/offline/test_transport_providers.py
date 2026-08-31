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
    "options": {"num_ctx": 4096, "num_predict": 1024, "temperature": 0.0},
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
    body = json.dumps({"candidates": [{"content": {"parts": [{"text": "RESPOSTA"}]}}]}).encode()
    assert t.extract_content(body) == "RESPOSTA"


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


def test_nvidia_rejects_local_model_identifier_before_network():
    with pytest.raises(tp.TransportBlocked, match="NVIDIA_MODEL_AUTHORITY_MISMATCH"):
        tp.transport_from_config(
            {"provider": "nvidia", "model": "qwen3.5:9b", "api_key": "nvapi-test"},
            CANONICAL,
        )


def test_api_key_env_resolution():
    env = {"GEMINI_API_KEY": "key-from-env"}
    value = tp.api_key_from_env("gemini", environ_getter=env.get)
    assert value == "key-from-env"
    assert tp.api_key_from_env("gemini", environ_getter={}.get) is None


def test_nvidia_api_key_env_resolution():
    env = {"NVIDIA_API_KEY": "nvapi-from-env"}
    assert tp.api_key_from_env("nvidia", environ_getter=env.get) == "nvapi-from-env"
    assert tp.api_key_from_env("nvidia", environ_getter={}.get) is None
