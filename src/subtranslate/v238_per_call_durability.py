"""Opt-in, per-call durability for the canonical V2.3.8 V226 seam.

Legacy V2.2.x callers do not provide ``durable_call_root`` and therefore keep
their existing request path.  The canonical V2.3.8 execution context opts in
to this module.  Request bytes and response bytes are durable before parsing;
an in-flight call is never retried automatically.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping


STATES = {
    "PLANNED", "RESERVED", "REQUEST_DURABLE", "TRANSPORT_IN_PROGRESS",
    "RESPONSE_DURABLE", "PARSED_VALID", "PARSED_INVALID",
    "CANCELLED_CONFIRMED", "TRANSPORT_FAILED_CONFIRMED",
    "TRANSPORT_OUTCOME_UNKNOWN", "RESERVATION_FAILED",
}
TERMINAL_STATES = {"PARSED_VALID", "PARSED_INVALID", "CANCELLED_CONFIRMED", "TRANSPORT_FAILED_CONFIRMED"}


class DurableCallError(RuntimeError):
    """A durable call requires external reconciliation or failed closed."""

    durability_stop = True


class DurableCallOutcomeUnknown(DurableCallError):
    """The process could not prove whether the server completed a call."""

    durability_stop = True


class DurableCallFault(DurableCallError):
    """Deterministic fault injection used by offline crash/resume tests."""

    durability_stop = True


def _now() -> str:
    return datetime.now(UTC).isoformat()


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _fsync_dir(path: Path) -> None:
    descriptor = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_bytes(path: Path, data: bytes, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temporary = Path(raw)
    try:
        os.chmod(temporary, mode)
        with temporary.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, mode)
        _fsync_dir(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_json(path: Path, value: Any) -> None:
    _atomic_bytes(path, canonical_bytes(value))


class EpisodeBudgetLedger:
    """Small locked JSON ledger shared by all calls of one episode family."""

    def __init__(self, path: str | Path, *, episode_id: Any, limits: Mapping[str, Any]):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self.episode_id = str(episode_id)
        self.limits = {
            "planned_initial_calls": int(limits.get("planned_initial_calls", 0) or 0),
            "retry_reserve": int(limits.get("retry_reserve", 0) or 0),
            "physical_ceiling": int(limits.get("physical_ceiling", 0) or 0),
        }

    def _initial(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            **self.limits,
            "initial_consumed": 0,
            "retry_consumed": 0,
            "successful_durable_responses": 0,
            "invalid_responses": 0,
            "cancelled_confirmed": 0,
            "transport_failures_confirmed": 0,
            "unknown_outcomes": 0,
            "reservations": [],
            "updated_at": _now(),
        }

    def _read_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._initial()
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if str(value.get("episode_id")) != self.episode_id:
            raise DurableCallError("V238_EPISODE_BUDGET_IDENTITY_MISMATCH")
        for key, expected in self.limits.items():
            if int(value.get(key, expected)) != expected:
                raise DurableCallError("V238_EPISODE_BUDGET_LIMIT_MISMATCH")
        return value

    def _locked(self):
        self.lock_path.touch(mode=0o600, exist_ok=True)
        os.chmod(self.lock_path, 0o600)
        handle = self.lock_path.open("r+")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        return handle

    def reserve(self, *, attempt_id: str, model_tag: str, model_digest: str | None,
                phase: str, attempt_type: str) -> dict[str, Any]:
        handle = self._locked()
        try:
            value = self._read_unlocked()
            for row in value["reservations"]:
                if row.get("attempt_id") == attempt_id:
                    return {**row, "_reused": True}
            consumed = int(value["initial_consumed"]) + int(value["retry_consumed"])
            cap = int(value.get("physical_ceiling", 0))
            if cap > 0 and consumed >= cap:
                raise DurableCallError("V238_EPISODE_BUDGET_EXHAUSTED_BEFORE_TRANSPORT")
            retry = attempt_type != "initial"
            if retry and int(value["retry_consumed"]) >= int(value.get("retry_reserve", 0)):
                raise DurableCallError("V238_EPISODE_RETRY_RESERVE_EXHAUSTED")
            row = {
                "attempt_id": attempt_id,
                "ordinal": consumed + 1,
                "attempt_type": attempt_type,
                "phase": phase,
                "model_tag": model_tag,
                "model_digest": model_digest,
                "reserved_at": _now(),
                "state": "RESERVED",
            }
            value["reservations"].append(row)
            value["retry_consumed" if retry else "initial_consumed"] += 1
            value["updated_at"] = _now()
            _atomic_json(self.path, value)
            return {**row, "_reused": False}
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    def release(self, attempt_id: str) -> None:
        handle = self._locked()
        try:
            value = self._read_unlocked()
            for row in value["reservations"]:
                if row.get("attempt_id") == attempt_id and row.get("state") == "RESERVED":
                    row["state"] = "RESERVATION_RELEASED"
                    if row.get("attempt_type") == "initial":
                        value["initial_consumed"] = max(0, int(value["initial_consumed"]) - 1)
                    else:
                        value["retry_consumed"] = max(0, int(value["retry_consumed"]) - 1)
                    value["updated_at"] = _now()
                    _atomic_json(self.path, value)
                    return
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    def update_attempt(self, attempt_id: str, **facts: Any) -> None:
        handle = self._locked()
        try:
            value = self._read_unlocked()
            for row in value["reservations"]:
                if row.get("attempt_id") == attempt_id:
                    row.update(facts)
                    value["updated_at"] = _now()
                    _atomic_json(self.path, value)
                    return
            raise DurableCallError("V238_BUDGET_ATTEMPT_NOT_FOUND")
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    def snapshot(self) -> dict[str, Any]:
        handle = self._locked()
        try:
            value = self._read_unlocked()
            consumed = int(value.get("initial_consumed", 0)) + int(value.get("retry_consumed", 0))
            cap = int(value.get("physical_ceiling", 0))
            value["physical_consumed"] = consumed
            value["physical_remaining"] = max(0, cap - consumed) if cap > 0 else None
            value["initial_remaining"] = max(0, int(value.get("planned_initial_calls", 0)) - int(value.get("initial_consumed", 0))) if int(value.get("planned_initial_calls", 0)) > 0 else None
            value["retry_remaining"] = max(0, int(value.get("retry_reserve", 0)) - int(value.get("retry_consumed", 0))) if int(value.get("retry_reserve", 0)) > 0 else None
            return value
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()


class DurableV226Call:
    """Durable request/response state machine for one physical V226 call."""

    def __init__(self, context: Mapping[str, Any], payload: Mapping[str, Any], metadata: Mapping[str, Any],
                 *, operation_budget: Any = None):
        required = ("operation_id", "episode_id", "source_sha256", "model", "model_digest", "durable_call_root")
        missing = [key for key in required if context.get(key) in (None, "")]
        if missing:
            raise DurableCallError("V238_DURABLE_CONTEXT_MISSING:" + ",".join(missing))
        payload_bytes = canonical_bytes(payload)
        self.context = dict(context)
        self.payload = dict(payload)
        self.payload_bytes = payload_bytes
        self.payload_sha256 = sha256_bytes(payload_bytes)
        self.metadata = dict(metadata)
        self.metadata.update({"request_payload_sha256": self.payload_sha256, "request_payload_bytes": len(payload_bytes)})
        self.operation_budget = operation_budget
        attempt_ordinal = int(metadata.get("attempt_ordinal", 1) or 1)
        identity = {
            "operation_id": str(context["operation_id"]),
            "episode_id": str(context["episode_id"]),
            "source_sha256": str(context["source_sha256"]),
            "model_tag": str(context["model"]),
            "model_digest": str(context["model_digest"]),
            "phase": str(metadata.get("phase", "initial")),
            "batch_index": int(metadata.get("batch_index", 0) or 0),
            "unit_membership_sha256": str(metadata.get("unit_membership_sha256", "")),
            "request_payload_sha256": self.payload_sha256,
            "attempt_ordinal": attempt_ordinal,
        }
        self.identity = identity
        self.request_id = "v226-" + hashlib.sha256(canonical_bytes(identity)).hexdigest()[:32]
        root = Path(context["durable_call_root"])
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(root, 0o700)
        self.call_dir = root / "calls" / self.request_id
        self.state_path = self.call_dir / "state.json"
        budget_path = context.get("episode_budget_ledger_path") or (root / "episode-budget.json")
        limits = context.get("episode_budget_limits") or {
            "planned_initial_calls": context.get("planned_initial_calls", 0),
            "retry_reserve": context.get("retry_reserve", 0),
            "physical_ceiling": context.get("physical_ceiling", context.get("qwen_physical_maximum", 0)),
        }
        self.budget_ledger = EpisodeBudgetLedger(budget_path, episode_id=context["episode_id"], limits=limits)
        self._fault_point = str(context.get("durability_fault_point") or "")

    def _state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"request_id": self.request_id, "state": "PLANNED", "identity": self.identity, "history": []}
        value = json.loads(self.state_path.read_text(encoding="utf-8"))
        if value.get("request_id") != self.request_id or value.get("identity") != self.identity:
            raise DurableCallError("V238_DURABLE_CALL_IDENTITY_MISMATCH")
        if value.get("state") not in STATES:
            raise DurableCallError("V238_DURABLE_CALL_STATE_CORRUPT")
        return value

    def state(self) -> str:
        return str(self._state().get("state"))

    def _transition(self, state: str, **facts: Any) -> dict[str, Any]:
        if state not in STATES:
            raise DurableCallError("V238_DURABLE_CALL_UNKNOWN_STATE")
        value = self._state()
        value["state"] = state
        value.update(facts)
        value.setdefault("history", []).append({"state": state, "at": _now()})
        _atomic_json(self.state_path, value)
        # ``capture_state.json`` is a compatibility/readability alias for
        # operators inspecting a call directory; both files are written by
        # the same atomic transition and therefore cannot disagree after a
        # successful transition.
        _atomic_json(self.call_dir / "capture_state.json", value)
        return value

    def _fault(self, point: str) -> None:
        if self._fault_point == point:
            raise DurableCallFault("V238_DURABILITY_FAULT:" + point)

    def reserve(self) -> dict[str, Any]:
        current = self._state()
        if current["state"] in TERMINAL_STATES:
            return current
        if current["state"] in {"REQUEST_DURABLE", "TRANSPORT_IN_PROGRESS", "RESPONSE_DURABLE", "PARSED_VALID", "PARSED_INVALID", "TRANSPORT_OUTCOME_UNKNOWN"}:
            return current
        attempt_type = "initial" if str(self.metadata.get("phase", "initial")) == "initial" else "retry"
        reservation = self.budget_ledger.reserve(
            attempt_id=self.request_id,
            model_tag=str(self.context["model"]),
            model_digest=str(self.context["model_digest"]),
            phase=str(self.metadata.get("phase", "initial")),
            attempt_type=attempt_type,
        )
        try:
            if self.operation_budget is not None and not reservation.get("_reused"):
                self.operation_budget.reserve(model_tag=str(self.context["model"]), model_digest=str(self.context["model_digest"]), phase="V226_QWEN")
        except Exception:
            self.budget_ledger.release(self.request_id)
            self.call_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(self.call_dir, 0o700)
            self._transition("RESERVATION_FAILED", error="operation_budget_rejected")
            raise
        self.call_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.call_dir, 0o700)
        value = self._transition("RESERVED", reservation=reservation, reserved_at=_now())
        self._fault("after_reservation")
        return value

    def prepare_request(self) -> dict[str, Any]:
        current = self.reserve()
        if current["state"] == "REQUEST_DURABLE":
            return current
        if current["state"] in TERMINAL_STATES or current["state"] == "TRANSPORT_OUTCOME_UNKNOWN":
            return current
        _atomic_bytes(self.call_dir / "request_payload.json", self.payload_bytes)
        _atomic_json(self.call_dir / "request_metadata.json", {**self.metadata, "request_id": self.request_id, "identity": self.identity, "request_sha256": self.payload_sha256})
        value = self._transition("REQUEST_DURABLE", request_path="request_payload.json", request_sha256=self.payload_sha256, request_bytes=len(self.payload_bytes), request_durable_at=_now())
        self._fault("after_request_durable")
        return value

    def load_raw(self) -> bytes:
        raw = self.call_dir / "response.body"
        if not raw.is_file():
            raise DurableCallError("V238_DURABLE_RESPONSE_MISSING")
        return raw.read_bytes()

    def begin_transport(self) -> dict[str, Any]:
        current = self._state()
        if current["state"] == "TRANSPORT_IN_PROGRESS":
            raise DurableCallOutcomeUnknown("V238_TRANSPORT_OUTCOME_UNKNOWN")
        if current["state"] == "TRANSPORT_OUTCOME_UNKNOWN":
            raise DurableCallOutcomeUnknown("V238_TRANSPORT_OUTCOME_UNKNOWN")
        if current["state"] in TERMINAL_STATES or current["state"] == "RESPONSE_DURABLE":
            return current
        if current["state"] != "REQUEST_DURABLE":
            raise DurableCallError("V238_TRANSPORT_REQUIRES_REQUEST_DURABLE")
        value = self._transition("TRANSPORT_IN_PROGRESS", transport_started_at=_now())
        self._fault("during_transport")
        return value

    def record_response(self, raw: bytes, *, status_code: int) -> dict[str, Any]:
        self.call_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        _atomic_bytes(self.call_dir / "response.body", bytes(raw))
        _atomic_json(self.call_dir / "response_metadata.json", {
            "http_status": int(status_code), "response_bytes": len(raw),
            "response_sha256": sha256_bytes(bytes(raw)), "received_at": _now(),
        })
        state = "RESPONSE_DURABLE" if int(status_code) == 200 else ("CANCELLED_CONFIRMED" if int(status_code) == 499 else "TRANSPORT_FAILED_CONFIRMED")
        value = self._transition(state, http_status=int(status_code), response_sha256=sha256_bytes(bytes(raw)), response_bytes=len(raw), response_durable_at=_now())
        self.budget_ledger.update_attempt(self.request_id, state=value["state"], response_sha256=value.get("response_sha256"), http_status=int(status_code))
        self._fault("after_response_durable")
        return value

    def mark_unknown(self, exc: BaseException) -> dict[str, Any]:
        current = self._state()
        if current.get("state") == "TRANSPORT_OUTCOME_UNKNOWN":
            return current
        value = self._transition("TRANSPORT_OUTCOME_UNKNOWN", error=f"{type(exc).__name__}:{exc}"[:1000], outcome_unknown_at=_now())
        self.budget_ledger.update_attempt(self.request_id, state=value["state"], error=value.get("error"))
        handle = self.budget_ledger._locked()
        try:
            ledger = self.budget_ledger._read_unlocked()
            row = next((item for item in ledger["reservations"] if item.get("attempt_id") == self.request_id), None)
            if row is not None and not row.get("aggregate_accounted"):
                ledger["unknown_outcomes"] = int(ledger.get("unknown_outcomes", 0)) + 1
                row["aggregate_accounted"] = True
                ledger["updated_at"] = _now()
                _atomic_json(self.budget_ledger.path, ledger)
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()
        return value

    def mark_parsed(self, *, valid: bool, error: str | None = None) -> dict[str, Any]:
        current = self._state()
        if current.get("state") in {"PARSED_VALID", "PARSED_INVALID"}:
            return current
        if valid:
            value = self._transition("PARSED_VALID", parsed_at=_now())
        else:
            _atomic_json(self.call_dir / "parse_failure.json", {"error": error or "invalid response", "at": _now()})
            value = self._transition("PARSED_INVALID", parse_error=error or "invalid response", parsed_at=_now())
        facts = {"state": value["state"], "response_sha256": value.get("response_sha256")}
        self.budget_ledger.update_attempt(self.request_id, **facts)
        handle = self.budget_ledger._locked()
        try:
            ledger = self.budget_ledger._read_unlocked()
            row = next((item for item in ledger["reservations"] if item.get("attempt_id") == self.request_id), None)
            if row is not None and not row.get("aggregate_accounted"):
                if value["state"] == "PARSED_VALID":
                    ledger["successful_durable_responses"] = int(ledger.get("successful_durable_responses", 0)) + 1
                else:
                    ledger["invalid_responses"] = int(ledger.get("invalid_responses", 0)) + 1
                row["aggregate_accounted"] = True
                ledger["updated_at"] = _now()
                _atomic_json(self.budget_ledger.path, ledger)
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()
        return value


__all__ = ["DurableCallError", "DurableCallOutcomeUnknown", "DurableCallFault", "DurableV226Call", "EpisodeBudgetLedger", "STATES"]
