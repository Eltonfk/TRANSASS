"""Deterministic, advisory-only Llama fallback/reviewer policy.

This module contains no model client.  Callers inject a bounded fake/provider
and receive non-publishable candidates with complete unit-level lineage.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

ALLOWED_REASON_CODES = frozenset({
    "PRIMARY_RETRIES_EXHAUSTED", "PRIMARY_SCHEMA_REJECTED",
    "PRIMARY_VALIDATION_REJECTED", "SEMANTIC_AMBIGUITY_UNRESOLVED",
    "DETERMINISTIC_SUSPECT_FLAG",
})
FALLBACK_CANDIDATE_ONLY = "FALLBACK_CANDIDATE_ONLY"
REVIEW_VERDICTS = frozenset({"REVIEWER_NO_OBJECTION", "REVIEWER_FLAGGED", "REVIEWER_UNRESOLVED"})


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
) -> dict[str, Any]:
    """Run exactly one grouped fallback request, then unload in ``finally``.

    The provider receives all eligible canonical units in one request.  It is
    injected by the caller; this function never discovers Ollama or silently
    falls back to a legacy translator.
    """
    units = eligible_units(primary_ledger)
    results: list[dict[str, Any]] = []
    phase = {"phase_count": 1, "eligible_count": len(units), "batches": 0, "calls": 0, "results": results, "lineage": [], "unload_requested": False, "unload_status": "NOT_REQUIRED"}
    if not units:
        phase["phase_count"] = 0
        return phase
    budget = HardCallBudget(max_calls)
    request_id = "llama-fallback-group-" + hashlib.sha256("|".join(row["canonical_unit_id"] for row in units).encode()).hexdigest()[:24]
    request = {"operation": "llama_fallback_group", "canonical_unit_ids": [row["canonical_unit_id"] for row in units], "units": units, "model": model_tag, "expected_response_schema": "candidates[]"}
    try:
        budget.reserve(1)
        phase["calls"] = 1
        phase["batches"] = 1
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
            row = {"canonical_unit_id": unit["canonical_unit_id"], "state": FALLBACK_CANDIDATE_ONLY, "primary_model_digest": unit.get("primary_model_digest"), "primary_attempts": unit.get("primary_attempts", 0), "failure_reason_code": unit["reason_code"], "fallback_model_tag": model_tag, "fallback_model_digest": model_digest, "fallback_request_id": request_id, "raw_response_capture": raw_path, "schema_status": "PASS" if valid else "FAIL", "validation_status": "CANDIDATE_ONLY", "accepted": bool(valid), "publication_authorization": False}
            results.append(row)
            phase["lineage"].append({"episode_id": unit.get("episode_id"), "source_object": unit.get("source_object"), "canonical_unit_id": unit["canonical_unit_id"], "primary_model_digest": unit.get("primary_model_digest"), "fallback_model_digest": model_digest, "role": "FALLBACK", "reason_code": unit["reason_code"], "attempt": attempt, "request_id": request_id, "result_status": row["state"]})
    finally:
        unload_fn = unload or getattr(provider, "unload", None)
        if callable(unload_fn):
            phase["unload_requested"] = True
            try:
                unload_fn()
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
    verdicts: list[dict[str, Any]] = []
    for output in outputs:
        if str(output.get("role", "PRIMARY")).upper() != "PRIMARY" or str(output.get("status", "")).upper() != "SUSPECT":
            continue
        response = reviewer({"operation": "llama_reviewer", "canonical_unit_id": output.get("canonical_unit_id"), "qwen_output": output.get("text")})
        verdict = str(response.get("verdict", "REVIEWER_UNRESOLVED")) if isinstance(response, Mapping) else "REVIEWER_UNRESOLVED"
        if verdict not in REVIEW_VERDICTS:
            verdict = "REVIEWER_UNRESOLVED"
        verdicts.append({"canonical_unit_id": output.get("canonical_unit_id"), "verdict": verdict, "advisory": True, "publication_authorization": False})
    return verdicts


__all__ = ["ALLOWED_REASON_CODES", "FALLBACK_CANDIDATE_ONLY", "HardCallBudget", "LlamaPolicyError", "eligible_units", "run_single_fallback_phase", "review_suspect_qwen_outputs", "enforce_v238_runtime_context"]
