"""Web transport adapter as a DurableResponseProvider subclass (C2).

Subclasses the concrete ``DurableResponseProvider`` (v238_response_provider.py:69)
because the stage requires ``isinstance(value, DurableResponseProvider)``
(v238_full_translation_stage.py:196-200).  A Protocol-only implementation would
fail that check.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from v238_response_provider import DurableResponseProvider
from transport_providers import transport_from_config


def _http_post(url: str, headers: dict[str, str], request: dict[str, Any]) -> bytes:
    """EXACTLY ONE HTTP POST.  Imported lazily so offline tests never load
    the requests dependency graph unless a live call is actually made."""
    import requests

    response = requests.post(url, headers=headers, json=request, timeout=300)
    response.raise_for_status()
    return response.content


def _decode_model_content(content: str) -> dict[str, Any]:
    """Decodifica o texto do modelo; se JSON, retorna o dict (B1)."""
    text = (content or "").strip()
    if text.startswith("{") or text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    return {"text": content}


class WebDurableResponseProvider(DurableResponseProvider):
    """Subclasse de DurableResponseProvider que expõe o transport_config web.

    O fallback de transporte (primary -> fallback) NÃO acontece dentro do
    provider: é uma re-execução completa com novo operation_id
    (web_retranslation_runner.py:102-107, D5).
    """

    def __init__(
        self,
        transport_config: dict,
        *,
        mode: str = "LIVE_CAPTURED",
        capture_root: Path,
        api_key: str | None = None,
    ):
        provider = str((transport_config.get("primary") or {}).get("provider", "ollama")).lower()
        transport_semantics = "OLLAMA_MODEL" if provider == "ollama" else "NETWORK_NON_MODEL"
        super().__init__(mode, capture_root=capture_root, transport_semantics=transport_semantics)
        self._transport_config = transport_config
        self._api_key = api_key
        self._client = self._build_client()

    def _build_client(self) -> Callable[[dict[str, Any]], dict[str, Any]]:
        def client(payload: dict) -> dict:
            section = self._select_section(payload)
            section = self._inject_api_key(section)
            transport = transport_from_config(section, payload)
            chat_payload = self._project_request(payload)
            request = transport.build_request(chat_payload)
            body = _http_post(transport.endpoint(), transport.headers(), request)
            content = transport.extract_content(body)
            parsed = _decode_model_content(content)
            translation = parsed.get("translation") or parsed.get("text") or content
            return {"translation": translation}
        return client

    def _project_request(self, payload: dict) -> dict:
        """Stage payload V238 -> chat-shape para transport.build_request (B1).

        O payload do stage NÃO tem messages/options
        (v238_full_translation_stage.py:444-452); build_request exige
        chat-shape (transport_providers.py:58-69).
        """
        text = payload.get("text") or payload.get("source_text") or ""
        return {
            "messages": [{"role": "user", "content": text}],
            "options": {"temperature": 0.0, "num_predict": 1024},
            "format": "json",
        }

    def _inject_api_key(self, section: dict) -> dict:
        """Injeta api_key da seção keys do transport_config (B1)."""
        provider = str(section.get("provider", "")).lower()
        keys = self._transport_config.get("keys") or {}
        if not section.get("api_key") and provider in keys and keys[provider]:
            section = dict(section)
            section["api_key"] = keys[provider]
        return section

    def _select_section(self, payload: dict) -> dict:
        """Primary incondicional dentro do provider (R2.5/D5)."""
        primary = self._transport_config.get("primary") or {}
        if primary.get("provider"):
            return primary
        fallback = self._transport_config.get("fallback") or {}
        return fallback or {"provider": "ollama", "model": "qwen3.5:9b"}


__all__ = ["WebDurableResponseProvider", "_http_post", "_decode_model_content"]
