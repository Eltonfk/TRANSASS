"""V2.3.8 RC8 offline pairwise-precedence and local-boundary programs.

This module deliberately has no transport, model, renderer, or production
pipeline dependency.  It represents the RC8 domain and provides the strict
deterministic operations that a future model-facing adapter may call.
"""
from __future__ import annotations

import hashlib
import itertools
import json
from typing import Any, Iterable, Mapping, Sequence


I_BEFORE_J = "I_BEFORE_J"
J_BEFORE_I = "J_BEFORE_I"
NONE_OR_UNRESOLVED = "NONE_OR_UNRESOLVED"
PAIRWISE_RESULTS = frozenset({I_BEFORE_J, J_BEFORE_I, NONE_OR_UNRESOLVED})


class RC8ValidationError(ValueError):
    """A fail-closed RC8 representation or graph error."""


def _strict_positive_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise RC8ValidationError(f"{field}_MUST_BE_POSITIVE_INTEGER")
    return value


def _owner_id(value: Any, *, owner_count: int, field: str) -> int:
    value = _strict_positive_int(value, field=field)
    if value > owner_count:
        raise RC8ValidationError(f"{field}_OUT_OF_RANGE")
    return value


def pair_count(owner_count: int) -> int:
    """Return C(N,2), rejecting nonsensical owner domains."""
    _strict_positive_int(owner_count, field="owner_count")
    return owner_count * (owner_count - 1) // 2


def _pairwise_edges(
    owner_count: int, comparisons: Iterable[Mapping[str, Any]]
) -> tuple[dict[int, set[int]], list[dict[str, Any]]]:
    """Validate pair observations and construct the directed graph.

    Each record is relative to the supplied orientation: ``left`` and
    ``right`` are owner IDs and ``result`` is I_BEFORE_J or J_BEFORE_I.
    NONE is intentionally rejected rather than resolved heuristically.
    """
    _strict_positive_int(owner_count, field="owner_count")
    adjacency = {owner: set() for owner in range(1, owner_count + 1)}
    expected = {(i, j) for i in range(1, owner_count + 1) for j in range(i + 1, owner_count + 1)}
    seen: set[tuple[int, int]] = set()
    trace: list[dict[str, Any]] = []
    for index, record in enumerate(comparisons):
        if not isinstance(record, Mapping):
            raise RC8ValidationError(f"COMPARISON_{index}_NOT_OBJECT")
        left = _owner_id(record.get("left"), owner_count=owner_count, field="left")
        right = _owner_id(record.get("right"), owner_count=owner_count, field="right")
        if left == right:
            raise RC8ValidationError("SELF_COMPARISON_FORBIDDEN")
        result = record.get("result")
        if result not in PAIRWISE_RESULTS:
            raise RC8ValidationError("UNKNOWN_PAIRWISE_RESULT")
        key = (min(left, right), max(left, right))
        if key in seen:
            raise RC8ValidationError("DUPLICATE_OR_CONTRADICTORY_COMPARISON")
        seen.add(key)
        if result == NONE_OR_UNRESOLVED:
            raise RC8ValidationError("PAIRWISE_NONE_OR_UNRESOLVED")
        before, after = (left, right) if result == I_BEFORE_J else (right, left)
        adjacency[before].add(after)
        trace.append({"index": index, "left": left, "right": right, "result": result, "edge": [before, after]})
    missing = sorted(expected - seen)
    if missing:
        raise RC8ValidationError("MISSING_PAIRWISE_COMPARISON")
    return adjacency, trace


