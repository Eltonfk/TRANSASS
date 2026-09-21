"""Deterministic, advisory-only Llama fallback/reviewer policy.

This module contains no model client.  Callers inject a bounded fake/provider
and receive non-publishable candidates with complete unit-level lineage.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

ALLOWED_REASON_CODES = frozenset({
    "PRIMARY_RETRIES_EXHAUSTED", "PRIMARY_SCHEMA_REJECTED",
    "PRIMARY_VALIDATION_REJECTED", "SEMANTIC_AMBIGUITY_UNRESOLVED",
    "DETERMINISTIC_SUSPECT_FLAG",
})
FALLBACK_CANDIDATE_ONLY = "FALLBACK_CANDIDATE_ONLY"
REVIEW_VERDICTS = frozenset({"REVIEWER_NO_OBJECTION", "REVIEWER_FLAGGED", "REVIEWER_UNRESOLVED"})
LLAMA_MODEL_TAG = "llama3.1:8b"
LLAMA_MODEL_DIGEST = "46e0c10c039e019119339687c3c1757cc81b9da49709a3b3924863ba87ca666e"
DEFAULT_QWEN_PHYSICAL_MAXIMUM = 256
DEFAULT_PROVIDER_PHYSICAL_MAXIMUM = 4096


def _normalise_provider(value: str | None) -> str:
    """Return the stable provider bucket used by the operation budget."""
    provider = str(value or "").strip().casefold()
    if provider == "ollama":
        return "qwen"
    return provider


class LlamaPolicyError(RuntimeError):
    pass


class HardCallBudget:
    """Finite budget shared by one deterministic fallback phase."""

    def __init__(self, maximum: int) -> None:
        self.maximum = max(0, int(maximum))
        self.consumed = 0

    def reserve(self, count: int = 1) -> None:
        if self.consumed + int(count) > self.maximum:
            raise LlamaPolicyError("V238_LLAMA_HARD_CALL_BUDGET_EXCEEDED")
        self.consumed += int(count)


class OperationCallBudget:
    """Shared per-operation reservation ledger for every model transport.

    Older versions treated every non-Llama transport as Qwen.  That made a
    DeepSeek operation consume the local Ollama bucket and produced a false
    ``V238_SHARED_QWEN_*`` failure.  The active provider is now explicit, with
    a compatibility inference for direct legacy callers.
    """

    def __init__(
        self,
        *,
        qwen_physical_maximum: int = DEFAULT_QWEN_PHYSICAL_MAXIMUM,
        llama_generation_maximum: int = 1,
        provider_name: str | None = None,
        provider_physical_maximum: int | None = None,
        provider_physical_maximums: Mapping[str, int] | None = None,
    ) -> None:
        self.qwen_physical_maximum = max(1, int(qwen_physical_maximum))
        self.llama_generation_maximum = max(0, int(llama_generation_maximum))
        self.provider_name = _normalise_provider(provider_name)
        self.provider_physical_maximums: dict[str, int] = {
            _normalise_provider(key): max(1, int(value))
            for key, value in (provider_physical_maximums or {}).items()
            if _normalise_provider(key)
        }
        if self.provider_name and self.provider_name != "qwen":
            self.provider_physical_maximums.setdefault(
                self.provider_name,
                max(1, int(provider_physical_maximum or self.qwen_physical_maximum)),
            )
        self.total_reserved = 0
        self.qwen_reserved = 0
        self.llama_reserved = 0
        self.provider_reserved: dict[str, int] = {}
        self.reservations: list[dict[str, Any]] = []

    def _bucket(self, *, model_tag: str, phase: str, provider: str | None) -> str:
        token = str(phase or "").upper()
        # An explicit fallback phase is authoritative.  Otherwise the
        # provider identity wins: hosted NVIDIA/Groq endpoints may legitimately
        # serve a model whose name contains ``llama`` without being the local
        # canonical fallback phase.
        if "LLAMA" in token:
            return "llama"
        explicit = _normalise_provider(provider)
        if explicit:
            return explicit
        if self.provider_name:
            return self.provider_name
        # Compatibility for direct V2.2.x callers that predate provider
        # identity in the execution context.
        model = str(model_tag).casefold()
        if model.startswith("llama"):
            return "llama"
        for candidate in ("deepseek", "gemini", "groq", "qwen"):
            if candidate in model or candidate.upper() in token:
                return candidate
        return "qwen"

    def _physical_maximum(self, bucket: str) -> int:
        if bucket == "llama":
            return self.llama_generation_maximum
        if bucket == "qwen":
            return self.qwen_physical_maximum
        return self.provider_physical_maximums.get(bucket, DEFAULT_PROVIDER_PHYSICAL_MAXIMUM)

    def _reservation_error(self, bucket: str, *, model_tag: str, phase: str, reserved: int, maximum: int) -> str:
        label = "QWEN" if bucket == "qwen" else bucket.upper()
        return (
            f"V238_SHARED_{label}_PHYSICAL_CALL_BUDGET_EXCEEDED:"
            f"reserved={reserved}:maximum={maximum}:"
            f"model={str(model_tag)[:120]}:phase={phase}"
        )

    def reserve(
        self,
        *,
        model_tag: str,
        model_digest: str | None,
        phase: str,
        reservation_id: str | None = None,
        provider: str | None = None,
    ) -> dict[str, Any]:
        token = str(phase or "").upper()
        bucket = self._bucket(model_tag=model_tag, phase=token, provider=provider)
        if reservation_id:
            existing = next((row for row in self.reservations if row.get("reservation_id") == str(reservation_id)), None)
            if existing is not None:
                expected = (str(model_tag), model_digest, token, bucket)
                # Rows created by pre-provider-identity callers are accepted
                # with the bucket inferred from the current request.  New
                # rows always persist the provider bucket explicitly.
                actual = (
                    existing.get("model_tag"), existing.get("model_digest"),
                    existing.get("phase"), existing.get("provider") or bucket,
                )
                if actual != expected:
                    raise LlamaPolicyError("V238_SHARED_BUDGET_RESERVATION_IDENTITY_MISMATCH")
                return {**existing, "reused": True}
        maximum = self._physical_maximum(bucket)
        reserved = self.llama_reserved if bucket == "llama" else self.provider_reserved.get(bucket, 0)
        if reserved >= maximum:
            if bucket == "llama":
                raise LlamaPolicyError("V238_SHARED_LLAMA_CALL_BUDGET_EXCEEDED")
            raise LlamaPolicyError(self._reservation_error(
                bucket, model_tag=model_tag, phase=token, reserved=reserved, maximum=maximum,
            ))
        if bucket == "llama":
            self.llama_reserved += 1
        else:
            self.provider_reserved[bucket] = reserved + 1
            if bucket == "qwen":
                self.qwen_reserved = self.provider_reserved[bucket]
        self.total_reserved += 1
        reservation = {
            "model_tag": str(model_tag), "model_digest": model_digest,
            "phase": token, "provider": bucket, "attempt": self.total_reserved,
            "reservation_id": str(reservation_id) if reservation_id else None,
        }
        self.reservations.append(reservation)
        return reservation

    def snapshot(self) -> dict[str, Any]:
        active_bucket = self.provider_name or "qwen"
        return {
            # Legacy aliases remain for existing status consumers.  New code
            # must use active_provider/physical_* or provider_* below.
            "qwen_reserved": self.qwen_reserved,
            "llama_reserved": self.llama_reserved,
            "total_reserved": self.total_reserved,
            "qwen_physical_maximum": self.qwen_physical_maximum,
            "llama_generation_maximum": self.llama_generation_maximum,
            "active_provider": active_bucket,
            "physical_reserved": self.llama_reserved if active_bucket == "llama" else self.provider_reserved.get(active_bucket, 0),
            "physical_maximum": self._physical_maximum(active_bucket),
            "provider_reserved": dict(self.provider_reserved),
            "provider_physical_maximums": dict(self.provider_physical_maximums),
            "reservations": list(self.reservations),
        }


class CanonicalLlamaProvider:
    """Canonical context boundary for one grouped Llama phase."""

    def __init__(self, provider: Any, *, model_tag: str, model_digest: str,
                 budget: OperationCallBudget | None = None,
                 load: Callable[[], Any] | None = None,
                 unload: Callable[[], Any] | None = None,
                 before_call: Callable[[], bool] | None = None) -> None:
        if str(model_tag) != LLAMA_MODEL_TAG or str(model_digest) != LLAMA_MODEL_DIGEST:
            raise LlamaPolicyError("V238_LLAMA_MODEL_AUTHORITY_MISMATCH")
        if not callable(provider) and not callable(getattr(provider, "respond", None)):
            raise LlamaPolicyError("V238_LLAMA_PROVIDER_REQUIRED")
        self.provider = provider
        self.model_tag = model_tag
        self.model_digest = model_digest
        self.budget = budget
        self.load_callback = load or getattr(provider, "load", None)
        self.unload_callback = unload or getattr(provider, "unload", None)
        self.before_call = before_call

    def load(self) -> Any:
        if self.before_call is not None and self.before_call():
            raise LlamaPolicyError("V238_STOP_REQUESTED")
        if callable(self.load_callback):
            return self.load_callback()
        return None

    def __call__(self, request: dict[str, Any]) -> Any:
        if self.before_call is not None and self.before_call():
            raise LlamaPolicyError("V238_STOP_REQUESTED")
        if self.budget is not None:
            self.budget.reserve(model_tag=self.model_tag, model_digest=self.model_digest, phase="LLAMA_GROUPED")
        if callable(getattr(self.provider, "respond", None)):
            return self.provider.respond(request, capture_id=request.get("capture_id"))
        return self.provider(request)

    def unload(self) -> Any:
        if callable(self.unload_callback):
            try:
                return self.unload_callback(keep_alive=0)
            except TypeError:
                # Test/fake providers may expose a zero-argument unload while
                # the canonical Ollama boundary still requests keep_alive=0.
                return self.unload_callback()
        return None


def _capture_raw(root: str | Path | None, request_id: str, raw: Any) -> str | None:
    if root is None:
        return None
    directory = Path(root) / request_id
    directory.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".raw-", suffix=".json", dir=str(directory))
    os.close(fd)
    temporary = Path(name)
    try:
        payload = raw if isinstance(raw, bytes) else (json.dumps(raw, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        temporary.write_bytes(payload)
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        destination = directory / "raw-response.json"
        os.replace(temporary, destination)
        fd = os.open(str(directory), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        return str(destination)
    finally:
        temporary.unlink(missing_ok=True)


def eligible_units(primary_ledger: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    for item in primary_ledger:
        row = dict(item)
        status = str(row.get("status", "")).upper()
        reason = str(row.get("reason_code", ""))
        if status in {"BLOCKED", "SUSPECT"}:
            if reason not in ALLOWED_REASON_CODES:
                raise LlamaPolicyError("V238_LLAMA_ELIGIBILITY_REASON_CODE_REQUIRED")
            identity = str(row.get("canonical_unit_id") or "")
            if not identity:
                raise LlamaPolicyError("V238_LLAMA_UNIT_ID_REQUIRED")
            selected.setdefault(identity, row)
    return [selected[key] for key in sorted(selected)]


def run_single_fallback_phase(
    primary_ledger: Iterable[Mapping[str, Any]],
    provider: Callable[[dict[str, Any]], Any],
    *,
    model_tag: str,
    model_digest: str,
    max_calls: int = 1,
    capture_root: str | Path | None = None,
    unload: Callable[[], Any] | None = None,
    load: Callable[[], Any] | None = None,
    budget: OperationCallBudget | None = None,
) -> dict[str, Any]:
    """Run exactly one grouped fallback request, then unload in ``finally``.

    The provider receives all eligible canonical units in one request.  It is
    injected by the caller; this function never discovers Ollama or silently
    falls back to a legacy translator.
    """
    units = eligible_units(primary_ledger)
    results: list[dict[str, Any]] = []
    phase = {"phase_count": 1, "eligible_count": len(units), "batches": 0, "calls": 0, "generation_calls": 0, "load_calls": 0, "unload_calls": 0, "control_calls": 0, "results": results, "lineage": [], "unload_requested": False, "unload_status": "NOT_REQUIRED", "load_requested": False, "load_status": "NOT_REQUIRED", "state": "CANDIDATE_REVIEW_REQUIRED", "publishable": False, "model_tag": model_tag, "model_digest": model_digest}
    if not units:
        phase["phase_count"] = 0
        phase["state"] = "NO_ELIGIBLE_UNITS"
        return phase
    call_budget = budget or HardCallBudget(max_calls)
    request_id = "llama-fallback-group-" + hashlib.sha256("|".join(row["canonical_unit_id"] for row in units).encode()).hexdigest()[:24]
    request_units = []
    for row in units:
        item = dict(row)
        item["role"] = "ADVISORY_REVIEW_FOR_SUSPECT" if str(row.get("status", "")).upper() == "SUSPECT" else "FALLBACK_FOR_BLOCKED"
        request_units.append(item)
    request = {"operation": "llama_fallback_group", "canonical_unit_ids": [row["canonical_unit_id"] for row in units], "units": request_units, "model": model_tag, "expected_response_schema": "candidates[]"}
    try:
        if callable(load):
            phase["load_requested"] = True
            phase["load_calls"] = 1
            phase["control_calls"] = 1
            load_started = time.perf_counter()
            load()
            phase["load_time_seconds"] = time.perf_counter() - load_started
            phase["load_status"] = "PASS"
        if isinstance(call_budget, OperationCallBudget):
            # CanonicalLlamaProvider reserves at the transport boundary; the
            # direct helper reserves here for backwards-compatible tests.
            if not isinstance(provider, CanonicalLlamaProvider):
                call_budget.reserve(model_tag=model_tag, model_digest=model_digest, phase="LLAMA_GROUPED")
        else:
            call_budget.reserve(1)
        phase["calls"] = 1
        phase["generation_calls"] = 1
        phase["batches"] = 1
        if isinstance(provider, CanonicalLlamaProvider):
            raw_response = provider(request)
        elif callable(getattr(provider, "respond", None)):
            raw_response = provider.respond(request, capture_id=request_id)
        else:
            raw_response = provider(request)
        raw_path = _capture_raw(capture_root, request_id, raw_response)
        if isinstance(raw_response, Mapping) and isinstance(raw_response.get("candidates"), list):
            candidates = raw_response["candidates"]
        elif len(units) == 1 and isinstance(raw_response, Mapping):
            candidates = [{"canonical_unit_id": units[0]["canonical_unit_id"], **dict(raw_response)}]
        else:
            candidates = []
        by_id = {str(item.get("canonical_unit_id")): item for item in candidates if isinstance(item, Mapping)}
        for attempt, unit in enumerate(units, 1):
            candidate = by_id.get(unit["canonical_unit_id"], {})
            valid = isinstance(candidate.get("text", candidate.get("translation")), str)
            canonical_boundary = isinstance(provider, CanonicalLlamaProvider)
            role = "ADVISORY_REVIEW_FOR_SUSPECT" if canonical_boundary and str(unit.get("status", "")).upper() == "SUSPECT" else "FALLBACK_FOR_BLOCKED"
            state = "ADVISORY_REVIEW_ONLY" if role.startswith("ADVISORY") else FALLBACK_CANDIDATE_ONLY
            row = {"canonical_unit_id": unit["canonical_unit_id"], "role": role, "state": state, "primary_model_tag": unit.get("primary_model_tag"), "primary_model_digest": unit.get("primary_model_digest"), "primary_attempts": unit.get("primary_attempts", 0), "failure_reason_code": unit["reason_code"], "fallback_model_tag": model_tag, "fallback_model_digest": model_digest, "fallback_request_id": request_id, "raw_response_capture": raw_path, "schema_status": "PASS" if valid else "FAIL", "validation_status": "CANDIDATE_ONLY" if role.startswith("FALLBACK") else "ADVISORY_ONLY", "accepted": bool(valid), "publishable": False, "publication_authorization": False}
            results.append(row)
            phase["lineage"].append({"episode_id": unit.get("episode_id"), "source_object": unit.get("source_object"), "canonical_unit_id": unit["canonical_unit_id"], "primary_model_digest": unit.get("primary_model_digest"), "fallback_model_digest": model_digest, "role": role, "reason_code": unit["reason_code"], "attempt": attempt, "request_id": request_id, "result_status": row["state"]})
    finally:
        unload_fn = unload or getattr(provider, "unload", None)
        if callable(unload_fn):
            phase["unload_requested"] = True
            phase["unload_calls"] = 1
            phase["unload_keep_alive"] = 0
            phase["control_calls"] = int(phase.get("control_calls", 0)) + 1
            try:
                unload_started = time.perf_counter()
                unload_fn()
                phase["unload_time_seconds"] = time.perf_counter() - unload_started
                phase["unload_status"] = "PASS"
            except Exception:
                phase["unload_status"] = "FAIL_CLOSED"
                raise
    return phase


def enforce_v238_runtime_context(context: Mapping[str, Any]) -> dict[str, Any]:
    """Reject legacy fallback/reviewer injection and expose explicit policy."""
    forbidden = ("legacy_fallback", "fallback_translator", "legacy_reviewer", "fallback_model_callable")
    if any(context.get(key) for key in forbidden):
        raise LlamaPolicyError("V238_LEGACY_FALLBACK_DISABLED")
    return {"legacy_fallback_enabled": False, "llama_policy": "OBJECTIVE_SINGLE_GROUP_PHASE", "llama_unload_finally": True}


def review_suspect_qwen_outputs(outputs: Iterable[Mapping[str, Any]], reviewer: Callable[[dict[str, Any]], Any]) -> list[dict[str, Any]]:
    eligible = [dict(output) for output in outputs
                if str(output.get("role", "PRIMARY")).upper() == "PRIMARY"
                and str(output.get("status", "")).upper() == "SUSPECT"]
    if not eligible:
        return []
    # Legacy callers may still provide this function, but it now has one
    # grouped boundary and never performs one request per unit.
    response = reviewer({"operation": "llama_grouped_reviewer", "units": eligible,
                         "expected_response_schema": "verdicts[]"})
    rows = response.get("verdicts", []) if isinstance(response, Mapping) else []
    if not isinstance(rows, list) and isinstance(response, Mapping) and response.get("verdict"):
        rows = [{"canonical_unit_id": eligible[0].get("canonical_unit_id"), "verdict": response.get("verdict")}]
    by_id = {str(row.get("canonical_unit_id")): row for row in rows if isinstance(row, Mapping)}
    verdicts: list[dict[str, Any]] = []
    for output in eligible:
        row = by_id.get(str(output.get("canonical_unit_id")), {})
        verdict = str(row.get("verdict", "REVIEWER_UNRESOLVED"))
        if verdict not in REVIEW_VERDICTS:
            verdict = "REVIEWER_UNRESOLVED"
        verdicts.append({"canonical_unit_id": output.get("canonical_unit_id"), "verdict": verdict, "advisory": True, "publication_authorization": False})
    return verdicts


__all__ = ["ALLOWED_REASON_CODES", "FALLBACK_CANDIDATE_ONLY", "LLAMA_MODEL_TAG", "LLAMA_MODEL_DIGEST", "DEFAULT_QWEN_PHYSICAL_MAXIMUM", "DEFAULT_PROVIDER_PHYSICAL_MAXIMUM", "HardCallBudget", "OperationCallBudget", "CanonicalLlamaProvider", "LlamaPolicyError", "eligible_units", "run_single_fallback_phase", "review_suspect_qwen_outputs", "enforce_v238_runtime_context"]
