"""V2.3.8 RC9 independent semantic-span representation.

This is an offline representation/validator only.  It has no model, HTTP,
renderer, or production-pipeline dependency.
"""
from __future__ import annotations

import hashlib
import itertools
import json
from typing import Any, Iterable, Mapping, Sequence

from v238_rc8_pairwise_boundary import RC8ValidationError, candidate_hash


class RC9ValidationError(ValueError):
    """An independent semantic span fails closed."""


ASSIGNMENT_KEYS = frozenset({"owner_index", "start_atom", "end_atom", "provenance"})


def _strict_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RC9ValidationError(f"{field}_MUST_BE_INTEGER")
    return value


def _positive(value: Any, *, field: str) -> int:
    value = _strict_int(value, field=field)
    if value < 1:
        raise RC9ValidationError(f"{field}_MUST_BE_POSITIVE")
    return value


def _normalize_assignment(raw: Mapping[str, Any], *, owner_count: int) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise RC9ValidationError("ASSIGNMENT_MUST_BE_OBJECT")
    if set(raw) != ASSIGNMENT_KEYS:
        raise RC9ValidationError("ASSIGNMENT_KEYS_INVALID")
    owner = _positive(raw["owner_index"], field="owner_index")
    if owner > owner_count:
        raise RC9ValidationError("OWNER_INDEX_OUT_OF_RANGE")
    start = _strict_int(raw["start_atom"], field="start_atom")
    end = _strict_int(raw["end_atom"], field="end_atom")
    if not isinstance(raw["provenance"], (str, Mapping)):
        raise RC9ValidationError("PROVENANCE_INVALID")
    return {"owner_index": owner, "start_atom": start, "end_atom": end, "provenance": raw["provenance"]}


def validate_span_partition(assignments: Iterable[Mapping[str, Any]], *, owner_count: int, atom_count: int) -> dict[str, Any]:
    """Validate a complete owner→contiguous-span partition and derive RC8 data."""
    owner_count = _positive(owner_count, field="owner_count")
    atom_count = _positive(atom_count, field="atom_count")
    rows = [_normalize_assignment(raw, owner_count=owner_count) for raw in assignments]
    if len(rows) != owner_count:
        raise RC9ValidationError("ASSIGNMENT_COUNT_MISMATCH")
    owners = [row["owner_index"] for row in rows]
    if set(owners) != set(range(1, owner_count + 1)) or len(set(owners)) != owner_count:
        raise RC9ValidationError("OWNER_COVERAGE_INVALID")
    for row in rows:
        start, end = row["start_atom"], row["end_atom"]
        if start == 0 or end == 0:
            if start == 0 and end == 0:
                raise RC9ValidationError("UNRESOLVED_SPAN")
            raise RC9ValidationError("PARTIAL_UNRESOLVED_SPAN")
        if start < 1 or end < 1:
            raise RC9ValidationError("SPAN_INDEX_NEGATIVE_OR_ZERO")
        if start > end:
            raise RC9ValidationError("SPAN_START_AFTER_END")
        if end > atom_count:
            raise RC9ValidationError("SPAN_END_OUT_OF_RANGE")
    ordered = sorted(rows, key=lambda row: (row["start_atom"], row["end_atom"], row["owner_index"]))
    covered: list[int] = []
    previous_end = 0
    for row in ordered:
        start, end = row["start_atom"], row["end_atom"]
        if start <= previous_end:
            raise RC9ValidationError("SPAN_OVERLAP_OR_DUPLICATE")
        if start != previous_end + 1:
            raise RC9ValidationError("SPAN_GAP")
        covered.extend(range(start, end + 1))
        previous_end = end
    if covered != list(range(1, atom_count + 1)):
        raise RC9ValidationError("INCOMPLETE_ATOM_COVERAGE")
    owner_order = [row["owner_index"] for row in ordered]
    cuts = [row["end_atom"] for row in ordered[:-1]]
    run_lengths = [row["end_atom"] - row["start_atom"] + 1 for row in ordered]
    vector: list[int] = []
    ranges: list[dict[str, int]] = []
    for row in ordered:
        vector.extend([row["owner_index"]] * (row["end_atom"] - row["start_atom"] + 1))
        ranges.append({"owner": row["owner_index"], "start": row["start_atom"], "end": row["end_atom"], "count": row["end_atom"] - row["start_atom"] + 1})
    if len(vector) != atom_count:
        raise RC9ValidationError("CANONICAL_VECTOR_LENGTH_MISMATCH")
    return {
        "assignments_input_order": rows,
        "assignments_ordered_by_start": ordered,
        "owner_order": owner_order,
        "cuts": cuts,
        "run_lengths": run_lengths,
        "owner_ranges": ranges,
        "canonical_owner_vector": vector,
        "atom_count": atom_count,
        "owner_count": owner_count,
        "canonical_candidate_hash": candidate_hash(tuple(owner_order), tuple(run_lengths)),
        "valid": True,
    }


class IndependentSemanticSpanProgram:
    """Named RC9 offline validator/deriver."""

    @staticmethod
    def validate(assignments: Iterable[Mapping[str, Any]], *, owner_count: int, atom_count: int) -> dict[str, Any]:
        return validate_span_partition(assignments, owner_count=owner_count, atom_count=atom_count)


def _span_rows(owner_order: Sequence[int], cuts: Sequence[int], atom_count: int) -> list[dict[str, Any]]:
    points = [0, *cuts, atom_count]
    return [
        {"owner_index": owner, "start_atom": points[index] + 1, "end_atom": points[index + 1], "provenance": "RC9_INDEPENDENT_ENUMERATOR"}
        for index, owner in enumerate(owner_order)
    ]


def enumerate_rc9_partitions(owner_count: int, atom_count: int) -> list[dict[str, Any]]:
    """Independently enumerate ordered owner spans and validate each partition."""
    owner_count = _positive(owner_count, field="owner_count")
    atom_count = _positive(atom_count, field="atom_count")
    if atom_count < owner_count:
        raise RC9ValidationError("ATOM_COUNT_LESS_THAN_OWNER_COUNT")
    result: list[dict[str, Any]] = []
    for owner_order in itertools.permutations(range(1, owner_count + 1)):
        for cuts in itertools.combinations(range(1, atom_count), owner_count - 1):
            result.append(validate_span_partition(_span_rows(owner_order, cuts, atom_count), owner_count=owner_count, atom_count=atom_count))
    vectors = [tuple(row["canonical_owner_vector"]) for row in result]
    if len(vectors) != len(set(vectors)):
        raise AssertionError("RC9_DUPLICATE_CANONICAL_VECTOR")
    return result


def allocation_key(result: Mapping[str, Any]) -> tuple[int, ...]:
    return tuple(result["canonical_owner_vector"])
