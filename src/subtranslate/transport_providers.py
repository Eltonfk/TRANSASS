#!/usr/bin/env python3
"""Pluggable transport providers for the V238 subtitle translation pipeline.

A transport provider converts the CANONICAL chat-style translation payload
(the deterministic planner output in Ollama-chat shape) into a specific API's
wire format, performs EXACTLY ONE HTTP POST, and extracts the assistant text
from the response.  The durable evidence layer (DurableV226Call) stays
provider-agnostic: it records whatever bytes were actually sent/received.

Supported providers:
  ollama        local Ollama /api/chat (canonical passthrough)
  openai_compat any OpenAI-compatible endpoint (Groq, OpenRouter, LM Studio,
                vLLM, llama.cpp server, Together, ...)
  gemini        Google Generative Language API (Gemini 2.x Flash free tier)

API keys are read from the environment by default and are NEVER stored in
config files.
"""

from __future__ import annotations

import json
from typing import Any


class TransportBlocked(RuntimeError):
    """Raised for provider-side errors (HTTP errors, refusals, empty output)."""


class BaseTransport:
    name = "base"

    def __init__(self, *, model: str, base_url: str | None = None,
                 api_key: str | None = None) -> None:
        self.model = model
        self.base_url = (base_url or self.default_base_url()).rstrip("/")
        self.api_key = api_key

    @staticmethod
    def default_base_url() -> str:
        raise NotImplementedError

    def endpoint(self) -> str:
        raise NotImplementedError

    def headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json"}

    def build_request(self, canonical_payload: dict[str, Any]) -> dict[str, Any]:
        """Convert the canonical Ollama-chat payload into wire format."""
        raise NotImplementedError

    def extract_content(self, body: bytes) -> str:
        """Extract assistant text from a successful response body."""
        raise NotImplementedError


def _messages_of(canonical_payload: dict[str, Any]) -> list[dict[str, str]]:
    messages = canonical_payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise TransportBlocked("CANONICAL_PAYLOAD_MESSAGES_MISSING")
    out = []
    for message in messages:
        role = str(message.get("role", "user"))
        text = message.get("content")
        if not isinstance(text, str):
            raise TransportBlocked("CANONICAL_PAYLOAD_MESSAGE_CONTENT_INVALID")
        out.append({"role": role, "content": text})
    return out


def _options_of(canonical_payload: dict[str, Any]) -> dict[str, Any]:
    options = canonical_payload.get("options")
    if not isinstance(options, dict):
        raise TransportBlocked("CANONICAL_PAYLOAD_OPTIONS_MISSING")
    return options


class OllamaTransport(BaseTransport):
    """Local Ollama /api/chat — the canonical payload passes through as-is."""

    name = "ollama"

    @staticmethod
    def default_base_url() -> str:
        return "http://127.0.0.1:11434"

    def endpoint(self) -> str:
        return f"{self.base_url}/api/chat"

    def build_request(self, canonical_payload: dict[str, Any]) -> dict[str, Any]:
        request = dict(canonical_payload)
        if self.model:
            request["model"] = self.model
        request["stream"] = False
        return request

    def extract_content(self, body: bytes) -> str:
        envelope = json.loads(body.decode("utf-8"))
        if envelope.get("error"):
            raise TransportBlocked(f"OLLAMA_ERROR:{envelope['error']}")
        message = envelope.get("message") or {}
        content = message.get("content")
        if not isinstance(content, str):
            raise TransportBlocked("OLLAMA_RESPONSE_CONTENT_MISSING")
        return content


class OpenAICompatTransport(BaseTransport):
    """Any OpenAI-compatible /chat/completions endpoint."""

    name = "openai_compat"

    @staticmethod
    def default_base_url() -> str:
        raise TransportBlocked("OPENAI_COMPAT_BASE_URL_REQUIRED")

    def headers(self) -> dict[str, str]:
        headers = super().headers()
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def endpoint(self) -> str:
        return f"{self.base_url}/chat/completions"

    def build_request(self, canonical_payload: dict[str, Any]) -> dict[str, Any]:
        options = _options_of(canonical_payload)
        return {
            "model": self.model,
            "messages": _messages_of(canonical_payload),
            "temperature": float(options.get("temperature", 0.0)),
            "max_tokens": int(options.get("num_predict", 1024)),
            "stream": False,
        }

    def extract_content(self, body: bytes) -> str:
        envelope = json.loads(body.decode("utf-8"))
        if envelope.get("error"):
            detail = envelope["error"]
            detail = detail.get("message", "") if isinstance(detail, dict) else str(detail)
            raise TransportBlocked(f"OPENAI_COMPAT_ERROR:{detail[:300]}")
        choices = envelope.get("choices") or []
        if not choices:
            raise TransportBlocked("OPENAI_COMPAT_EMPTY_CHOICES")
        content = (choices[0].get("message") or {}).get("content")
        if not isinstance(content, str):
            raise TransportBlocked("OPENAI_COMPAT_RESPONSE_CONTENT_MISSING")
        return content


