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
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping


STATE_TRANSITIONS = {
    "PLANNED": frozenset({"RESERVED", "RESERVATION_FAILED"}),
    "RESERVED": frozenset({"REQUEST_DURABLE", "RESERVATION_FAILED"}),
    "REQUEST_DURABLE": frozenset({"TRANSPORT_CLAIMED"}),
    "TRANSPORT_CLAIMED": frozenset({"TRANSPORT_IN_PROGRESS", "TRANSPORT_OUTCOME_UNKNOWN", "CORRUPT_CAPTURE"}),
    "TRANSPORT_IN_PROGRESS": frozenset({
        "RESPONSE_DURABLE", "CANCELLED_CONFIRMED", "TRANSPORT_FAILED_CONFIRMED",
        "TRANSPORT_OUTCOME_UNKNOWN", "CORRUPT_CAPTURE",
    }),
    "RESPONSE_DURABLE": frozenset({"PARSED_VALID", "PARSED_INVALID"}),
    "PARSED_VALID": frozenset(),
    "PARSED_INVALID": frozenset({"DERIVED_NORMALIZATION_RECORDED", "VALID_SUBSET_RECORDED"}),
    "VALID_SUBSET_RECORDED": frozenset({"RETRIES_PENDING", "BATCH_COMPLETE"}),
    "RETRIES_PENDING": frozenset({"RETRY_RESPONSE_DURABLE", "BATCH_COMPLETE"}),
    "RETRY_RESPONSE_DURABLE": frozenset({"RETRIES_PENDING", "BATCH_COMPLETE"}),
    "DERIVED_NORMALIZATION_RECORDED": frozenset({"DERIVED_PARSED_VALID"}),
    "DERIVED_PARSED_VALID": frozenset(),
    "BATCH_COMPLETE": frozenset(),
    "CANCELLED_CONFIRMED": frozenset(),
    "TRANSPORT_FAILED_CONFIRMED": frozenset(),
    "TRANSPORT_OUTCOME_UNKNOWN": frozenset(),
    "CORRUPT_CAPTURE": frozenset(),
    "RESERVATION_FAILED": frozenset(),
}
STATES = frozenset(STATE_TRANSITIONS)
TERMINAL_STATES = frozenset({
    "PARSED_VALID", "PARSED_INVALID", "DERIVED_NORMALIZATION_RECORDED", "DERIVED_PARSED_VALID", "VALID_SUBSET_RECORDED", "BATCH_COMPLETE", "CANCELLED_CONFIRMED",
    "TRANSPORT_FAILED_CONFIRMED", "TRANSPORT_OUTCOME_UNKNOWN",
    "CORRUPT_CAPTURE", "RESERVATION_FAILED",
})
ATTEMPT_TYPES = frozenset({"INITIAL", "RETRY"})
TERMINAL_RESERVATION_STATES = frozenset({"RESERVATION_FAILED", "RESERVATION_RELEASED"})
FAMILY_CONTRACT_FIELDS = (
    "anime_series_id", "episode_id", "source_sha256", "pipeline_id", "stage_id",
    "model_tag", "model_digest", "prompt_schema_hash", "glossary_hash",
    "configuration_hash", "candidate_execution_contract",
)


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
        os.close(fd)
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


def _family_contract(context: Mapping[str, Any]) -> dict[str, str]:
    aliases = {
        "model_tag": context.get("model_tag") or context.get("model"),
        "candidate_execution_contract": context.get("candidate_execution_contract") or context.get("candidate_commit"),
    }
    contract: dict[str, str] = {}
    for key in FAMILY_CONTRACT_FIELDS:
        value = aliases.get(key, context.get(key))
        if value in (None, ""):
            raise DurableCallError("V238_EPISODE_FAMILY_CONTRACT_MISSING:" + key)
        contract[key] = str(value)
    contract_hash = sha256_bytes(canonical_bytes(contract))
    requested_family_id = context.get("episode_family_id")
    contract["episode_family_id"] = str(requested_family_id or ("v238-family-" + contract_hash[:24]))
    contract["family_contract_sha256"] = contract_hash
    return contract


