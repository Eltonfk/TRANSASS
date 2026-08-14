"""Deterministic, advisory-only Llama fallback/reviewer policy.

This module contains no model client.  Callers inject a bounded fake/provider
and receive non-publishable candidates with complete unit-level lineage.
"""
from __future__ import annotations

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


def run_single_fallback_phase(primary_ledger: Iterable[Mapping[str, Any]], provider: Callable[[dict[str, Any]], Any], *, model_tag: str, model_digest: str, batch_size: int = 16) -> dict[str, Any]:
    units = eligible_units(primary_ledger)
    results: list[dict[str, Any]] = []
    phase = {"phase_count": 1, "eligible_count": len(units), "batches": 0, "calls": 0, "results": results, "lineage": []}
    if not units:
        phase["phase_count"] = 0
        return phase
    for start in range(0, len(units), max(1, int(batch_size))):
        batch = units[start:start + max(1, int(batch_size))]
        phase["batches"] += 1
        for attempt, unit in enumerate(batch, 1):
            request = {"operation": "llama_fallback", "canonical_unit_id": unit["canonical_unit_id"], "episode_id": unit.get("episode_id"), "source_object": unit.get("source_object"), "reason_code": unit["reason_code"], "model": model_tag}
            phase["calls"] += 1
            response = provider(request)
            valid = isinstance(response, Mapping) and isinstance(response.get("text", response.get("translation")), str)
            row = {"canonical_unit_id": unit["canonical_unit_id"], "state": FALLBACK_CANDIDATE_ONLY, "primary_model_digest": unit.get("primary_model_digest"), "primary_attempts": unit.get("primary_attempts", 0), "failure_reason_code": unit["reason_code"], "fallback_model_tag": model_tag, "fallback_model_digest": model_digest, "fallback_request_id": request["canonical_unit_id"], "schema_status": "PASS" if valid else "FAIL", "validation_status": "CANDIDATE_ONLY", "accepted": bool(valid), "publication_authorization": False}
            results.append(row)
            phase["lineage"].append({"episode_id": unit.get("episode_id"), "source_object": unit.get("source_object"), "canonical_unit_id": unit["canonical_unit_id"], "primary_model_digest": unit.get("primary_model_digest"), "fallback_model_digest": model_digest, "role": "FALLBACK", "reason_code": unit["reason_code"], "attempt": attempt, "request_id": request["canonical_unit_id"], "result_status": row["state"]})
    return phase


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


__all__ = ["ALLOWED_REASON_CODES", "FALLBACK_CANDIDATE_ONLY", "LlamaPolicyError", "eligible_units", "run_single_fallback_phase", "review_suspect_qwen_outputs"]