class NvidiaTransport(OpenAICompatTransport):
    """NVIDIA NIM API (build.nvidia.com) — OpenAI-compatible /chat/completions.

    Offers several free models (e.g. ``meta/llama-3.1-8b-instruct``,
    ``nvidia/llama-3.1-nemotron-70b-instruct``).  Reuses the OpenAI-compatible
    wire format with a default base URL so the user only needs the API key.
    """

    name = "nvidia"

    @staticmethod
    def default_base_url() -> str:
        return "https://integrate.api.nvidia.com/v1"


class GeminiTransport(BaseTransport):
    """Google Generative Language API (generateContent)."""

    name = "gemini"

    @staticmethod
    def default_base_url() -> str:
        return "https://generativelanguage.googleapis.com/v1beta"

    def headers(self) -> dict[str, str]:
        headers = super().headers()
        if self.api_key:
            headers["x-goog-api-key"] = self.api_key
        return headers

    def endpoint(self) -> str:
        return f"{self.base_url}/models/{self.model}:generateContent"

    def build_request(self, canonical_payload: dict[str, Any]) -> dict[str, Any]:
        options = _options_of(canonical_payload)
        messages = _messages_of(canonical_payload)
        system_texts = [m["content"] for m in messages if m["role"] == "system"]
        user_messages = [m for m in messages if m["role"] != "system"]
        request: dict[str, Any] = {
            "contents": [{"role": m["role"], "parts": [{"text": m["content"]}]}
                         for m in user_messages],
            "generationConfig": {
                "temperature": float(options.get("temperature", 0.0)),
                # Gemini truncates at maxOutputTokens; the canonical 1024 is
                # too small for 8-event batches with long translations.
                "maxOutputTokens": max(int(options.get("num_predict", 1024)), 8192),
                "responseMimeType": "application/json",
            },
        }
        if system_texts:
            request["systemInstruction"] = {"parts": [{"text": "\n".join(system_texts)}]}
        return request

    def extract_content(self, body: bytes) -> str:
        envelope = json.loads(body.decode("utf-8"))
        if envelope.get("error"):
            detail = envelope["error"]
            detail = detail.get("message", "") if isinstance(detail, dict) else str(detail)
            raise TransportBlocked(f"GEMINI_ERROR:{detail[:300]}")
        candidates = envelope.get("candidates") or []
        if not candidates:
            feedback = envelope.get("promptFeedback") or {}
            raise TransportBlocked(f"GEMINI_NO_CANDIDATES:{feedback.get('blockReason', 'unknown')}")
        parts = ((candidates[0].get("content") or {}).get("parts")) or []
        texts = [p.get("text", "") for p in parts if isinstance(p, dict)]
        content = "".join(texts)
        if not content.strip():
            raise TransportBlocked("GEMINI_RESPONSE_CONTENT_MISSING")
        return content


_PROVIDERS = {
    "ollama": OllamaTransport,
    "openai_compat": OpenAICompatTransport,
    "gemini": GeminiTransport,
    "nvidia": NvidiaTransport,
}


def transport_from_config(transport_config: dict[str, Any] | None,
                          canonical_payload: dict[str, Any]) -> BaseTransport:
    """Build a transport from an optional ``transport`` section of the episode
    config.  Missing section => local Ollama with the canonical model."""
    tc = dict(transport_config or {})
    name = str(tc.get("provider", "ollama")).lower()
    cls = _PROVIDERS.get(name)
    if cls is None:
        raise TransportBlocked(f"TRANSPORT_PROVIDER_UNKNOWN:{name}")
    canonical_model = canonical_payload.get("model")
    model = tc.get("model") or canonical_model
    if not model:
        raise TransportBlocked("TRANSPORT_MODEL_MISSING")
    api_key = tc.get("api_key") or tc.get("api_key_env_placeholder")
    return cls(model=str(model), base_url=tc.get("base_url"), api_key=api_key)


def api_key_from_env(provider_name: str, environ_getter=None) -> str | None:
    import os

    getter = environ_getter or os.environ.get
    env_names = {
        "openai_compat": ("TRANSPORT_API_KEY", "OPENAI_API_KEY", "GROQ_API_KEY",
                          "OPENROUTER_API_KEY"),
        "gemini": ("TRANSPORT_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"),
        "nvidia": ("TRANSPORT_API_KEY", "NVIDIA_API_KEY", "NVIDIA_NIM_API_KEY"),
        "ollama": ("TRANSPORT_API_KEY",),
    }
    for env_name in env_names.get(provider_name, ("TRANSPORT_API_KEY",)):
        value = getter(env_name)
        if value:
            return value
    return None
