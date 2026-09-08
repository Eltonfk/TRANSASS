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


# Google applies RPM/TPM limits per project and the exact ceiling varies by
# account tier. Four seconds is a conservative floor for the free/API-Studio
# profile (15 requests/minute); paid users can still choose a larger delay.
GEMINI_MIN_DELAY_SECONDS = 4.0
# Groq's free limits are model/account dependent. A conservative floor keeps
# bursts below the common free-plan RPM ceiling while retaining its latency
# advantage over slower hosted providers.
GROQ_MIN_DELAY_SECONDS = 2.5
# DeepSeek is also rate-limited by account/model. Keep a small floor in the
# shared transport boundary so direct clients and durable web clients agree.
DEEPSEEK_MIN_DELAY_SECONDS = 2.0
# These providers are network services whose credentials must be explicit.
# ``openai_compat`` is intentionally absent because it may point to a local
# LM Studio/vLLM/llama.cpp endpoint.
API_KEY_PROVIDERS = frozenset({"gemini", "groq", "nvidia", "deepseek"})


def _gemini_schema(value: Any) -> Any:
    """Project the canonical JSON schema to Gemini's responseSchema subset.

    Ollama's schema uses lower-case JSON-Schema names and includes
    JSON-Schema names. Gemini's structured output contract accepts the same
    shape semantically, but expects enum type names and only a subset of JSON
    Schema keywords. Keep the portable structural fields; batch cardinality
    remains enforced by the canonical validator after the response returns.
    """
    if isinstance(value, list):
        return [_gemini_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    projected: dict[str, Any] = {}
    if "type" in value:
        projected["type"] = str(value["type"]).upper()
    for key in (
        "format", "title", "description", "nullable", "enum", "required",
        "propertyOrdering", "minItems", "maxItems",
    ):
        if key in value:
            projected[key] = _gemini_schema(value[key])
    if "properties" in value and isinstance(value["properties"], dict):
        projected["properties"] = {
            str(name): _gemini_schema(schema)
            for name, schema in value["properties"].items()
        }
    if "items" in value:
        projected["items"] = _gemini_schema(value["items"])
    return projected


# Stable text-generation models that are appropriate for subtitle translation.
# The online catalogue may contain audio, image, TTS, embedding and preview
# models that cannot satisfy the Transass structured text contract; these are
# the safe offline fallback shown before an API key is available.  The 2.5
# family is retained only through migration for older configurations.
GEMINI_TRANSLATION_MODEL_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "id": "gemini-3.5-flash-lite",
        "label": "Gemini 3.5 Flash-Lite · recomendado",
        "description": "Mais econômico e rápido; melhor ponto de partida para temporadas.",
        "stable": True,
        "recommended": True,
    },
    {
        "id": "gemini-3.5-flash",
        "label": "Gemini 3.5 Flash · equilibrado",
        "description": "Mais qualidade de raciocínio, com custo e latência moderados.",
        "stable": True,
        "recommended": True,
    },
    {
        "id": "gemini-3.1-pro",
        "label": "Gemini 3.1 Pro · maior qualidade",
        "description": "Indicado para casos difíceis; mais lento e geralmente mais caro.",
        "stable": True,
        "recommended": False,
    },
)

_GEMINI_MODEL_CATALOG_BY_ID = {
    item["id"]: item for item in GEMINI_TRANSLATION_MODEL_CATALOG
}
GEMINI_MODEL_MIGRATIONS = {
    # Models retired by Google are migrated to stable text-generation models
    # before a request is made.  The user's configured model remains visible
    # as the replacement in the UI and is persisted on the next save.
    "gemini-1.5-flash": "gemini-3.5-flash-lite",
    "gemini-1.5-flash-8b": "gemini-3.5-flash-lite",
    "gemini-1.5-pro": "gemini-3.5-flash",
    "gemini-2.0-flash": "gemini-3.5-flash-lite",
    "gemini-2.0-flash-001": "gemini-3.5-flash-lite",
    "gemini-2.0-flash-lite": "gemini-3.5-flash-lite",
    "gemini-2.0-flash-lite-001": "gemini-3.5-flash-lite",
    "gemini-2.5-flash-lite": "gemini-3.5-flash-lite",
    "gemini-2.5-flash-lite-preview-09-2025": "gemini-3.5-flash-lite",
    "gemini-2.5-flash": "gemini-3.5-flash",
    "gemini-2.5-pro": "gemini-3.1-pro",
}
_GEMINI_MODEL_BLOCKLIST = (
    "audio", "embedding", "image", "live", "tts", "transcribe", "veo", "imagen",
)


