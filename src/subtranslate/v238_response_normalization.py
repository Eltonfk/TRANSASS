"""Explicit, opt-in projection for one known V2.3.8 response defect.

This module is deliberately not a permissive JSON parser.  It accepts only a
complete response whose IDs and required values are already valid, with exactly
one item-level ``additionalProperties`` violation.  The projected response is
derived, never promoted as the raw response, and is safe to replay without a
new model transport.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from v238_per_call_durability import canonical_bytes, sha256_bytes


POLICY = "V238_ITEM_EXTRA_PROPERTY_PROJECTION_V1"


class NormalizationRejected(ValueError):
    """The response is outside the narrow deterministic projection contract."""


def _item_contract(item_id: int, expected_item_keys: Mapping[int, Sequence[str]] | None) -> set[str]:
    if expected_item_keys is None:
        return {"id", "text"}
    keys = expected_item_keys.get(item_id)
    if keys is None:
        raise NormalizationRejected("NORMALIZATION_UNKNOWN_EXPECTED_ID")
    return {str(key) for key in keys}


def _validate_scalar_fields(item: Mapping[str, Any], required: set[str]) -> None:
    if "id" not in item or isinstance(item["id"], bool) or not isinstance(item["id"], int):
        raise NormalizationRejected("NORMALIZATION_ID_TYPE_INVALID")
    if "text" in required and (not isinstance(item.get("text"), str) or not item["text"].strip()):
        raise NormalizationRejected("NORMALIZATION_TEXT_INVALID")
    if "segments" in required:
        segments = item.get("segments")
        if not isinstance(segments, list) or not segments:
            raise NormalizationRejected("NORMALIZATION_SEGMENTS_INVALID")


def project_extra_property_response(
    value: Any,
    expected_ids: Sequence[int],
    *,
    expected_item_keys: Mapping[int, Sequence[str]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a deterministic projection and a spoiler-safe audit summary.

    Every structural condition is checked before projection.  The only
    tolerated defect is one or more extra keys on exactly one item; its known
    required fields are copied byte-for-byte and all extra values are dropped.
    """
    if not isinstance(value, dict) or set(value) != {"translations"}:
        raise NormalizationRejected("NORMALIZATION_ROOT_STRUCTURE_INVALID")
    rows = value.get("translations")
    if not isinstance(rows, list):
        raise NormalizationRejected("NORMALIZATION_TRANSLATIONS_NOT_ARRAY")
    expected = [int(item_id) for item_id in expected_ids]
    expected_set = set(expected)
    if len(rows) != len(expected) or len(expected_set) != len(expected):
        raise NormalizationRejected("NORMALIZATION_CARDINALITY_INVALID")

    seen: set[int] = set()
    offenders: list[tuple[int, set[str]]] = []
    projected: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, dict):
            raise NormalizationRejected("NORMALIZATION_ITEM_NOT_OBJECT")
        if "id" not in item or isinstance(item["id"], bool) or not isinstance(item["id"], int):
            raise NormalizationRejected("NORMALIZATION_ID_TYPE_INVALID")
        item_id = int(item["id"])
        if item_id not in expected_set:
            raise NormalizationRejected("NORMALIZATION_UNKNOWN_ID")
        if item_id in seen:
            raise NormalizationRejected("NORMALIZATION_DUPLICATE_ID")
        seen.add(item_id)
        required = _item_contract(item_id, expected_item_keys)
        if "id" not in required:
            raise NormalizationRejected("NORMALIZATION_ITEM_CONTRACT_INVALID")
        _validate_scalar_fields(item, required)
        extras = set(item) - required
        if extras:
            offenders.append((item_id, extras))
        projected.append({key: item[key] for key in required})

    if seen != expected_set:
        raise NormalizationRejected("NORMALIZATION_MISSING_ID")
    if len(offenders) == 0:
        normalized = {"translations": projected}
        normalized_sha = sha256_bytes(canonical_bytes(normalized))
        return normalized, {
            "policy": POLICY,
            "raw_schema_status": "VALID",
            "derived_schema_status": "VALID_NO_PROJECTION_REQUIRED",
            "expected_count": len(expected),
            "returned_count": len(rows),
            "offending_item_count": 0,
            "extra_property_count": 0,
            "extra_property_name_hashes": [],
            "dropped_value_hashes": {},
            "raw_id_set_sha256": sha256_bytes(canonical_bytes(sorted(seen))),
            "normalized_response_sha256": normalized_sha,
        }
    if len(offenders) != 1:
        raise NormalizationRejected("NORMALIZATION_OFFENDING_ITEM_COUNT_INVALID")

    offending_id, extra_keys = offenders[0]
    normalized = {"translations": projected}
    dropped_hashes = {
        key: sha256_bytes(canonical_bytes(value["translations"][index][key]))
        for index, item in enumerate(rows)
        if isinstance(item, dict) and int(item.get("id", -1)) == offending_id
        for key in extra_keys
    }
    audit = {
        "policy": POLICY,
        "raw_schema_status": "INVALID_EXTRA_PROPERTY",
        "derived_schema_status": "VALID_AFTER_DETERMINISTIC_PROJECTION",
        "expected_count": len(expected),
        "returned_count": len(rows),
        "offending_item_count": 1,
        "offending_item_identity_hash": hashlib.sha256(str(offending_id).encode("ascii")).hexdigest(),
        "extra_property_count": len(extra_keys),
        "extra_property_name_hashes": sorted(hashlib.sha256(key.encode("utf-8")).hexdigest() for key in extra_keys),
        "dropped_value_hashes": dropped_hashes,
        "raw_id_set_sha256": sha256_bytes(canonical_bytes(sorted(seen))),
        "normalized_response_sha256": sha256_bytes(canonical_bytes(normalized)),
    }
    return normalized, audit


__all__ = ["NormalizationRejected", "POLICY", "project_extra_property_response"]
