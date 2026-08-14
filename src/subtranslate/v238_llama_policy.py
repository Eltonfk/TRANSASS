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
    """Shared per-operation reservation ledger for every model transport."""

    def __init__(self, *, qwen_physical_maximum: int = 131, llama_generation_maximum: int = 1) -> None:
        self.qwen_physical_maximum = int(qwen_physical_maximum)
        self.llama_generation_maximum = int(llama_generation_maximum)
        self.total_reserved = 0
        self.qwen_reserved = 0
        self.llama_reserved = 0
        self.reservations: list[dict[str, Any]] = []

    def reserve(self, *, model_tag: str, model_digest: str | None, phase: str) -> dict[str, Any]:
        token = str(phase or "").upper()
        is_llama = "LLAMA" in token or str(model_tag).casefold().startswith("llama")
        if is_llama:
            if self.llama_reserved >= self.llama_generation_maximum:
                raise LlamaPolicyError("V238_SHARED_LLAMA_CALL_BUDGET_EXCEEDED")
            self.llama_reserved += 1
        else:
            if self.qwen_reserved >= self.qwen_physical_maximum:
                raise LlamaPolicyError("V238_SHARED_QWEN_PHYSICAL_CALL_BUDGET_EXCEEDED")
            self.qwen_reserved += 1
        self.total_reserved += 1
        reservation = {
            "model_tag": str(model_tag), "model_digest": model_digest,
            "phase": token, "attempt": self.total_reserved,
        }
        self.reservations.append(reservation)
        return reservation

    def snapshot(self) -> dict[str, Any]:
        return {
            "qwen_reserved": self.qwen_reserved,
            "llama_reserved": self.llama_reserved,
            "total_reserved": self.total_reserved,
            "qwen_physical_maximum": self.qwen_physical_maximum,
            "llama_generation_maximum": self.llama_generation_maximum,
            "reservations": list(self.reservations),
        }


class CanonicalLlamaProvider:
    """Canonical context boundary for one grouped Llama phase."""

    def __init__(self, provider: Any, *, model_tag: str, model_digest: str,
                 budget: OperationCallBudget | None = None,
                 load: Callable[[], Any] | None = None,
                 unload: Callable[[], Any] | None = None) -> None:
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

    def load(self) -> Any:
        if callable(self.load_callback):
            return self.load_callback()
        return None

    def __call__(self, request: dict[str, Any]) -> Any:
        if self.budget is not None:
            self.budget.reserve(model_tag=self.model_tag, model_digest=self.model_digest, phase="LLAMA_GROUPED")
        if callable(getattr(self.provider, "respond", None)):
            return self.provider.respond(request, capture_id=request.get("capture_id"))
        return self.provider(request)

    def unload(self) -> Any:
        if callable(self.unload_callback):
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
    request = {"operation": "llama_fallback_group", "canonical_unit_ids": [row["canonical_unit_id"] for row in units], "units": units, "model": model_tag, "expected_response_schema": "candidates[]"}
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


__all__ = ["ALLOWED_REASON_CODES", "FALLBACK_CANDIDATE_ONLY", "LLAMA_MODEL_TAG", "LLAMA_MODEL_DIGEST", "HardCallBudget", "OperationCallBudget", "CanonicalLlamaProvider", "LlamaPolicyError", "eligible_units", "run_single_fallback_phase", "review_suspect_qwen_outputs", "enforce_v238_runtime_context"]