def gemini_model_catalog() -> list[dict[str, Any]]:
    """Return a copy of the safe stable fallback catalogue."""
    return [dict(item) for item in GEMINI_TRANSLATION_MODEL_CATALOG]


def migrate_gemini_model(model: str) -> str:
    """Return a stable replacement for a known retired Gemini model."""
    value = str(model or "").strip()
    lowered = value.casefold()
    direct = GEMINI_MODEL_MIGRATIONS.get(lowered)
    if direct:
        return direct
    # Versioned aliases (``-001``, ``-latest`` and experimental suffixes)
    # were also used by older configs.  Keep the migration deterministic while
    # leaving newer families to the live catalogue returned by Google.
    if lowered.startswith("gemini-1.5-flash") or lowered.startswith("gemini-2.0-flash"):
        return "gemini-3.5-flash-lite"
    if lowered.startswith("gemini-1.5-pro"):
        return "gemini-3.5-flash"
    if lowered.startswith("gemini-2.5-flash-lite-preview"):
        return "gemini-3.5-flash-lite"
    if lowered.startswith("gemini-2.5-flash-lite"):
        return "gemini-3.5-flash-lite"
    if lowered.startswith("gemini-2.5-flash"):
        return "gemini-3.5-flash"
    if lowered.startswith("gemini-2.5-pro"):
        return "gemini-3.1-pro"
    return value


