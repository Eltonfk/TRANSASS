"""Explicit response boundary for the reusable V2.3.8 stage.

The stage never discovers a model client by itself.  A caller must inject one
of the three deliberately separate providers below.  This keeps live capture,
offline replay, and deterministic tests from silently crossing boundaries.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable, Mapping

from v236_durable_response_capture import DurableResponseCaptureV1, _atomic_json


class ResponseProviderError(RuntimeError):
    """A response was unavailable, ambiguous, or failed its strict contract."""


class ResponseSchemaError(ResponseProviderError):
    """A durable response did not satisfy the V2.3.8 response schema."""


def _canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _safe_id(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "call"))
    return token[:96] or "call"


def _parse_response(value: Any) -> dict[str, Any]:
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ResponseSchemaError("V238_RESPONSE_NOT_UTF8") from exc
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ResponseSchemaError("V238_RESPONSE_NOT_JSON") from exc
    if not isinstance(value, Mapping):
        raise ResponseSchemaError("V238_RESPONSE_ROOT_NOT_OBJECT")
    result = dict(value)
    if "candidates" in result:
        if not isinstance(result["candidates"], list):
            raise ResponseSchemaError("V238_CANDIDATES_NOT_LIST")
        for item in result["candidates"]:
            if not isinstance(item, Mapping) or not item.get("canonical_unit_id"):
                raise ResponseSchemaError("V238_CANDIDATE_ID_REQUIRED")
            if "text" in item and not isinstance(item["text"], str):
                raise ResponseSchemaError("V238_CANDIDATE_TEXT_NOT_STRING")
            if "translation" in item and not isinstance(item["translation"], str):
                raise ResponseSchemaError("V238_CANDIDATE_TRANSLATION_NOT_STRING")
        return result
    if "translation" in result and not isinstance(result["translation"], str):
        raise ResponseSchemaError("V238_TRANSLATION_NOT_STRING")
    if "text" in result and not isinstance(result["text"], str):
        raise ResponseSchemaError("V238_TEXT_NOT_STRING")
    if "translation" not in result and "text" not in result and "ass_text" not in result and "ownership_runs" not in result and "owner_vector" not in result:
        raise ResponseSchemaError("V238_RESPONSE_HAS_NO_SUPPORTED_PAYLOAD")
    return result


class DurableResponseProvider:
    """Provider with explicit LIVE_CAPTURED/OFFLINE_REPLAY/TEST_FAKE modes."""

    MODES = {"LIVE_CAPTURED", "OFFLINE_REPLAY", "TEST_FAKE"}
    TRANSPORTS = {"OLLAMA_MODEL", "NETWORK_NON_MODEL", "LOCAL_TEST", "OFFLINE_REPLAY", "TEST_FAKE"}

    def __init__(
        self,
        mode: str,
        *,
        capture_root: str | Path | None = None,
        client: Callable[[dict[str, Any]], Any] | None = None,
        fake: Callable[[dict[str, Any]], Any] | Mapping[str, Any] | None = None,
        expected_capture_ids: Mapping[str, str] | None = None,
        transport_semantics: str | None = None,
    ) -> None:
        self.mode = str(mode or "").upper()
        if self.mode not in self.MODES:
            raise ValueError(f"unsupported V2.3.8 response mode: {self.mode or '<empty>'}")
        self.capture_root = Path(capture_root) if capture_root is not None else None
        self.client = client
        self.fake = fake
        self.expected_capture_ids = dict(expected_capture_ids or {})
        default_transport = {
            "LIVE_CAPTURED": "OLLAMA_MODEL",
            "OFFLINE_REPLAY": "OFFLINE_REPLAY",
            "TEST_FAKE": "TEST_FAKE",
        }[self.mode]
        self.transport_semantics = str(transport_semantics or default_transport).upper()
        if self.transport_semantics not in self.TRANSPORTS:
            raise ValueError(f"unsupported V2.3.8 transport semantics: {self.transport_semantics or '<empty>'}")
        self.calls: list[dict[str, Any]] = []
        self.metrics: dict[str, Any] = {
            "physical_client_calls": 0, "model_generation_calls": 0,
            "application_network_calls": 0, "offline_replay_reads": 0,
            "test_fake_responses": 0, "durable_capture_writes": 0,
            "parse_failures": 0, "schema_failures": 0,
            "provider_requests": 0, "requests_by_operation": {},
        }
        self.operation_budget: Any = None
        self.operation_budget_phase = "V238_SEMANTIC"
        if self.mode in {"LIVE_CAPTURED", "OFFLINE_REPLAY"} and self.capture_root is None:
            raise ValueError(f"{self.mode} requires an explicit capture_root")

    def attach_operation_budget(self, budget: Any, *, phase: str = "V238_SEMANTIC") -> None:
        self.operation_budget = budget
        self.operation_budget_phase = str(phase)

    def _capture_dir(self, request: Mapping[str, Any], capture_id: str | None) -> tuple[str, Path]:
        raw_id = capture_id or str(request.get("capture_id") or "")
        if not raw_id:
            digest = hashlib.sha256(_canonical(dict(request))).hexdigest()[:24]
            raw_id = f"v238-call-{digest}"
        call_id = _safe_id(raw_id)
        root = self.capture_root if self.capture_root is not None else Path(".")
        return call_id, root / call_id

    def _fake_response(self, request: dict[str, Any], call_id: str) -> dict[str, Any]:
        if callable(self.fake):
            return _parse_response(self.fake(request))
        if isinstance(self.fake, Mapping):
            if call_id in self.fake:
                return _parse_response(self.fake[call_id])
            if "default" in self.fake:
                return _parse_response(self.fake["default"])
        # TEST_FAKE is intentionally source-preserving: it proves the call
        # graph and structural contracts without pretending to be a model.
        return {"translation": str(request.get("text", "")), "mode": "TEST_FAKE"}

    def _offline_response(self, request: dict[str, Any], call_id: str, call_dir: Path) -> dict[str, Any]:
        if not call_dir.is_dir():
            raise ResponseProviderError("V238_OFFLINE_CAPTURE_MISSING")
        expected = self.expected_capture_ids.get(str(request.get("capture_id", call_id)))
        if expected and expected != call_id:
            raise ResponseProviderError("V238_OFFLINE_CAPTURE_ID_MISMATCH")
        request_path = call_dir / "request_payload.json"
        if not request_path.is_file():
            raise ResponseProviderError("V238_OFFLINE_REQUEST_MISSING")
        recorded = json.loads(request_path.read_text(encoding="utf-8"))
        if hashlib.sha256(_canonical(recorded)).hexdigest() != hashlib.sha256(_canonical(request)).hexdigest():
            raise ResponseProviderError("V238_OFFLINE_REQUEST_IDENTITY_MISMATCH")
        candidates = (call_dir / "response_payload.json", call_dir / "parsed_response.json", call_dir / "raw-http-response.bin")
        response_path = next((path for path in candidates if path.is_file()), None)
        if response_path is None:
            raise ResponseProviderError("V238_OFFLINE_RESPONSE_MISSING")
        self.metrics["offline_replay_reads"] += 1
        try:
            return _parse_response(response_path.read_bytes())
        except ResponseSchemaError:
            self.metrics["parse_failures"] += 1
            self.metrics["schema_failures"] += 1
            raise

    def respond(self, request: Mapping[str, Any], *, capture_id: str | None = None) -> dict[str, Any]:
        payload = dict(request)
        call_id, call_dir = self._capture_dir(payload, capture_id)
        payload.setdefault("capture_id", call_id)
        self.metrics["provider_requests"] += 1
        operation = str(payload.get("operation", "unknown"))
        by_operation = self.metrics["requests_by_operation"]
        by_operation[operation] = int(by_operation.get(operation, 0)) + 1
        self.calls.append({
            "capture_id": call_id,
            "mode": self.mode,
            "transport_semantics": self.transport_semantics,
            "request_sha256": hashlib.sha256(_canonical(payload)).hexdigest(),
            "operation": operation,
        })
        if self.mode == "TEST_FAKE":
            response = self._fake_response(payload, call_id)
            self.metrics["test_fake_responses"] += 1
        elif self.mode == "OFFLINE_REPLAY":
            response = self._offline_response(payload, call_id, call_dir)
        else:
            if self.client is None:
                raise ResponseProviderError("V238_LIVE_CLIENT_NOT_INJECTED")
            if self.operation_budget is not None:
                model_tag = str(payload.get("model") or "qwen3.5:9b")
                model_digest = payload.get("model_digest")
                self.operation_budget.reserve(model_tag=model_tag, model_digest=model_digest, phase=self.operation_budget_phase)
            call_dir.parent.mkdir(parents=True, exist_ok=True)
            capture = DurableResponseCaptureV1(call_dir, call_id=call_id)
            capture.prepare(payload, {"mode": self.mode, "capture_id": call_id})
            self.metrics["durable_capture_writes"] += 1
            self.metrics["physical_client_calls"] += 1
            if self.transport_semantics in {"OLLAMA_MODEL", "NETWORK_NON_MODEL"}:
                self.metrics["application_network_calls"] += 1
            if self.transport_semantics == "OLLAMA_MODEL":
                self.metrics["model_generation_calls"] += 1
            def invoke_client() -> bytes:
                raw = self.client(payload)
                return raw if isinstance(raw, bytes) else _canonical(raw)

            raw_bytes, _capture_state = capture.run_injected_transport(
                invoke_client,
                metadata={"mode": self.mode, "transport_semantics": self.transport_semantics},
            )
            self.metrics["durable_capture_writes"] += 1
            try:
                response = _parse_response(raw_bytes)
            except ResponseSchemaError as exc:
                self.metrics["parse_failures"] += 1
                self.metrics["schema_failures"] += 1
                capture.mark_validation_failure(exc)
                raise
            _atomic_json(call_dir / "parsed_response.json", response)
            self.metrics["durable_capture_writes"] += 1
        return response

    def translate(self, request: Mapping[str, Any], *, capture_id: str | None = None) -> str:
        result = self.respond(request, capture_id=capture_id)
        value = result.get("translation", result.get("text"))
        if not isinstance(value, str):
            raise ResponseSchemaError("V238_TRANSLATION_MISSING")
        return value

    def ownership(self, request: Mapping[str, Any], *, capture_id: str | None = None) -> dict[str, Any]:
        return self.respond(request, capture_id=capture_id)


__all__ = ["DurableResponseProvider", "ResponseProviderError", "ResponseSchemaError"]
