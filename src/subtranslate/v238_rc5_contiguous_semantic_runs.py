"""V2.3.8 RC5 contiguous semantic-run wire protocol.

The model returns only a permutation of owner labels and positive contiguous
run lengths. Target text remains entirely deterministic and comes from the
canonical RC3 atomizer.
"""
from __future__ import annotations

import json
from typing import Any

import jsonschema

from v238_rc3_atom_owner_vector import target_atoms, validate_owner_vector


RC5_SCHEMA = {
    "type": "array",
    "minItems": 1,
    "items": {
        "type": "array",
        "minItems": 2,
        "maxItems": 2,
        "items": {"type": "integer"},
    },
}


def strict_run_schema(owner_count: int) -> dict[str, Any]:
    schema = json.loads(json.dumps(RC5_SCHEMA))
    schema["minItems"] = owner_count
    schema["maxItems"] = owner_count
    schema["items"]["items"]["minimum"] = 1
    return schema


def validate_run_allocation(value: Any, *, owner_count: int, atom_count: int) -> tuple[list[list[int]] | None, dict[str, Any]]:
    trace = {"schema": "v238_rc5_contiguous_semantic_runs.v1", "owner_count": owner_count, "atom_count": atom_count}
    try:
        jsonschema.Draft7Validator(strict_run_schema(owner_count)).validate(value)
    except jsonschema.ValidationError as exc:
        return None, {**trace, "valid": False, "reason": "STRICT_RC5_WIRE_SCHEMA", "detail": exc.message}
    rows = [[int(row[0]), int(row[1])] for row in value]
    labels = [row[0] for row in rows]
    counts = [row[1] for row in rows]
    if any(label < 1 or label > owner_count for label in labels):
        return None, {**trace, "valid": False, "reason": "OWNER_LABEL_OUT_OF_RANGE"}
    if len(set(labels)) != owner_count or set(labels) != set(range(1, owner_count + 1)):
        return None, {**trace, "valid": False, "reason": "OWNER_LABEL_PERMUTATION_INVALID"}
    if any(count < 1 for count in counts):
        return None, {**trace, "valid": False, "reason": "RUN_COUNT_NOT_POSITIVE"}
    if sum(counts) != atom_count:
        return None, {**trace, "valid": False, "reason": "RUN_COUNT_SUM_MISMATCH", "sum": sum(counts)}
    return rows, {**trace, "valid": True, "reason": "RC5_CONTIGUOUS_RUN_ALLOCATION_VALID", "labels": labels, "run_counts": counts, "run_count_sum": sum(counts)}


def expand_run_allocation(rows: list[list[int]], *, atom_count: int) -> tuple[list[int] | None, dict[str, Any]]:
    vector: list[int] = []
    for label, count in rows:
        vector.extend([label] * count)
    trace = {"atom_count": atom_count, "expanded_vector": vector, "expanded_length": len(vector), "valid": len(vector) == atom_count}
    if not trace["valid"]:
        trace["reason"] = "EXPANDED_VECTOR_LENGTH_MISMATCH"
        return None, trace
    return vector, {**trace, "reason": "DETERMINISTIC_EXPANSION_PASS"}


def canonicalize_labels(rows: list[list[int]], presented_to_canonical: dict[int, int]) -> list[list[int]]:
    return [[presented_to_canonical[label], count] for label, count in rows]


def expand_and_validate_against_target(rows: list[list[int]], *, canonical_segment_ids: list[str], target_text: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    atoms = target_atoms(target_text)
    vector, expansion = expand_run_allocation(rows, atom_count=len(atoms))
    if vector is None:
        return None, expansion
    internal, atom_trace = validate_owner_vector(vector, atoms=atoms, canonical_segment_ids=canonical_segment_ids, target_text=target_text)
    if internal is None:
        return None, {"valid": False, "reason": "EXPANDED_VECTOR_RC3_VALIDATION_FAILED", "expansion": expansion, "atom_trace": atom_trace}
    return internal, {"valid": True, "reason": "RC5_EXPANSION_AND_RC3_MAPPING_PASS", "expansion": expansion, "atom_trace": atom_trace}


def build_run_request(*, semantic_group_id: str, target_text: str, source_segments: list[dict[str, str]], schema: dict[str, Any], model: str, presented_labels: dict[int, int] | None = None) -> dict[str, Any]:
    presented_labels = presented_labels or {i: i for i in range(1, len(source_segments) + 1)}
    owners = [{"owner_label": presented_labels[i], "source_text": segment["source_text"]} for i, segment in enumerate(source_segments, 1)]
    atoms = target_atoms(target_text)
    contract = {
        "semantic_group_id": semantic_group_id,
        "task": "CONTIGUOUS_SEMANTIC_RUN_ALLOCATION_ONLY",
        "source_owners": owners,
        "target_atoms": [{"atom_index": i, "text": atom} for i, atom in enumerate(atoms, 1)],
        "approved_target_linguistic_text": target_text,
        "target_locale": "pt-BR",
        "rules": [
            "Return only a JSON root array with exactly one [owner_label, run_atom_count] row per owner.",
            "Rows are contiguous runs in target order; run_atom_count is positive.",
            "Use every presented owner_label exactly once and make counts sum to the target atom count.",
            "Do not return text, atoms, objects, keys, translated content, or explanations.",
        ],
    }
    return {"model": model, "messages": [{"role": "system", "content": "Return only the strict contiguous owner-run array. Do not return text."}, {"role": "user", "content": json.dumps(contract, ensure_ascii=False, separators=(",", ":"))}], "format": schema, "options": {"temperature": 0.0, "num_ctx": 4096, "num_predict": 384}, "stream": False, "think": False, "keep_alive": "30m"}