class EpisodeBudgetLedger:
    """Small locked JSON ledger shared by all calls of one episode family."""

    def __init__(self, path: str | Path, *, family_contract: Mapping[str, Any], limits: Mapping[str, Any]):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self.family_contract = dict(family_contract)
        self.episode_id = str(self.family_contract["episode_id"])
        self.family_contract_sha256 = str(self.family_contract["family_contract_sha256"])
        self.limits = {
            "planned_initial_calls": int(limits.get("planned_initial_calls", 0) or 0),
            "retry_reserve": int(limits.get("retry_reserve", 0) or 0),
            "physical_ceiling": int(limits.get("physical_ceiling", 0) or 0),
            "operation_retry_transport_cap": (None if limits.get("operation_retry_transport_cap") is None else int(limits["operation_retry_transport_cap"])),
            "per_event_retry_transport_cap": (None if limits.get("per_event_retry_transport_cap") is None else int(limits["per_event_retry_transport_cap"])),
        }

    def _initial(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "episode_family_id": self.family_contract["episode_family_id"],
            "family_contract": self.family_contract,
            "family_contract_sha256": self.family_contract_sha256,
            **self.limits,
            "initial_consumed": 0,
            "retry_consumed": 0,
            "successful_durable_responses": 0,
            "invalid_responses": 0,
            "cancelled_confirmed": 0,
            "transport_failures_confirmed": 0,
            "unknown_outcomes": 0,
            "logical_calls": {},
            "reservations": [],
            "updated_at": _now(),
        }

    def _read_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._initial()
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if str(value.get("episode_id")) != self.episode_id:
            raise DurableCallError("V238_EPISODE_BUDGET_IDENTITY_MISMATCH")
        if value.get("family_contract") != self.family_contract or str(value.get("family_contract_sha256")) != self.family_contract_sha256:
            raise DurableCallError("V238_EPISODE_FAMILY_CONTRACT_MISMATCH")
        for key, expected in self.limits.items():
            actual = value.get(key, expected)
            if expected is None:
                if actual is not None:
                    raise DurableCallError("V238_EPISODE_BUDGET_LIMIT_MISMATCH")
            elif int(actual) != expected:
                raise DurableCallError("V238_EPISODE_BUDGET_LIMIT_MISMATCH")
        return value

    def _locked(self):
        self.lock_path.touch(mode=0o600, exist_ok=True)
        os.chmod(self.lock_path, 0o600)
        handle = self.lock_path.open("r+")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        return handle

    def reservation(self, attempt_id: str) -> dict[str, Any] | None:
        handle = self._locked()
        try:
            value = self._read_unlocked()
            row = next((item for item in value["reservations"] if item.get("physical_attempt_id") == attempt_id), None)
            return dict(row) if row is not None else None
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    def reserve(self, *, logical_call_id: str, physical_attempt_id: str,
                logical_batch_id: str, request_payload_sha256: str,
                model_tag: str, model_digest: str | None, phase: str,
                attempt_type: str, attempt_ordinal: int,
                parent_attempt_id: str | None, unresolved_ids: list[int] | None = None) -> dict[str, Any]:
        if attempt_type not in ATTEMPT_TYPES:
            raise DurableCallError("V238_DURABLE_ATTEMPT_TYPE_INVALID")
        if attempt_type == "INITIAL" and parent_attempt_id:
            raise DurableCallError("V238_INITIAL_ATTEMPT_HAS_PARENT")
        if attempt_type == "INITIAL" and int(attempt_ordinal) != 1:
            raise DurableCallError("V238_INITIAL_ATTEMPT_ORDINAL_MUST_BE_ONE")
        if attempt_type == "RETRY" and not parent_attempt_id:
            raise DurableCallError("V238_RETRY_PARENT_ATTEMPT_REQUIRED")
        if attempt_type == "RETRY" and int(attempt_ordinal) <= 1:
            raise DurableCallError("V238_RETRY_ATTEMPT_ORDINAL_INVALID")
        handle = self._locked()
        try:
            value = self._read_unlocked()
            for row in value["reservations"]:
                if row.get("physical_attempt_id") == physical_attempt_id:
                    if row.get("state") in TERMINAL_RESERVATION_STATES:
                        raise DurableCallError("V238_TERMINAL_RESERVATION_REPLAY_BLOCKED:" + str(row.get("state")))
                    expected = {
                        "logical_call_id": logical_call_id,
                        "logical_batch_id": logical_batch_id,
                        "request_payload_sha256": request_payload_sha256,
                        "attempt_type": attempt_type,
                        "attempt_ordinal": int(attempt_ordinal),
                        "parent_attempt_id": parent_attempt_id,
                    }
                    if any(row.get(key) != item for key, item in expected.items()):
                        raise DurableCallError("V238_DURABLE_ATTEMPT_IDENTITY_MISMATCH")
                    return {**row, "_reused": True}
            logical = value.setdefault("logical_calls", {}).get(logical_batch_id)
            logical_identity = {
                "logical_call_id": logical_call_id,
                "request_payload_sha256": request_payload_sha256,
            }
            if logical is not None and logical != logical_identity:
                raise DurableCallError("V238_LOGICAL_BATCH_PAYLOAD_IDENTITY_MISMATCH")
            value["logical_calls"][logical_batch_id] = logical_identity
            consumed = int(value["initial_consumed"]) + int(value["retry_consumed"])
            cap = int(value.get("physical_ceiling", 0))
            if cap > 0 and consumed >= cap:
                raise DurableCallError("V238_EPISODE_BUDGET_EXHAUSTED_BEFORE_TRANSPORT")
            retry = attempt_type == "RETRY"
            if not retry and int(value["initial_consumed"]) >= int(value.get("planned_initial_calls", 0)):
                raise DurableCallError("V238_EPISODE_INITIAL_ALLOCATION_EXHAUSTED")
            if retry and int(value["retry_consumed"]) >= int(value.get("retry_reserve", 0)):
                raise DurableCallError("V238_EPISODE_RETRY_RESERVE_EXHAUSTED")
            unresolved = sorted({int(item) for item in (unresolved_ids or [])})
            if retry:
                operation_cap = value.get("operation_retry_transport_cap")
                active_retries = [item for item in value["reservations"] if item.get("attempt_type") == "RETRY" and item.get("state") != "RESERVATION_FAILED"]
                if operation_cap is not None and len(active_retries) >= int(operation_cap):
                    raise DurableCallError("V238_OPERATION_RETRY_TRANSPORT_CAP_REACHED")
                event_cap = value.get("per_event_retry_transport_cap")
                if event_cap is not None:
                    for event_id in unresolved:
                        used_for_event = sum(
                            event_id in {int(value) for value in item.get("unresolved_ids", [])}
                            for item in active_retries
                        )
                        if used_for_event >= int(event_cap):
                            raise DurableCallError("V238_PER_EVENT_RETRY_TRANSPORT_CAP_REACHED")
            if retry:
                parent = next((item for item in value["reservations"] if item.get("physical_attempt_id") == parent_attempt_id), None)
                if parent is None:
                    raise DurableCallError("V238_RETRY_PARENT_ATTEMPT_NOT_FOUND")
                if int(attempt_ordinal) <= int(parent.get("attempt_ordinal", 0)):
                    raise DurableCallError("V238_RETRY_ATTEMPT_ORDINAL_INVALID")
            row = {
                "attempt_id": physical_attempt_id,
                "physical_attempt_id": physical_attempt_id,
                "logical_call_id": logical_call_id,
                "logical_batch_id": logical_batch_id,
                "ordinal": consumed + 1,
                "attempt_type": attempt_type,
                "attempt_ordinal": int(attempt_ordinal),
                "parent_attempt_id": parent_attempt_id,
                "phase": phase,
                "model_tag": model_tag,
                "model_digest": model_digest,
                "request_payload_sha256": request_payload_sha256,
                "reserved_at": _now(),
                "state": "RESERVED",
                "operation_budget_state": "PENDING",
                "unresolved_ids": unresolved,
            }
            value["reservations"].append(row)
            value["retry_consumed" if retry else "initial_consumed"] += 1
            value["updated_at"] = _now()
            _atomic_json(self.path, value)
            return {**row, "_reused": False}
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    def reject_reservation(self, attempt_id: str, *, reason: str) -> None:
        handle = self._locked()
        try:
            value = self._read_unlocked()
            for row in value["reservations"]:
                if row.get("physical_attempt_id") == attempt_id and row.get("state") == "RESERVED":
                    row["state"] = "RESERVATION_FAILED"
                    row["reservation_failure_reason"] = str(reason)
                    if row.get("attempt_type") == "INITIAL":
                        value["initial_consumed"] = max(0, int(value["initial_consumed"]) - 1)
                    else:
                        value["retry_consumed"] = max(0, int(value["retry_consumed"]) - 1)
                    value["updated_at"] = _now()
                    _atomic_json(self.path, value)
                    return
                if row.get("physical_attempt_id") == attempt_id and row.get("state") == "RESERVATION_FAILED":
                    return
            raise DurableCallError("V238_BUDGET_ATTEMPT_NOT_FOUND")
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    def assert_retry_cap(self, *, unresolved_ids: list[int] | None = None, attempt_id: str | None = None) -> None:
        """Re-check retry caps from the persistent ledger before transport."""
        handle = self._locked()
        try:
            value = self._read_unlocked()
            row = next((item for item in value["reservations"] if item.get("physical_attempt_id") == attempt_id), None) if attempt_id else None
            if row is None or row.get("attempt_type") != "RETRY":
                return
            active = [item for item in value["reservations"] if item.get("attempt_type") == "RETRY" and item.get("state") != "RESERVATION_FAILED"]
            operation_cap = value.get("operation_retry_transport_cap")
            if operation_cap is not None and len(active) > int(operation_cap):
                raise DurableCallError("V238_OPERATION_RETRY_TRANSPORT_CAP_REACHED")
            event_cap = value.get("per_event_retry_transport_cap")
            for event_id in sorted({int(item) for item in (unresolved_ids or row.get("unresolved_ids", []))}):
                if event_cap is not None and sum(event_id in {int(item) for item in row2.get("unresolved_ids", [])} for row2 in active) > int(event_cap):
                    raise DurableCallError("V238_PER_EVENT_RETRY_TRANSPORT_CAP_REACHED")
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    def update_attempt(self, attempt_id: str, **facts: Any) -> None:
        handle = self._locked()
        try:
            value = self._read_unlocked()
            for row in value["reservations"]:
                if row.get("physical_attempt_id") == attempt_id:
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
            if int(value.get("initial_consumed", 0)) > int(value.get("planned_initial_calls", 0)):
                raise DurableCallError("V238_EPISODE_INITIAL_INVARIANT_VIOLATION")
            if int(value.get("retry_consumed", 0)) > int(value.get("retry_reserve", 0)):
                raise DurableCallError("V238_EPISODE_RETRY_INVARIANT_VIOLATION")
            if cap > 0 and consumed > cap:
                raise DurableCallError("V238_EPISODE_PHYSICAL_INVARIANT_VIOLATION")
            return value
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()


