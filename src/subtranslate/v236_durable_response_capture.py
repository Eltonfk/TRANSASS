"""Durable response capture for candidate/canary HTTP calls.

This module is operational only.  It never decides semantic validity and never
reissues a request.  A caller must explicitly invoke validation after the raw
HTTP body was fsynced and atomically promoted to its canonical evidence path.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

import requests


STATES = {
    "PLANNED", "REQUEST_DURABLE", "TRANSPORT_IN_PROGRESS", "RESPONSE_DURABLE",
    "VALIDATION_PENDING", "VALIDATED_PASS", "VALIDATED_FAIL", "TRANSPORT_FAILED",
    "CAPTURE_INCOMPLETE",
}


class DurableResponseCaptureError(RuntimeError):
    """A transport/capture boundary failed and requires reconciliation.

    Callers must not treat this as an ordinary model validation failure: the
    durable call directory is the source of truth and no automatic resend is
    permitted until it is reconciled externally.
    """



def _now() -> str:
    return datetime.now(UTC).isoformat()


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_bytes_durable(path: Path, data: bytes) -> None:
    with path.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    _write_bytes_durable(temporary, (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    os.replace(temporary, path)
    _fsync_directory(path.parent)


class DurableResponseCaptureV1:
    """One call directory; ambiguous states are terminal until external action.

    The class intentionally has no retry method.  `receive()` is permitted
    only from REQUEST_DURABLE, so a later invocation sees the durable state and
    cannot silently transmit a duplicate request.
    """

    def __init__(self, call_dir: str | Path, *, call_id: str):
        self.call_dir = Path(call_dir)
        self.call_id = call_id
        self.state_path = self.call_dir / "capture_state.json"

    def _state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"call_id": self.call_id, "state": "PLANNED", "history": []}
        value = json.loads(self.state_path.read_text(encoding="utf-8"))
        if value.get("call_id") != self.call_id or value.get("state") not in STATES:
            raise RuntimeError("DURABLE_CAPTURE_STATE_CORRUPT")
        return value

    def _transition(self, state: str, **facts: Any) -> dict[str, Any]:
        if state not in STATES:
            raise ValueError("DURABLE_CAPTURE_UNKNOWN_STATE")
        value = self._state()
        value["state"] = state
        value.update(facts)
        value.setdefault("history", []).append({"state": state, "at": _now()})
        _atomic_json(self.state_path, value)
        return value

    def prepare(self, payload: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
        current = self._state()
        if current["state"] != "PLANNED":
            raise RuntimeError("DURABLE_CAPTURE_DUPLICATE_CALL_ID")
        self.call_dir.mkdir(parents=True, exist_ok=False)
        _atomic_json(self.call_dir / "request_payload.json", payload)
        return self._transition(
            "REQUEST_DURABLE",
            request_path="request_payload.json",
            request_sha256=_sha(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")),
            metadata=metadata,
        )

    def receive(self, url: str, *, post: Callable[..., Any] = requests.post, timeout: float = 240.0) -> dict[str, Any]:
        current = self._state()
        if current["state"] != "REQUEST_DURABLE":
            raise RuntimeError("DURABLE_CAPTURE_TRANSPORT_NOT_PERMITTED_FROM_CURRENT_STATE")
        payload = json.loads((self.call_dir / "request_payload.json").read_text(encoding="utf-8"))
        self._transition("TRANSPORT_IN_PROGRESS", transport_started_at=_now())
        partial_path = self.call_dir / "response.body.tmp"
        try:
            response = post(url, json=payload, timeout=timeout, stream=True)
            headers = {"http_status": response.status_code, "headers": dict(response.headers), "received_at": _now()}
            _write_bytes_durable(self.call_dir / "response.headers.tmp", (json.dumps(headers, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
            byte_count = 0
            with partial_path.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=65536):
                    if not chunk:
                        continue
                    handle.write(chunk)
                    byte_count += len(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            # Body bytes are now complete and durable; only this point may
            # create the canonical raw response name.
            canonical = self.call_dir / "raw-http-response.bin"
            os.replace(partial_path, canonical)
            _fsync_directory(self.call_dir)
            os.replace(self.call_dir / "response.headers.tmp", self.call_dir / "response.headers.json")
            _fsync_directory(self.call_dir)
            transport = {
                "http_status": response.status_code,
                "raw_response_path": canonical.name,
                "raw_response_bytes": byte_count,
                "raw_response_sha256": _sha(canonical.read_bytes()),
                "transport_finished_at": _now(),
                "client_exit_status": 0,
            }
            _atomic_json(self.call_dir / "transport_result.json", transport)
            state = "RESPONSE_DURABLE" if response.status_code == 200 else "TRANSPORT_FAILED"
            return self._transition(state, **transport)
        except Exception as exc:
            partial_bytes = partial_path.stat().st_size if partial_path.exists() else 0
            # Any partial bytes remain evidence but must never be parsed.
            return self._transition(
                "CAPTURE_INCOMPLETE",
                transport_error=f"{type(exc).__name__}:{exc}"[:1000],
                partial_response_path=partial_path.name if partial_path.exists() else None,
                partial_response_bytes=partial_bytes,
                transport_finished_at=_now(),
                client_exit_status=1,
            )

    def receive_injected(self, raw_body: bytes, *, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        """Persist bytes returned by an explicitly injected transport.

        The production client remains outside this class.  This seam is used
        by the generic V2.3.8 provider so tests and callers can inject a
        transport without giving the capture layer a second HTTP authority.
        """
        current = self._state()
        if current["state"] != "REQUEST_DURABLE":
            raise RuntimeError("DURABLE_CAPTURE_INJECTED_NOT_PERMITTED_FROM_CURRENT_STATE")
        self._transition("TRANSPORT_IN_PROGRESS", transport_started_at=_now(), transport_metadata=metadata or {})
        canonical = self.call_dir / "raw-http-response.bin"
        _write_bytes_durable(canonical, bytes(raw_body))
        _fsync_directory(self.call_dir)
        transport = {
            "http_status": 200,
            "raw_response_path": canonical.name,
            "raw_response_bytes": len(raw_body),
            "raw_response_sha256": _sha(canonical.read_bytes()),
            "transport_finished_at": _now(),
            "client_exit_status": 0,
            "injected_transport": True,
        }
        _atomic_json(self.call_dir / "transport_result.json", transport)
        return self._transition("RESPONSE_DURABLE", **transport)

    def validate(self, parser: Callable[[bytes], Any], validator: Callable[[Any], Any]) -> dict[str, Any]:
        current = self._state()
        if current["state"] not in {"RESPONSE_DURABLE", "VALIDATION_PENDING"}:
            raise RuntimeError("DURABLE_CAPTURE_VALIDATION_NOT_PERMITTED_FROM_CURRENT_STATE")
        raw_path = self.call_dir / "raw-http-response.bin"
        if not raw_path.exists():
            raise RuntimeError("DURABLE_CAPTURE_RESPONSE_MISSING")
        self._transition("VALIDATION_PENDING", validation_started_at=_now())
        try:
            raw = raw_path.read_bytes()
            parsed = parser(raw)
            result = validator(parsed)
            _atomic_json(self.call_dir / "validation_result.json", {"result": result, "validated_at": _now()})
            return self._transition("VALIDATED_PASS", validation_result="PASS")
        except Exception as exc:
            # Raw body stays canonical and can be inspected/revalidated later.
            _atomic_json(self.call_dir / "validation_failure.json", {"error": f"{type(exc).__name__}:{exc}"[:1000], "failed_at": _now()})
            return self._transition("VALIDATED_FAIL", validation_result="FAIL", validation_error=f"{type(exc).__name__}:{exc}"[:1000])

    def mark_validation_failure(self, error: BaseException) -> dict[str, Any]:
        """Record a parse/schema failure without discarding durable evidence."""
        current = self._state()
        if current["state"] not in {"RESPONSE_DURABLE", "VALIDATION_PENDING"}:
            raise RuntimeError("DURABLE_CAPTURE_FAILURE_NOT_PERMITTED_FROM_CURRENT_STATE")
        _atomic_json(
            self.call_dir / "validation_failure.json",
            {"error": f"{type(error).__name__}:{error}"[:1000], "failed_at": _now()},
        )
        return self._transition(
            "VALIDATED_FAIL",
            validation_result="FAIL",
            validation_error=f"{type(error).__name__}:{error}"[:1000],
        )

    def reconcile(self) -> dict[str, Any]:
        state = self._state()
        raw = self.call_dir / "raw-http-response.bin"
        partial = self.call_dir / "response.body.tmp"
        if state["state"] in {"RESPONSE_DURABLE", "VALIDATION_PENDING"} and raw.exists():
            action = "OFFLINE_VALIDATION_REQUIRED_NO_NEW_HTTP"
        elif state["state"] in {"REQUEST_DURABLE", "TRANSPORT_IN_PROGRESS", "CAPTURE_INCOMPLETE"}:
            action = "EXTERNAL_DECISION_REQUIRED_NO_AUTOMATIC_RESEND"
        else:
            action = "TERMINAL_OR_INVALID_STATE"
        return {"call_id": self.call_id, "state": state["state"], "raw_response_exists": raw.exists(), "partial_exists": partial.exists(), "next_action": action}
