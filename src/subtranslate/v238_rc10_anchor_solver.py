"""V2.3.8 RC10 semantic-anchor constrained partition solver.

Offline only.  The solver never invents or repairs a boundary: it intersects
resolved semantic anchors with the complete RC9 allocation domain.  Exactly
one compatible allocation is required before any allocation is exposed.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from v238_rc9_independent_span import RC9ValidationError, enumerate_rc9_partitions


class RC10AnchorValidationError(ValueError):
    """An anchor set is structurally invalid and fails closed."""


ANCHOR_KEYS = frozenset({"owner_index", "start_atom", "end_atom", "provenance"})


def _strict_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RC10AnchorValidationError(f"{field}_MUST_BE_INTEGER")
    return value


def _normalize_anchors(anchors: Iterable[Mapping[str, Any]], *, owner_count: int, atom_count: int) -> tuple[list[dict[str, Any]], str | None]:
    rows = list(anchors)
    if len(rows) != owner_count:
        raise RC10AnchorValidationError("ANCHOR_COUNT_MISMATCH")
    normalized: list[dict[str, Any]] = []
    for raw in rows:
        if not isinstance(raw, Mapping) or set(raw) != ANCHOR_KEYS:
            raise RC10AnchorValidationError("ANCHOR_KEYS_INVALID")
        owner = _strict_int(raw["owner_index"], "owner_index")
        if owner < 1 or owner > owner_count:
            raise RC10AnchorValidationError("OWNER_INDEX_OUT_OF_RANGE")
        start = _strict_int(raw["start_atom"], "start_atom")
        end = _strict_int(raw["end_atom"], "end_atom")
        if not isinstance(raw["provenance"], (str, Mapping)):
            raise RC10AnchorValidationError("PROVENANCE_INVALID")
        if start == 0 and end == 0:
            normalized.append({"owner_index": owner, "start_atom": 0, "end_atom": 0, "provenance": raw["provenance"]})
            continue
        if start == 0 or end == 0:
            raise RC10AnchorValidationError("PARTIAL_UNRESOLVED_ANCHOR")
        if start < 1 or end < 1 or start > end or end > atom_count:
            raise RC10AnchorValidationError("ANCHOR_RANGE_INVALID")
        normalized.append({"owner_index": owner, "start_atom": start, "end_atom": end, "provenance": raw["provenance"]})
    owners = [row["owner_index"] for row in normalized]
    if len(set(owners)) != owner_count:
        raise RC10AnchorValidationError("DUPLICATE_OR_MISSING_OWNER")
    if set(owners) != set(range(1, owner_count + 1)):
        raise RC10AnchorValidationError("OWNER_COVERAGE_INVALID")
    if any(row["start_atom"] == 0 for row in normalized):
        return normalized, "ANCHOR_UNRESOLVED"
    return normalized, None


def _candidate_hash(candidate: Mapping[str, Any]) -> str:
    return str(candidate["canonical_candidate_hash"])


def _complete_spans(candidate: Mapping[str, Any]) -> list[dict[str, int]]:
    return [
        {"owner_index": row["owner"], "start_atom": row["start"], "end_atom": row["end"]}
        for row in candidate["owner_ranges"]
    ]


class AnchorConstrainedPartitionSolver:
    """Intersect resolved anchors with the complete RC9 allocation universe."""

    def __init__(self, owner_count: int, atom_count: int):
        if isinstance(owner_count, bool) or not isinstance(owner_count, int) or owner_count < 1:
            raise RC10AnchorValidationError("OWNER_COUNT_INVALID")
        if isinstance(atom_count, bool) or not isinstance(atom_count, int) or atom_count < owner_count:
            raise RC10AnchorValidationError("ATOM_COUNT_INVALID")
        self.owner_count = owner_count
        self.atom_count = atom_count
        self.candidates = enumerate_rc9_partitions(owner_count, atom_count)

    @property
    def candidate_count(self) -> int:
        return len(self.candidates)

    @property
    def candidate_hashes(self) -> list[str]:
        return sorted(_candidate_hash(candidate) for candidate in self.candidates)

    def solve(self, anchors: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
        normalized, special_status = _normalize_anchors(anchors, owner_count=self.owner_count, atom_count=self.atom_count)
        base: dict[str, Any] = {
            "schema": "v238_rc10_anchor_solver_result.v1",
            "owner_count": self.owner_count,
            "atom_count": self.atom_count,
            "candidate_universe_count": self.candidate_count,
            "candidate_universe_hashes": self.candidate_hashes,
            "anchors": normalized,
        }
        if special_status:
            return {**base, "status": special_status, "compatible_candidate_count": 0, "compatible_candidate_hashes": []}
        compatible = []
        for candidate in self.candidates:
            vector = candidate["canonical_owner_vector"]
            if all(all(vector[index - 1] == anchor["owner_index"] for index in range(anchor["start_atom"], anchor["end_atom"] + 1)) for anchor in normalized):
                compatible.append(candidate)
        hashes = sorted(_candidate_hash(candidate) for candidate in compatible)
        result = {**base, "compatible_candidate_count": len(compatible), "compatible_candidate_hashes": hashes}
        if not compatible:
            return {**result, "status": "ANCHOR_CONTRADICTION"}
        if len(compatible) > 1:
            return {**result, "status": "AMBIGUOUS_EXACT_PARTITION"}
        candidate = compatible[0]
        return {
            **result,
            "status": "UNIQUE_EXACT_PARTITION",
            "unique_canonical_owner_vector": candidate["canonical_owner_vector"],
            "owner_order": candidate["owner_order"],
            "complete_spans": _complete_spans(candidate),
            "cuts": candidate["cuts"],
            "run_lengths": candidate["run_lengths"],
            "unique_candidate_hash": _candidate_hash(candidate),
        }


def rc10_domain_signature(owner_count: int, atom_count: int) -> dict[str, Any]:
    solver = AnchorConstrainedPartitionSolver(owner_count, atom_count)
    return {"owner_count": owner_count, "atom_count": atom_count, "candidate_count": solver.candidate_count, "candidate_hashes": solver.candidate_hashes}