def gemini_models_from_api(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract text-generation models from ``GET /v1beta/models``.

    Google returns several model families in this endpoint.  Only models that
    advertise ``generateContent`` and are not specialized audio/image/TTS
    variants can be used by the Transass Gemini transport.
    """
    rows = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    models: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        methods = row.get("supportedGenerationMethods")
        if isinstance(methods, list) and "generateContent" not in methods:
            continue
        raw_id = str(row.get("baseModelId") or row.get("name") or "").strip()
        model_id = raw_id.removeprefix("models/").strip()
        lowered = model_id.lower()
        if (
            not model_id.startswith("gemini-")
            or any(token in lowered for token in _GEMINI_MODEL_BLOCKLIST)
            or "preview" in lowered
            or "experimental" in lowered
        ):
            continue
        if model_id in seen:
            continue
        seen.add(model_id)
        known = _GEMINI_MODEL_CATALOG_BY_ID.get(model_id, {})
        display = str(row.get("displayName") or "").strip() or model_id
        description = str(row.get("description") or "").strip()
        models.append({
            "id": model_id,
            "label": known.get("label") or display,
            "description": description or known.get("description") or "Modelo Gemini com generateContent.",
            "stable": bool(known.get("stable", "preview" not in lowered)),
            "recommended": bool(known.get("recommended", False)),
        })
    models.sort(key=lambda item: (not item["recommended"], not item["stable"], item["id"]))
    return models


class TransportBlocked(RuntimeError):
    """Raised for provider-side errors (HTTP errors, refusals, empty output)."""


class BaseTransport:
    name = "base"

    def __init__(self, *, model: str, base_url: str | None = None,
                 api_key: str | None = None,
                 delay_between_calls: float = 0.0) -> None:
        self.model = model
        self.base_url = (base_url or self.default_base_url()).rstrip("/")
        self.api_key = api_key
        # Hosted-provider profiles set this value. Keeping it on the
        # transport makes the V226 direct-client path obey the same limiter
        # as the durable V238 provider without affecting local Ollama or
        # generic OpenAI-compatible/NVIDIA transports.
        self.delay_between_calls = max(0.0, float(delay_between_calls or 0.0))

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

    @staticmethod
    def error_detail(body: bytes) -> str:
        """Return a bounded provider error without exposing request secrets."""
        try:
            value = json.loads(bytes(body).decode("utf-8", errors="replace"))
        except (TypeError, ValueError, UnicodeError):
            return bytes(body).decode("utf-8", errors="replace")[:400]
        if isinstance(value, dict):
            error = value.get("error")
            if isinstance(error, dict):
                message = error.get("message") or error.get("status") or error.get("code")
                if message:
                    return str(message)[:400]
        return json.dumps(value, ensure_ascii=False)[:400]


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
        # ``response_mode`` is an internal canonical hint.  Ollama accepts a
        # JSON ``format`` but has no portable text-mode field, so remove the
        # hint before sending and make the no-thinking policy explicit for
        # short karaoke calls.
        if request.pop("response_mode", None) == "text":
            request.pop("format", None)
            request["think"] = False
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
        if not content.strip():
            done_reason = str(envelope.get("done_reason") or "unknown")
            thinking = bool(str(message.get("thinking") or "").strip())
            raise TransportBlocked(
                "OLLAMA_EMPTY_CONTENT:"
                f"done_reason={done_reason}:thinking_present={str(thinking).lower()}"
            )
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
        if not content.strip():
            message = choices[0].get("message") or {}
            finish_reason = str(choices[0].get("finish_reason") or "unknown")
            reasoning = bool(str(message.get("reasoning_content") or "").strip())
            raise TransportBlocked(
                "OPENAI_COMPAT_EMPTY_CONTENT:"
                f"finish_reason={finish_reason}:reasoning_present={str(reasoning).lower()}"
            )
        return content


GROQ_TRANSLATION_MODEL_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "id": "openai/gpt-oss-20b",
        "label": "GPT-OSS 20B · recomendado",
        "description": "Rápido e econômico para tradução em lotes.",
        "recommended": True,
    },
    {
        "id": "openai/gpt-oss-120b",
        "label": "GPT-OSS 120B · maior qualidade",
        "description": "Mais qualidade, com maior consumo de tokens.",
        "recommended": False,
    },
    {
        "id": "qwen/qwen3.6-27b",
        "label": "Qwen 3.6 27B · equilibrado",
        "description": "Alternativa Qwen hospedada pela Groq.",
        "recommended": True,
    },
)
_GROQ_BLOCKLIST = ("whisper", "guard", "embed", "audio", "tts", "compound")


def groq_model_catalog() -> list[dict[str, Any]]:
    return [dict(item) for item in GROQ_TRANSLATION_MODEL_CATALOG]


def groq_models_from_api(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    known = {item["id"]: item for item in GROQ_TRANSLATION_MODEL_CATALOG}
    models: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        model_id = str(row.get("id") or "").strip()
        lowered = model_id.lower()
        if not model_id or any(token in lowered for token in _GROQ_BLOCKLIST):
            continue
        item = known.get(model_id, {})
        models.append({
            "id": model_id,
            "label": item.get("label") or model_id,
            "description": item.get("description") or "Modelo Groq compatível com chat.",
            "stable": True,
            "recommended": bool(item.get("recommended", False)),
        })
    models.sort(key=lambda item: (not item["recommended"], item["id"]))
    return models


class GroqTransport(OpenAICompatTransport):
    """Groq Inference API using its OpenAI-compatible chat endpoint."""

    name = "groq"

    @staticmethod
    def default_base_url() -> str:
        return "https://api.groq.com/openai/v1"


_NVIDIA_MODEL_NAMESPACES = {
    "deepseek-ai", "google", "meta", "microsoft", "minimaxai", "mistralai",
    "moonshotai", "nvidia", "openai", "poolside", "qwen", "sarvamai",
    "stepfun-ai", "stockmark", "thinkingmachines", "upstage", "z-ai",
}


def nvidia_model_is_supported(model: str) -> bool:
    """Check the provider namespace before an NVIDIA NIM request is made."""
    value = str(model or "").strip()
    namespace = value.split("/", 1)[0].casefold() if "/" in value else ""
    return bool(namespace and namespace in _NVIDIA_MODEL_NAMESPACES)


class NvidiaTransport(OpenAICompatTransport):
    """NVIDIA NIM API (build.nvidia.com) — OpenAI-compatible /chat/completions.

    Offers several free models (e.g. ``meta/llama-3.1-8b-instruct``,
    ``nvidia/llama-3.1-nemotron-70b-instruct``).  Reuses the OpenAI-compatible
    wire format with a default base URL so the user only needs the API key.
    """

    name = "nvidia"

    NVIDIA_MODEL_NAMESPACES = _NVIDIA_MODEL_NAMESPACES

    def __init__(self, *, model: str, base_url: str | None = None, api_key: str | None = None,
                 delay_between_calls: float = 0.0):
        if not nvidia_model_is_supported(model):
            raise TransportBlocked("NVIDIA_MODEL_AUTHORITY_MISMATCH")
        super().__init__(model=model, base_url=base_url, api_key=api_key,
                         delay_between_calls=delay_between_calls)

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
        plain_text = (
            canonical_payload.get("response_mode") == "text"
            or canonical_payload.get("gemini_plain_text")
        )
        request: dict[str, Any] = {
            "contents": [{"role": m["role"], "parts": [{"text": m["content"]}]}
                         for m in user_messages],
            "generationConfig": {
                "temperature": float(options.get("temperature", 0.0)),
                # Gemini truncates at maxOutputTokens; the canonical 1024 is
                # too small for 8-event batches with long translations.
                "maxOutputTokens": max(int(options.get("num_predict", 1024)), 8192),
                "responseMimeType": "text/plain" if plain_text else "application/json",
            },
        }
        if system_texts:
            request["systemInstruction"] = {"parts": [{"text": "\n".join(system_texts)}]}
        # The canonical V226 payload already contains the exact per-batch
        # schema.  Supplying it natively prevents Gemini from dropping IDs,
        # inventing fields, or mixing normal/segmented item shapes.  The
        # prompt remains present for providers without structured output; the
        # schema is an additional Gemini-only constraint.
        response_schema = canonical_payload.get("format")
        if isinstance(response_schema, dict) and not plain_text:
            request["generationConfig"]["responseSchema"] = _gemini_schema(response_schema)
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


class DeepseekTransport(OpenAICompatTransport):
    """DeepSeek API — OpenAI-compatible /chat/completions."""

    name = "deepseek"

    @staticmethod
    def default_base_url() -> str:
        return "https://api.deepseek.com/v1"

    def build_request(self, canonical_payload: dict[str, Any]) -> dict[str, Any]:
        """Map the app's no-thinking policy to DeepSeek's native field.

        DeepSeek V4 enables thinking by default.  The subtitle contracts need
        the visible answer, not a reasoning trace; with a small completion
        budget the model can otherwise spend the whole response on reasoning
        and return an empty ``message.content``.  Honor an explicit
        ``think=True`` for callers that intentionally opt into it, while the
        translation providers default to the deterministic disabled mode.
        """
        request = super().build_request(canonical_payload)
        request["thinking"] = {
            "type": "enabled" if canonical_payload.get("think") is True else "disabled"
        }
        return request


_PROVIDERS = {
    "ollama": OllamaTransport,
    "openai_compat": OpenAICompatTransport,
    "groq": GroqTransport,
    "gemini": GeminiTransport,
    "nvidia": NvidiaTransport,
    "deepseek": DeepseekTransport,
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
    if name == "gemini":
        model = migrate_gemini_model(str(model))
    # Environment/keyring resolution stays at the transport boundary.  The
    # execution context remains credential-free, while `.env` deployments and
    # headless runs behave like the UI-configured file/keyring path.
    api_key = tc.get("api_key") or tc.get("api_key_env_placeholder") or api_key_from_env(name)
    delay = tc.get("delay_between_calls", 0.0) if name in {"gemini", "groq", "deepseek"} else 0.0
    if name == "gemini":
        delay = max(GEMINI_MIN_DELAY_SECONDS, float(delay or 0.0))
    elif name == "groq":
        delay = max(GROQ_MIN_DELAY_SECONDS, float(delay or 0.0))
    elif name == "deepseek":
        delay = max(DEEPSEEK_MIN_DELAY_SECONDS, float(delay or 0.0))
    return cls(model=str(model), base_url=tc.get("base_url"), api_key=api_key,
               delay_between_calls=max(0.0, float(delay or 0.0)))


def api_key_from_env(provider_name: str, environ_getter=None) -> str | None:
    import os

    getter = environ_getter or os.environ.get
    env_names = {
        "openai_compat": ("TRANSPORT_API_KEY", "OPENAI_API_KEY", "GROQ_API_KEY",
                          "OPENROUTER_API_KEY"),
        "groq": ("TRANSPORT_API_KEY", "GROQ_API_KEY"),
        "gemini": ("TRANSPORT_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"),
        "nvidia": ("TRANSPORT_API_KEY", "NVIDIA_API_KEY", "NVIDIA_NIM_API_KEY"),
        "deepseek": ("TRANSPORT_API_KEY", "DEEPSEEK_API_KEY"),
        "ollama": ("TRANSPORT_API_KEY",),
    }
    for env_name in env_names.get(provider_name, ("TRANSPORT_API_KEY",)):
        value = getter(env_name)
        if value:
            return value
    return None