def resolve_pairwise_order(owner_count: int, comparisons: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Build a DAG and require one unique topological order."""
    adjacency, trace = _pairwise_edges(owner_count, comparisons)
    indegree = {owner: 0 for owner in adjacency}
    for before, afters in adjacency.items():
        for after in afters:
            indegree[after] += 1
    order: list[int] = []
    levels: list[list[int]] = []
    while len(order) < owner_count:
        available = sorted(owner for owner, degree in indegree.items() if degree == 0 and owner not in order)
        levels.append(available)
        if len(available) != 1:
            if not available:
                raise RC8ValidationError("PAIRWISE_CYCLE")
            raise RC8ValidationError("PAIRWISE_NON_UNIQUE_TOTAL_ORDER")
        owner = available[0]
        order.append(owner)
        indegree[owner] = -1
        for after in adjacency[owner]:
            indegree[after] -= 1
    return {
        "owner_count": owner_count,
        "comparisons": trace,
        "edges": [[before, after] for before in sorted(adjacency) for after in sorted(adjacency[before])],
        "topological_levels": levels,
        "unique_total_order": order,
        "valid": True,
    }


class PairwisePrecedenceProgram:
    """Offline façade named by the RC8 architecture specification."""

    @staticmethod
    def resolve(owner_count: int, comparisons: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
        return resolve_pairwise_order(owner_count, comparisons)


def candidate_hash(owner_order: Sequence[int], run_lengths: Sequence[int]) -> str:
    """Canonical hash compatible with the existing RC6 candidate hash."""
    payload = {"owner_order": list(owner_order), "run_lengths": list(run_lengths)}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def derive_local_boundaries(owner_order: Sequence[int], cuts: Sequence[int], atom_count: int) -> dict[str, Any]:
    """Validate increasing cuts and derive contiguous ranges/vector."""
    _strict_positive_int(atom_count, field="atom_count")
    order = list(owner_order)
    if any(isinstance(owner, bool) or not isinstance(owner, int) for owner in order):
        raise RC8ValidationError("OWNER_ORDER_MUST_CONTAIN_INTEGERS")
    if len(order) < 1 or len(set(order)) != len(order) or set(order) != set(range(1, len(order) + 1)):
        raise RC8ValidationError("OWNER_ORDER_NOT_A_PERMUTATION")
    raw_cuts = list(cuts)
    if len(raw_cuts) != len(order) - 1:
        raise RC8ValidationError("CUT_COUNT_MISMATCH")
    if any(isinstance(cut, bool) or not isinstance(cut, int) for cut in raw_cuts):
        raise RC8ValidationError("CUT_MUST_BE_INTEGER")
    if any(cut < 1 or cut >= atom_count for cut in raw_cuts):
        raise RC8ValidationError("CUT_OUT_OF_RANGE")
    if any(left >= right for left, right in zip(raw_cuts, raw_cuts[1:])):
        raise RC8ValidationError("CUTS_NOT_STRICTLY_INCREASING")
    boundaries = [0, *raw_cuts, atom_count]
    lengths = [boundaries[index + 1] - boundaries[index] for index in range(len(order))]
    if any(length < 1 for length in lengths) or sum(lengths) != atom_count:
        raise RC8ValidationError("BOUNDARY_COVERAGE_INVALID")
    ranges = []
    vector: list[int] = []
    for owner, start_zero, end_zero in zip(order, boundaries[:-1], boundaries[1:]):
        start = start_zero + 1
        end = end_zero
        ranges.append({"owner": owner, "start": start, "end": end, "count": end - start + 1})
        vector.extend([owner] * (end - start + 1))
    if len(vector) != atom_count:
        raise RC8ValidationError("BOUNDARY_VECTOR_LENGTH_MISMATCH")
    return {
        "owner_order": order,
        "cuts": raw_cuts,
        "run_lengths": lengths,
        "owner_ranges": ranges,
        "canonical_owner_vector": vector,
        "atom_count": atom_count,
        "canonical_candidate_hash": candidate_hash(order, lengths),
        "valid": True,
    }


class LocalBoundaryProgram:
    """Offline façade for strict local boundary derivation."""

    @staticmethod
    def derive(owner_order: Sequence[int], cuts: Sequence[int], atom_count: int) -> dict[str, Any]:
        return derive_local_boundaries(owner_order, cuts, atom_count)


def enumerate_rc8_allocations(owner_count: int, atom_count: int) -> list[dict[str, Any]]:
    """Enumerate every total-order + increasing-cut RC8 allocation."""
    _strict_positive_int(owner_count, field="owner_count")
    _strict_positive_int(atom_count, field="atom_count")
    if atom_count < owner_count:
        raise RC8ValidationError("ATOM_COUNT_LESS_THAN_OWNER_COUNT")
    result: list[dict[str, Any]] = []
    for order in itertools.permutations(range(1, owner_count + 1)):
        for cuts in itertools.combinations(range(1, atom_count), owner_count - 1):
            result.append(derive_local_boundaries(order, cuts, atom_count))
    hashes = [entry["canonical_candidate_hash"] for entry in result]
    if len(set(hashes)) != len(hashes):
        raise AssertionError("RC8_DUPLICATE_CANONICAL_ALLOCATION")
    return result


def allocation_key(entry: Mapping[str, Any]) -> tuple[int, ...]:
    return tuple(entry["canonical_owner_vector"])