class DurableV226Call:
    """Durable request/response state machine for one physical V226 call."""

    def __init__(self, context: Mapping[str, Any], payload: Mapping[str, Any], metadata: Mapping[str, Any],
                 *, operation_budget: Any = None):
        required = ("operation_id", "episode_id", "source_sha256", "model", "model_digest", "durable_call_root", "episode_budget_ledger_path")
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
        attempt_type = str(metadata.get("attempt_type") or "").upper()
        if attempt_type not in ATTEMPT_TYPES:
            raise DurableCallError("V238_DURABLE_ATTEMPT_TYPE_REQUIRED")
        logical_batch_id = str(metadata.get("logical_batch_id") or "")
        if not logical_batch_id:
            raise DurableCallError("V238_DURABLE_LOGICAL_BATCH_ID_REQUIRED")
        parent_attempt_id = metadata.get("parent_attempt_id")
        if attempt_type == "RETRY" and not parent_attempt_id:
            raise DurableCallError("V238_RETRY_PARENT_ATTEMPT_REQUIRED")
        if attempt_type == "INITIAL" and parent_attempt_id:
            raise DurableCallError("V238_INITIAL_ATTEMPT_HAS_PARENT")
        attempt_ordinal = int(metadata.get("attempt_ordinal", 1) or 1)
        if attempt_type == "INITIAL" and attempt_ordinal != 1:
            raise DurableCallError("V238_INITIAL_ATTEMPT_ORDINAL_MUST_BE_ONE")
        if attempt_type == "RETRY" and attempt_ordinal <= 1:
            raise DurableCallError("V238_RETRY_ATTEMPT_ORDINAL_INVALID")
        self.family_contract = _family_contract(context)
        logical_identity = {
            "family_contract_sha256": self.family_contract["family_contract_sha256"],
            "logical_batch_id": logical_batch_id,
            "unit_membership_sha256": str(metadata.get("unit_membership_sha256", "")),
            "model_tag": str(context["model"]),
            "model_digest": str(context["model_digest"]),
        }
        self.logical_call_id = "v226-logical-" + hashlib.sha256(canonical_bytes(logical_identity)).hexdigest()[:32]
        physical_identity = {
            "logical_call_id": self.logical_call_id,
            "attempt_type": attempt_type,
            "attempt_ordinal": attempt_ordinal,
            "parent_attempt_id": parent_attempt_id,
            "request_payload_sha256": self.payload_sha256,
        }
        self.physical_attempt_id = "v226-attempt-" + hashlib.sha256(canonical_bytes(physical_identity)).hexdigest()[:32]
        identity = {
            **logical_identity,
            **physical_identity,
            "physical_attempt_id": self.physical_attempt_id,
        }
        self.identity = identity
        # Compatibility name: request_id now denotes the stable physical
        # attempt identity and deliberately excludes the transient operation.
        self.request_id = self.physical_attempt_id
        budget_path = Path(context["episode_budget_ledger_path"])
        family_root = Path(context.get("episode_family_root") or budget_path.parent)
        root = family_root
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(root, 0o700)
        self.call_dir = root / "calls" / self.request_id
        self.state_path = self.call_dir / "state.json"
        self.transport_lock_path = self.call_dir / "transport.lock"
        self._transport_claim_handle = None
        limits = context.get("episode_budget_limits") or {
            "planned_initial_calls": context.get("planned_initial_calls", 0),
            "retry_reserve": context.get("retry_reserve", 0),
            "physical_ceiling": context.get("physical_ceiling", context.get("qwen_physical_maximum", 0)),
            "operation_retry_transport_cap": context.get("operation_retry_transport_cap"),
            "per_event_retry_transport_cap": context.get("per_event_retry_transport_cap"),
        }
        if "operation_retry_transport_cap" not in limits:
            limits = {**limits, "operation_retry_transport_cap": context.get("operation_retry_transport_cap")}
        if "per_event_retry_transport_cap" not in limits:
            limits = {**limits, "per_event_retry_transport_cap": context.get("per_event_retry_transport_cap")}
        self.budget_ledger = EpisodeBudgetLedger(budget_path, family_contract=self.family_contract, limits=limits)
        self._fault_point = str(context.get("durability_fault_point") or "")

    def _state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"request_id": self.request_id, "logical_call_id": self.logical_call_id,
                    "physical_attempt_id": self.physical_attempt_id, "state": "PLANNED",
                    "identity": self.identity, "operation_ids": [str(self.context["operation_id"])], "history": []}
        value = json.loads(self.state_path.read_text(encoding="utf-8"))
        if value.get("request_id") != self.request_id or value.get("identity") != self.identity:
            raise DurableCallError("V238_DURABLE_CALL_IDENTITY_MISMATCH")
        if value.get("state") not in STATES:
            raise DurableCallError("V238_DURABLE_CALL_STATE_CORRUPT")
        operation_ids = value.setdefault("operation_ids", [])
        if str(self.context["operation_id"]) not in operation_ids:
            operation_ids.append(str(self.context["operation_id"]))
            _atomic_json(self.state_path, value)
        self._reconcile_alias(value)
        return value

    def state(self) -> str:
        return str(self._state().get("state"))

    def _transition(self, state: str, **facts: Any) -> dict[str, Any]:
        if state not in STATES:
            raise DurableCallError("V238_DURABLE_CALL_UNKNOWN_STATE")
        value = self._state()
        current = str(value["state"])
        if state == current:
            # Re-observing an already durable state is idempotent; it is not a
            # graph transition and must not append conflicting history.
            return value
        if state not in STATE_TRANSITIONS[current]:
            raise DurableCallError(f"V238_DURABLE_STATE_TRANSITION_PROHIBITED:{current}->{state}")
        value["state"] = state
        value.update(facts)
        value.setdefault("history", []).append({"state": state, "at": _now()})
        _atomic_json(self.state_path, value)
        self._fault("after_state_before_alias")
        self._write_alias(value)
        return value

    def _alias_value(self, value: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "authority": "state.json",
            "authority_sha256": sha256_bytes(canonical_bytes(value)),
            "state": value.get("state"),
            "request_id": self.request_id,
            "derived_alias": True,
        }

    def _write_alias(self, value: Mapping[str, Any]) -> None:
        _atomic_json(self.call_dir / "capture_state.json", self._alias_value(value))

    def _reconcile_alias(self, value: Mapping[str, Any]) -> None:
        alias_path = self.call_dir / "capture_state.json"
        expected = self._alias_value(value)
        try:
            current = json.loads(alias_path.read_text(encoding="utf-8")) if alias_path.is_file() else None
        except (OSError, ValueError, json.JSONDecodeError):
            current = None
        if current != expected:
            self._write_alias(value)

    def _fault(self, point: str) -> None:
        if self._fault_point == point:
            raise DurableCallFault("V238_DURABILITY_FAULT:" + point)

    def reserve(self) -> dict[str, Any]:
        self._fault("before_reservation")
        current = self._state()
        if current["state"] == "RESERVATION_FAILED":
            raise DurableCallError("V238_RESERVATION_FAILURE_IS_TERMINAL")
        if current["state"] == "RESERVED":
            return current
        if current["state"] in TERMINAL_STATES:
            return current
        if current["state"] in {"REQUEST_DURABLE", "TRANSPORT_CLAIMED", "TRANSPORT_IN_PROGRESS", "RESPONSE_DURABLE", "PARSED_VALID", "PARSED_INVALID", "TRANSPORT_OUTCOME_UNKNOWN", "CORRUPT_CAPTURE"}:
            return current
        attempt_type = str(self.metadata["attempt_type"]).upper()
        reservation = self.budget_ledger.reserve(
            logical_call_id=self.logical_call_id,
            physical_attempt_id=self.physical_attempt_id,
            logical_batch_id=str(self.metadata["logical_batch_id"]),
            request_payload_sha256=self.payload_sha256,
            model_tag=str(self.context["model"]),
            model_digest=str(self.context["model_digest"]),
            phase=str(self.metadata.get("phase", "initial")),
            attempt_type=attempt_type,
            attempt_ordinal=int(self.metadata.get("attempt_ordinal", 1)),
            parent_attempt_id=self.metadata.get("parent_attempt_id"),
            unresolved_ids=[int(item) for item in self.metadata.get("unit_ids", [])],
        )
        self._fault("after_ledger_reserve")
        operation_budget_accepted_now = False
        try:
            if self.operation_budget is not None and reservation.get("operation_budget_state") != "ACCEPTED":
                try:
                    self.operation_budget.reserve(
                        model_tag=str(self.context["model"]), model_digest=str(self.context["model_digest"]),
                        phase="V226_QWEN", reservation_id=self.physical_attempt_id,
                    )
                except TypeError:
                    self.operation_budget.reserve(model_tag=str(self.context["model"]), model_digest=str(self.context["model_digest"]), phase="V226_QWEN")
                self.budget_ledger.update_attempt(self.request_id, operation_budget_state="ACCEPTED")
                operation_budget_accepted_now = True
        except Exception as exc:
            self.budget_ledger.reject_reservation(self.request_id, reason=f"{type(exc).__name__}:{exc}"[:500])
            self.call_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(self.call_dir, 0o700)
            self._transition("RESERVATION_FAILED", error="operation_budget_rejected")
            raise
        if operation_budget_accepted_now:
            self._fault("after_operation_budget_reserve")
        self.call_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.call_dir, 0o700)
        value = self._transition("RESERVED", reservation=reservation, reserved_at=_now())
        self._fault("after_reservation")
        return value

    def prepare_request(self) -> dict[str, Any]:
        current = self.reserve()
        if current["state"] in {"TRANSPORT_CLAIMED", "TRANSPORT_IN_PROGRESS"}:
            return self._reconcile_abandoned_transport()
        if current["state"] in {"REQUEST_DURABLE", "RESPONSE_DURABLE"}:
            return current
        if current["state"] == "TRANSPORT_OUTCOME_UNKNOWN":
            raise DurableCallOutcomeUnknown("V238_TRANSPORT_OUTCOME_UNKNOWN")
        if current["state"] == "CORRUPT_CAPTURE":
            raise DurableCallError("V238_DURABLE_RESPONSE_CAPTURE_CORRUPT")
        if current["state"] in TERMINAL_STATES:
            return current
        if current["state"] != "RESERVED":
            raise DurableCallError("V238_REQUEST_PREPARATION_REQUIRES_RESERVED")
        _atomic_bytes(self.call_dir / "request_payload.json", self.payload_bytes)
        self._fault("after_request_body_fsync")
        _atomic_json(self.call_dir / "request_metadata.json", {**self.metadata, "request_id": self.request_id, "identity": self.identity, "request_sha256": self.payload_sha256})
        value = self._transition("REQUEST_DURABLE", request_path="request_payload.json", request_sha256=self.payload_sha256, request_bytes=len(self.payload_bytes), request_durable_at=_now())
        self._fault("after_request_durable")
        return value

    def load_raw(self) -> bytes:
        raw = self.call_dir / "response.body"
        if not raw.is_file():
            raise DurableCallError("V238_DURABLE_RESPONSE_MISSING")
        return raw.read_bytes()

    def _open_transport_lock(self):
        self.call_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.call_dir, 0o700)
        self.transport_lock_path.touch(mode=0o600, exist_ok=True)
        os.chmod(self.transport_lock_path, 0o600)
        return self.transport_lock_path.open("r+")

    def _acquire_transport_lock(self, *, timeout_seconds: float) -> Any:
        handle = self._open_transport_lock()
        deadline = time.monotonic() + max(0.0, float(timeout_seconds))
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return handle
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    handle.close()
                    raise DurableCallError("V238_EXCLUSIVE_TRANSPORT_CLAIM_BUSY")
                time.sleep(0.01)

    @staticmethod
    def _release_transport_lock(handle: Any) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()

    def _capture_facts(self) -> tuple[str, dict[str, Any] | None]:
        body_path = self.call_dir / "response.body"
        metadata_path = self.call_dir / "response_metadata.json"
        body_exists = body_path.is_file()
        metadata_exists = metadata_path.is_file()
        if not body_exists and not metadata_exists:
            return "ABSENT", None
        if not body_exists or not metadata_exists:
            return "INCOMPLETE", None
        try:
            raw = body_path.read_bytes()
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            status_code = int(metadata["http_status"])
            expected_bytes = int(metadata["response_bytes"])
            expected_sha = str(metadata["response_sha256"])
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            return "CORRUPT", None
        actual_sha = sha256_bytes(raw)
        if expected_bytes != len(raw) or expected_sha != actual_sha:
            return "CORRUPT", None
        return "COMPLETE", {
            "http_status": status_code,
            "response_bytes": len(raw),
            "response_sha256": actual_sha,
            "response_durable_at": metadata.get("received_at") or _now(),
        }

    def _promote_capture(self, facts: Mapping[str, Any]) -> dict[str, Any]:
        status_code = int(facts["http_status"])
        state = "RESPONSE_DURABLE" if status_code == 200 else (
            "CANCELLED_CONFIRMED" if status_code == 499 else "TRANSPORT_FAILED_CONFIRMED"
        )
        value = self._transition(state, **dict(facts), capture_reconciled=True)
        self.budget_ledger.update_attempt(
            self.request_id, state=value["state"], response_sha256=value.get("response_sha256"),
            http_status=status_code,
        )
        return value

    def _reconcile_capture_locked(self, *, incomplete_to_unknown: bool) -> dict[str, Any]:
        current = self._state()
        if current["state"] not in {"TRANSPORT_CLAIMED", "TRANSPORT_IN_PROGRESS"}:
            return current
        capture_status, facts = self._capture_facts()
        if capture_status == "COMPLETE" and facts is not None:
            return self._promote_capture(facts)
        if capture_status == "CORRUPT":
            value = self._transition("CORRUPT_CAPTURE", capture_reconciliation="BODY_METADATA_MISMATCH")
            self.budget_ledger.update_attempt(self.request_id, state=value["state"])
            raise DurableCallError("V238_DURABLE_RESPONSE_CAPTURE_CORRUPT")
        if incomplete_to_unknown:
            return self.mark_unknown(DurableCallOutcomeUnknown(
                "V238_TRANSPORT_CAPTURE_INCOMPLETE" if capture_status == "INCOMPLETE" else "V238_TRANSPORT_OWNER_TERMINATED"
            ))
        return current

    def _reconcile_abandoned_transport(self) -> dict[str, Any]:
        # A live owner keeps this lock across state validation, POST, response
        # capture, and durable promotion.  Only an abandoned owner permits a
        # restart to classify an incomplete in-flight state as unknown.
        handle = self._acquire_transport_lock(timeout_seconds=0.0)
        try:
            value = self._reconcile_capture_locked(incomplete_to_unknown=True)
            if value.get("state") == "TRANSPORT_OUTCOME_UNKNOWN":
                raise DurableCallOutcomeUnknown("V238_TRANSPORT_OUTCOME_UNKNOWN")
            return value
        finally:
            self._release_transport_lock(handle)

    @contextmanager
    def exclusive_transport_claim(self):
        timeout_seconds = float(self.context.get("transport_claim_timeout_seconds", 5.0))
        handle = self._acquire_transport_lock(timeout_seconds=timeout_seconds)
        self._transport_claim_handle = handle
        owner = False
        try:
            current = self._reconcile_capture_locked(incomplete_to_unknown=True)
            if current["state"] in {"RESPONSE_DURABLE", "PARSED_VALID", "PARSED_INVALID"}:
                yield False
                return
            if current["state"] in TERMINAL_STATES:
                raise DurableCallError("V238_DURABLE_CALL_TERMINAL_RECONCILIATION_REQUIRED:" + str(current["state"]))
            if current["state"] != "REQUEST_DURABLE":
                raise DurableCallError("V238_TRANSPORT_CLAIM_REQUIRES_REQUEST_DURABLE")
            claim_token = sha256_bytes(canonical_bytes({
                "physical_attempt_id": self.physical_attempt_id,
                "operation_id": str(self.context["operation_id"]),
                "pid": os.getpid(),
            }))
            value = self._transition(
                "TRANSPORT_CLAIMED", transport_claim_token=claim_token,
                transport_claimed_by_pid=os.getpid(), transport_claimed_at=_now(),
            )
            self.budget_ledger.update_attempt(self.request_id, state=value["state"])
            owner = True
            try:
                yield True
            except BaseException as exc:
                current_state = self.state()
                if current_state in {"TRANSPORT_CLAIMED", "TRANSPORT_IN_PROGRESS"}:
                    reconciled = self._reconcile_capture_locked(incomplete_to_unknown=False)
                    if reconciled.get("state") in {"TRANSPORT_CLAIMED", "TRANSPORT_IN_PROGRESS"}:
                        self.mark_unknown(exc)
                raise
        finally:
            self._transport_claim_handle = None
            self._release_transport_lock(handle)

    def begin_transport(self) -> dict[str, Any]:
        current = self._state()
        if current["state"] == "TRANSPORT_IN_PROGRESS":
            raise DurableCallOutcomeUnknown("V238_TRANSPORT_OUTCOME_UNKNOWN")
        if current["state"] == "TRANSPORT_OUTCOME_UNKNOWN":
            raise DurableCallOutcomeUnknown("V238_TRANSPORT_OUTCOME_UNKNOWN")
        if current["state"] in TERMINAL_STATES or current["state"] == "RESPONSE_DURABLE":
            return current
        if self._transport_claim_handle is None:
            raise DurableCallError("V238_TRANSPORT_REQUIRES_EXCLUSIVE_CLAIM")
        if current["state"] != "TRANSPORT_CLAIMED":
            raise DurableCallError("V238_TRANSPORT_REQUIRES_CLAIMED_STATE")
        self.budget_ledger.assert_retry_cap(unresolved_ids=[int(item) for item in self.metadata.get("unit_ids", [])], attempt_id=self.request_id)
        value = self._transition("TRANSPORT_IN_PROGRESS", transport_started_at=_now())
        self.budget_ledger.update_attempt(self.request_id, state=value["state"])
        self._fault("during_transport")
        return value

    def record_response(self, raw: bytes, *, status_code: int) -> dict[str, Any]:
        if self._transport_claim_handle is None:
            raise DurableCallError("V238_RESPONSE_CAPTURE_REQUIRES_EXCLUSIVE_CLAIM")
        if self.state() != "TRANSPORT_IN_PROGRESS":
            raise DurableCallError("V238_RESPONSE_CAPTURE_REQUIRES_TRANSPORT_IN_PROGRESS")
        self.call_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        _atomic_bytes(self.call_dir / "response.body", bytes(raw))
        self._fault("after_response_body_fsync")
        _atomic_json(self.call_dir / "response_metadata.json", {
            "http_status": int(status_code), "response_bytes": len(raw),
            "response_sha256": sha256_bytes(bytes(raw)), "received_at": _now(),
        })
        self._fault("after_response_metadata_fsync")
        state = "RESPONSE_DURABLE" if int(status_code) == 200 else ("CANCELLED_CONFIRMED" if int(status_code) == 499 else "TRANSPORT_FAILED_CONFIRMED")
        value = self._transition(state, http_status=int(status_code), response_sha256=sha256_bytes(bytes(raw)), response_bytes=len(raw), response_durable_at=_now())
        self.budget_ledger.update_attempt(self.request_id, state=value["state"], response_sha256=value.get("response_sha256"), http_status=int(status_code))
        self._fault("after_response_durable")
        return value

    def mark_unknown(self, exc: BaseException) -> dict[str, Any]:
        current = self._state()
        if current.get("state") == "TRANSPORT_OUTCOME_UNKNOWN":
            return current
        if current.get("state") not in {"TRANSPORT_CLAIMED", "TRANSPORT_IN_PROGRESS"}:
            raise DurableCallError("V238_UNKNOWN_OUTCOME_REQUIRES_ACTIVE_TRANSPORT_STATE")
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
        self._fault("after_parse")
        return value

    def record_valid_subset(self, found: Mapping[int, Mapping[str, Any]], expected_ids: list[int],
                            unresolved_ids: list[int], *, validator_version: str = "canonical-v226") -> dict[str, Any]:
        """Persist canonical-valid rows without declaring the batch complete."""
        current = self._state()
        if current.get("state") in {"VALID_SUBSET_RECORDED", "RETRIES_PENDING", "RETRY_RESPONSE_DURABLE", "BATCH_COMPLETE"}:
            return self.load_valid_subset()
        if current.get("state") != "PARSED_INVALID":
            raise DurableCallError("V238_VALID_SUBSET_REQUIRES_PARSED_INVALID")
        expected = sorted({int(item) for item in expected_ids})
        valid_ids = sorted({int(item) for item in found})
        unresolved = sorted({int(item) for item in unresolved_ids})
        if set(valid_ids) & set(unresolved) or set(valid_ids) | set(unresolved) != set(expected):
            raise DurableCallError("V238_VALID_SUBSET_ID_SET_INVALID")
        rows = [dict(found[item_id]) for item_id in valid_ids]
        subset = {"expected_ids": expected, "valid_ids": valid_ids, "unresolved_ids": unresolved,
                  "rows": rows, "source_response_sha256": current.get("response_sha256"),
                  "validator_version": validator_version}
        subset_sha = sha256_bytes(canonical_bytes(subset))
        manifest = {"kind": "V238_VALID_SUBSET_V1", "subset_sha256": subset_sha,
                    "source_response_sha256": current.get("response_sha256"),
                    "expected_ids_sha256": sha256_bytes(canonical_bytes(expected)),
                    "valid_ids_sha256": sha256_bytes(canonical_bytes(valid_ids)),
                    "unresolved_ids_sha256": sha256_bytes(canonical_bytes(unresolved)),
                    "logical_call_id": self.logical_call_id, "physical_attempt_id": self.physical_attempt_id,
                    "family_id": self.family_contract.get("episode_family_id"),
                    "operation_id": str(self.context["operation_id"]),
                    "candidate_commit": self.context.get("candidate_commit"),
                    "identity": self.identity, "created_at": _now()}
        manifest["manifest_sha256"] = sha256_bytes(canonical_bytes(manifest))
        _atomic_json(self.call_dir / "valid_subset.json", subset)
        self._fault("after_subset_body")
        _atomic_json(self.call_dir / "valid_subset_manifest.json", manifest)
        self._fault("after_subset_manifest")
        value = self._transition("VALID_SUBSET_RECORDED", valid_subset_sha256=subset_sha,
                                 valid_subset_manifest_sha256=manifest["manifest_sha256"], valid_subset_recorded_at=_now())
        self._fault("after_subset_state")
        return {"state": value.get("state"), "subset": subset, "manifest": manifest}

    def load_valid_subset(self) -> dict[str, Any]:
        subset_path = self.call_dir / "valid_subset.json"
        manifest_path = self.call_dir / "valid_subset_manifest.json"
        if not subset_path.is_file() or not manifest_path.is_file():
            raise DurableCallError("V238_VALID_SUBSET_CAPTURE_MISSING")
        try:
            subset = json.loads(subset_path.read_text(encoding="utf-8"))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            state = self._state()
            if sha256_bytes(canonical_bytes(subset)) != manifest.get("subset_sha256"):
                raise DurableCallError("V238_VALID_SUBSET_HASH_MISMATCH")
            if manifest.get("subset_sha256") != state.get("valid_subset_sha256") or manifest.get("manifest_sha256") != state.get("valid_subset_manifest_sha256"):
                raise DurableCallError("V238_VALID_SUBSET_STATE_HASH_MISMATCH")
            if manifest.get("source_response_sha256") != state.get("response_sha256"):
                raise DurableCallError("V238_VALID_SUBSET_SOURCE_HASH_MISMATCH")
            if manifest.get("identity") != self.identity or manifest.get("operation_id") != str(self.context["operation_id"]):
                raise DurableCallError("V238_VALID_SUBSET_IDENTITY_MISMATCH")
            if manifest.get("candidate_commit") != self.context.get("candidate_commit"):
                raise DurableCallError("V238_VALID_SUBSET_CANDIDATE_MISMATCH")
            expected = set(subset.get("expected_ids", [])); valid = set(subset.get("valid_ids", [])); unresolved = set(subset.get("unresolved_ids", []))
            if valid & unresolved or valid | unresolved != expected:
                raise DurableCallError("V238_VALID_SUBSET_ID_SET_INVALID")
            for key, ids in (("expected_ids", subset.get("expected_ids", [])), ("valid_ids", subset.get("valid_ids", [])), ("unresolved_ids", subset.get("unresolved_ids", []))):
                if manifest.get(key + "_sha256") != sha256_bytes(canonical_bytes(ids)):
                    raise DurableCallError("V238_VALID_SUBSET_SET_HASH_MISMATCH")
            return {"state": state.get("state"), "subset": subset, "manifest": manifest}
        except DurableCallError:
            raise
        except (OSError, ValueError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
            raise DurableCallError("V238_VALID_SUBSET_CORRUPT") from exc

    def load_derived_response(self) -> bytes:
        """Load a derived response bound to state, manifest and identity.

        ``state.json`` is the authoritative promotion record.  The private
        body and manifest are only usable when their hashes agree with both
        the state record and each other; a coherent body/manifest rewrite can
        therefore not redefine a promoted derivation.
        """
        response_path = self.call_dir / "derived_response.json"
        manifest_path = self.call_dir / "derived_normalization.json"
        if not response_path.is_file() or not manifest_path.is_file():
            raise DurableCallError("V238_DERIVED_RESPONSE_MISSING")
        try:
            state = self._state()
            raw = response_path.read_bytes()
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            actual_response_sha = sha256_bytes(raw)
            manifest_response_sha = manifest.get("normalized_response_sha256")
            state_response_sha = state.get("derived_response_sha256")
            if manifest_response_sha != actual_response_sha:
                raise DurableCallError("V238_DERIVED_RESPONSE_HASH_MISMATCH")
            if state_response_sha != actual_response_sha:
                raise DurableCallError("V238_DERIVED_RESPONSE_STATE_HASH_MISMATCH")
            manifest_sha = sha256_bytes(canonical_bytes(manifest))
            if state.get("derived_manifest_sha256") != manifest_sha:
                raise DurableCallError("V238_DERIVED_MANIFEST_STATE_HASH_MISMATCH")
            if manifest.get("source_response_sha256") != state.get("response_sha256"):
                raise DurableCallError("V238_DERIVED_SOURCE_RESPONSE_MISMATCH")
            expected_policy = str(self.context.get("response_normalization_policy") or "")
            if manifest.get("policy") not in {"V238_ITEM_EXTRA_PROPERTY_PROJECTION_V1", "V238_ITEM_EXTRA_PROPERTY_PROJECTION_V2_MULTI_KIND"} or manifest.get("policy") != expected_policy:
                raise DurableCallError("V238_DERIVED_POLICY_MISMATCH")
            expected_identity = {
                "source_response_sha256": state.get("response_sha256"),
                "policy": expected_policy,
                "logical_call_id": self.logical_call_id,
                "physical_attempt_id": self.physical_attempt_id,
                "family_id": self.family_contract.get("episode_family_id"),
                "candidate_commit": self.context.get("candidate_commit"),
                "identity": self.identity,
            }
            for key, expected in expected_identity.items():
                if manifest.get(key) != expected:
                    raise DurableCallError("V238_DERIVED_IDENTITY_MISMATCH:" + key)
            json.loads(raw.decode("utf-8"))
            return raw
        except DurableCallError:
            raise
        except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
            raise DurableCallError("V238_DERIVED_RESPONSE_CORRUPT") from exc

    def record_derived_normalization(self, normalized_value: Mapping[str, Any], audit: Mapping[str, Any]) -> dict[str, Any]:
        """Persist a deterministic projection after PARSED_INVALID.

        The raw response and its PARSED_INVALID history remain untouched.  The
        derived response is private, atomically written and independently
        hash-checked on every restart.  This method deliberately stops at
        DERIVED_NORMALIZATION_RECORDED; callers must validate the derived
        response before invoking :meth:`mark_derived_parsed_valid`.
        """
        current = self._state()
        if current.get("state") == "DERIVED_PARSED_VALID":
            self.load_derived_response()
            return current
        if current.get("state") == "DERIVED_NORMALIZATION_RECORDED":
            self.load_derived_response()
            return current
        if current.get("state") != "PARSED_INVALID":
            raise DurableCallError("V238_DERIVED_NORMALIZATION_REQUIRES_PARSED_INVALID")
        policy = str(audit.get("policy") or "")
        if policy not in {"V238_ITEM_EXTRA_PROPERTY_PROJECTION_V1", "V238_ITEM_EXTRA_PROPERTY_PROJECTION_V2_MULTI_KIND"}:
            raise DurableCallError("V238_DERIVED_POLICY_INVALID")
        derived_bytes = canonical_bytes(normalized_value)
        source_sha = str(current.get("response_sha256") or "")
        if not source_sha:
            raise DurableCallError("V238_DERIVED_SOURCE_RESPONSE_SHA_MISSING")
        derived_sha = sha256_bytes(derived_bytes)
        manifest = {
            **dict(audit),
            "source_response_sha256": source_sha,
            "normalized_response_sha256": derived_sha,
            "created_at": _now(),
            "candidate_commit": self.context.get("candidate_commit"),
            "logical_call_id": self.logical_call_id,
            "physical_attempt_id": self.physical_attempt_id,
            "family_id": self.family_contract.get("episode_family_id"),
            "identity": self.identity,
        }
        self._fault("before_derived_manifest")
        _atomic_bytes(self.call_dir / "derived_response.json", derived_bytes)
        self._fault("after_derived_response")
        _atomic_json(self.call_dir / "derived_normalization.json", manifest)
        self._fault("after_derived_manifest")
        recorded = self._transition(
            "DERIVED_NORMALIZATION_RECORDED",
            derived_response_sha256=derived_sha,
            derived_manifest_sha256=sha256_bytes(canonical_bytes(manifest)),
            normalization_policy=policy,
            derived_normalization_at=_now(),
        )
        self._fault("after_derived_recorded")
        return recorded

    def mark_derived_parsed_valid(self) -> dict[str, Any]:
        """Promote a persisted projection only after canonical validation.

        The response and manifest are reloaded before the forward-only state
        transition.  Schema/cardinality/source-membership validation belongs
        to the canonical client, so a recorded projection can never become
        valid merely because its files exist.
        """
        current = self._state()
        if current.get("state") == "DERIVED_PARSED_VALID":
            self.load_derived_response()
            return current
        if current.get("state") != "DERIVED_NORMALIZATION_RECORDED":
            raise DurableCallError("V238_DERIVED_VALID_PROMOTION_REQUIRES_RECORDED_STATE")
        self.load_derived_response()
        value = self._transition("DERIVED_PARSED_VALID", derived_parsed_at=_now())
        self.budget_ledger.update_attempt(self.request_id, state=value["state"])
        self._fault("after_derived_valid")
        return value


__all__ = ["DurableCallError", "DurableCallOutcomeUnknown", "DurableCallFault", "DurableV226Call", "EpisodeBudgetLedger", "STATE_TRANSITIONS", "STATES"]
