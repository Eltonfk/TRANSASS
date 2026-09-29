"""Production transport construction for V3's provider-agnostic callback."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable


class V3ReplaySafeResponseProvider:
    """Replay completed captured calls; never resend ambiguous calls."""

    def __init__(self, provider: Any) -> None:
        self._provider = provider

    def __getattr__(self, name: str) -> Any:
        return getattr(self._provider, name)

    def respond(self, request: dict[str, Any], *, capture_id: str | None = None) -> dict[str, Any]:
        import hashlib
        import json

        from v236_durable_response_capture import DurableResponseCaptureV1, _atomic_json
        payload = dict(request)
        call_id, call_dir = self._provider._capture_dir(payload, capture_id)
        payload.setdefault("capture_id", call_id)
        if not call_dir.exists():
            return self._provider.respond(payload, capture_id=call_id)

        try:
            recorded_request = json.loads((call_dir / "request_payload.json").read_text(encoding="utf-8"))
            state = json.loads((call_dir / "capture_state.json").read_text(encoding="utf-8"))
            transport = json.loads((call_dir / "transport_result.json").read_text(encoding="utf-8"))
            raw = (call_dir / "raw-http-response.bin").read_bytes()
        except (OSError, ValueError, TypeError) as exc:
            raise RuntimeError("V3_CAPTURE_RECONCILIATION_REQUIRED") from exc

        if recorded_request != payload or state.get("call_id") != call_id:
            raise RuntimeError("V3_CAPTURE_REQUEST_IDENTITY_MISMATCH")
        digest = hashlib.sha256(raw).hexdigest()
        if (
            state.get("state") not in {"RESPONSE_DURABLE", "VALIDATION_PENDING", "VALIDATED_PASS"}
            or transport.get("raw_response_sha256") != digest
            or state.get("raw_response_sha256") != digest
        ):
            raise RuntimeError("V3_CAPTURE_RECONCILIATION_REQUIRED")

        response = self._provider.parse_response(raw)
        if not isinstance(response, dict):
            raise RuntimeError("V3_CAPTURE_RECONCILIATION_REQUIRED")

        parsed_path = call_dir / "parsed_response.json"
        if parsed_path.is_file():
            try:
                cached_response = json.loads(parsed_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError) as exc:
                raise RuntimeError("V3_CAPTURE_PARSED_CACHE_CORRUPT") from exc
            if cached_response != response:
                raise RuntimeError("V3_CAPTURE_PARSED_CACHE_MISMATCH")
        else:
            _atomic_json(parsed_path, response)

        if state.get("state") in {"RESPONSE_DURABLE", "VALIDATION_PENDING"}:
            capture = DurableResponseCaptureV1(call_dir, call_id=call_id)
            capture.validate(
                self._provider.parse_response,
                lambda value: {"response_object": isinstance(value, dict)},
            )
        self._provider.metrics["offline_replay_reads"] += 1
        return response


def is_v3_transport_error(exc: Exception) -> bool:
    """Classify only transport/provider failures as eligible for fallback."""
    import requests

    from transport_providers import TransportBlocked

    if isinstance(exc, (requests.exceptions.RequestException, TransportBlocked)):
        return True
    if type(exc) in {ConnectionError, TimeoutError}:
        return True
    message = str(exc).casefold()
    return any(token in message for token in (
        "connectionerror", "timeout", "max retries", "connection refused",
        "name or service not known", "temporarily unavailable",
    ))


def v3_batch_size(transport_config: dict[str, Any], provider_name: str) -> int:
    """Resolve a bounded provider batch size from profile or runtime config."""
    import os

    profile = transport_config.get(f"{str(provider_name).casefold()}_profile") or {}
    configured = profile.get("batch_size", os.environ.get("TRANSLATOR_BATCH_SIZE", 16))
    try:
        value = int(configured)
    except (TypeError, ValueError):
        value = 16
    return min(30, max(1, value))


def create_v3_live_transport(
    transport_config: dict[str, Any],
    section: dict[str, Any],
    *,
    capture_root: str | Path,
    before_call: Callable[[], bool] | None = None,
):
    """Create one explicitly configured V3 transport with durable call capture.

    This function performs no network or filesystem mutation until the returned
    response provider is called. Captures are scoped to a single attempt.
    """
    import os

    from pipeline_orchestrator import _build_v238_operation_budget
    from transport_providers import (
        API_KEY_PROVIDERS,
        DEEPSEEK_MIN_DELAY_SECONDS,
        GEMINI_MIN_DELAY_SECONDS,
        GROQ_MIN_DELAY_SECONDS,
        api_key_from_env,
        transport_from_config,
    )
    from v238_response_provider import DurableResponseProvider

    active = dict(section or {})
    provider_name = str(active.get("provider") or "").strip().casefold()
    model = str(active.get("model") or "").strip()
    if not provider_name or not model:
        raise RuntimeError("V3_TRANSPORT_PROVIDER_AND_MODEL_REQUIRED")

    keys = transport_config.get("keys") or {}
    if not active.get("api_key") and provider_name in keys:
        active["api_key"] = keys[provider_name]
    if not active.get("api_key"):
        active["api_key"] = api_key_from_env(provider_name)
    if provider_name in API_KEY_PROVIDERS and not str(active.get("api_key") or "").strip():
        raise RuntimeError(f"{provider_name.upper()}_API_KEY_MISSING")

    if provider_name == "ollama" and not str(active.get("base_url") or "").strip():
        ollama_url = os.environ.get("TRANSLATOR_OLLAMA_URL", "").strip()
        if ollama_url:
            active["base_url"] = ollama_url.rsplit("/api/chat", 1)[0]

    profile_key = {
        "gemini": ("gemini_profile", GEMINI_MIN_DELAY_SECONDS),
        "groq": ("groq_profile", GROQ_MIN_DELAY_SECONDS),
        "deepseek": ("deepseek_profile", DEEPSEEK_MIN_DELAY_SECONDS),
    }.get(provider_name)
    if profile_key:
        profile_name, minimum_delay = profile_key
        profile = transport_config.get(profile_name) or {}
        if profile.get("enabled", True):
            active["delay_between_calls"] = max(
                minimum_delay,
                float(profile.get("delay_between_calls", minimum_delay) or 0.0),
            )

    transport = transport_from_config(active, {"model": model})
    fallback = transport_config.get("fallback") or {}
    is_fallback = (
        provider_name == str(fallback.get("provider") or "").casefold()
        and model == str(fallback.get("model") or "")
    )
    transport.model_digest = active.get("model_digest") or (
        transport_config.get("fallback_model_digest")
        if is_fallback else (
            transport_config.get("primary_model_digest") or transport_config.get("model_digest")
        )
    )

    def client(canonical_payload: dict[str, Any]) -> bytes:
        from web_durable_provider import _http_post

        wire_payload = {
            key: value
            for key, value in canonical_payload.items()
            if key not in {"operation", "model_digest", "operation_id", "pipeline_version", "capture_id"}
        }
        request = transport.build_request(wire_payload)
        response = _http_post(
            transport.endpoint(),
            transport.headers(),
            request,
            delay=float(getattr(transport, "delay_between_calls", 0.0) or 0.0),
        )
        return response

    durable_provider = DurableResponseProvider(
        "LIVE_CAPTURED",
        capture_root=capture_root,
        client=client,
        provider_name=provider_name,
        transport_semantics="OLLAMA_MODEL" if provider_name == "ollama" else "HOSTED_MODEL",
        response_parser=lambda raw: {"translation": transport.extract_content(raw)},
    )
    budget = _build_v238_operation_budget({
        "provider": provider_name,
        "transport": transport,
        "transport_config": transport_config,
        "qwen_physical_maximum": transport_config.get("qwen_physical_maximum"),
    })
    _restore_v3_budget_reservations(budget, Path(capture_root), provider_name)
    durable_provider.attach_operation_budget(budget, phase="V3_TRANSLATION", provider=provider_name)
    durable_provider.attach_before_call(before_call)
    provider = V3ReplaySafeResponseProvider(durable_provider)
    return transport, provider


def _restore_v3_budget_reservations(budget: Any, capture_root: Path, provider: str) -> None:
    """Rehydrate persisted reservations before replaying a crashed operation."""
    import json

    if not capture_root.is_dir():
        return
    for call_dir in sorted(path for path in capture_root.iterdir() if path.is_dir()):
        request_path = call_dir / "request_payload.json"
        state_path = call_dir / "capture_state.json"
        if not request_path.is_file() or not state_path.is_file():
            continue
        try:
            request = json.loads(request_path.read_text(encoding="utf-8"))
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise RuntimeError("V3_CAPTURE_BUDGET_RECONCILIATION_REQUIRED") from exc
        if state.get("call_id") != call_dir.name:
            raise RuntimeError("V3_CAPTURE_BUDGET_IDENTITY_MISMATCH")
        try:
            budget.reserve(
                model_tag=str(request.get("model") or "qwen3.5:9b"),
                model_digest=request.get("model_digest"),
                phase="V3_TRANSLATION",
                reservation_id=call_dir.name,
                provider=provider,
            )
        except RuntimeError as exc:
            # Existing completed captures remain replayable if an operator
            # lowered the current ceiling; the first new call stays blocked.
            if "PHYSICAL_CALL_BUDGET_EXCEEDED" not in str(exc):
                raise
            return


__all__ = [
    "V3ReplaySafeResponseProvider",
    "create_v3_live_transport",
    "is_v3_transport_error",
    "v3_batch_size",
]
