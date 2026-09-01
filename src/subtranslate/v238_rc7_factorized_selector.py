"""V2.3.8 RC7 factorized finite semantic selector.

The selector is deliberately model-facing only: code enumerates complete
owner-order and positive-composition domains; the model returns one opaque
choice ID per stage.  No target text is produced by the model.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import random
import re
from typing import Any

import jsonschema

from v238_rc3_atom_owner_vector import target_atoms
from v238_rc6_finite_selector import MAX_FINITE_SELECTOR_CANDIDATES, NONE_OF_THE_ABOVE, positive_compositions

CHOICE_SCHEMA = {"type": "array", "minItems": 1, "maxItems": 1, "items": {"type": "integer", "minimum": 1}}
_CANONICAL_CHOICE_KEY = re.compile(r"[1-9][0-9]*\Z")


def normalize_choice_map(raw_map: dict[Any, Any], *, expected_ids: set[int] | None = None, field: str = "choice_to_item") -> dict[int, Any]:
    """Normalize persisted JSON choice-map keys at the deserialization boundary.

    JSON object keys are strings.  Only canonical positive decimal spellings
    are accepted; ambiguous spellings, collisions and domain mismatches fail
    closed before any parser/canonicalizer sees the map.
    """
    if not isinstance(raw_map, dict):
        raise ValueError(f"RC7_CHOICE_MAP_NOT_OBJECT:{field}")
    normalized: dict[int, Any] = {}
    for key, value in raw_map.items():
        if not isinstance(key, str) or _CANONICAL_CHOICE_KEY.fullmatch(key) is None:
            raise ValueError(f"RC7_CHOICE_MAP_KEY_NOT_CANONICAL:{field}:{key!r}")
        choice_id = int(key)
        if choice_id in normalized:
            raise ValueError(f"RC7_CHOICE_MAP_KEY_COLLISION:{field}:{choice_id}")
        normalized[choice_id] = value
    if expected_ids is not None and set(normalized) != set(expected_ids):
        raise ValueError(
            f"RC7_CHOICE_MAP_DOMAIN_MISMATCH:{field}:"
            f"expected={sorted(expected_ids)}:actual={sorted(normalized)}"
        )
    return normalized


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def generate_owner_orders(owner_count: int, *, max_choices: int = MAX_FINITE_SELECTOR_CANDIDATES) -> list[dict[str, Any]]:
    if owner_count < 1:
        raise ValueError("RC7_OWNER_COUNT_INVALID")
    orders = list(itertools.permutations(range(1, owner_count + 1)))
    if len(orders) > max_choices:
        raise ValueError("RC7_ORDER_SPACE_TOO_LARGE")
    return [{
        "canonical_choice_hash": _hash({"owner_order": list(order)}),
        "canonical_owner_order": list(order),
        "source_owner_count": owner_count,
        "provenance": {"generator": "all_owner_permutations", "count": len(orders)},
    } for order in orders]


def generate_compositions(atom_count: int, owner_count: int, *, max_choices: int = MAX_FINITE_SELECTOR_CANDIDATES) -> list[dict[str, Any]]:
    if atom_count < owner_count:
        raise ValueError("RC7_COMPOSITION_M_LT_N")
    compositions = list(positive_compositions(atom_count, owner_count))
    if len(compositions) > max_choices:
        raise ValueError("RC7_COMPOSITION_SPACE_TOO_LARGE")
    return [{
        "canonical_choice_hash": _hash({"run_lengths": list(lengths)}),
        "run_lengths": list(lengths),
        "atom_count": atom_count,
        "source_owner_count": owner_count,
        "provenance": {"generator": "all_positive_compositions", "count": len(compositions)},
    } for lengths in compositions]


def factorized_candidates(owner_count: int, atom_count: int, *, max_candidates: int = MAX_FINITE_SELECTOR_CANDIDATES) -> list[dict[str, Any]]:
    orders = generate_owner_orders(owner_count, max_choices=max_candidates)
    compositions = generate_compositions(atom_count, owner_count, max_choices=max_candidates)
    product = len(orders) * len(compositions)
    if product > max_candidates:
        raise ValueError("RC7_FACTORIZED_SPACE_TOO_LARGE")
    return [
        {
            "canonical_candidate_hash": _hash({"owner_order": order["canonical_owner_order"], "run_lengths": composition["run_lengths"]}),
            "canonical_owner_order": order["canonical_owner_order"],
            "run_lengths": composition["run_lengths"],
            "atom_count": atom_count,
            "source_owner_count": owner_count,
            "provenance": {"factorized": True, "order_hash": order["canonical_choice_hash"], "composition_hash": composition["canonical_choice_hash"]},
        }
        for order in orders for composition in compositions
    ]


def expand_factorized(order: list[int], lengths: list[int]) -> list[int]:
    if len(order) != len(lengths) or any(int(n) < 1 for n in lengths):
        raise ValueError("RC7_FACTOR_LENGTH_INVALID")
    return [owner for owner, count in zip(order, lengths) for _ in range(count)]


def owner_ranges(order: list[int], lengths: list[int]) -> list[dict[str, int]]:
    cursor = 1
    result = []
    for owner, count in zip(order, lengths):
        result.append({"owner": owner, "start": cursor, "end": cursor + count - 1, "count": count})
        cursor += count
    return result


def factorized_vector(order: list[int], lengths: list[int]) -> list[int]:
    return expand_factorized(order, lengths)


def make_stage_presentation(items: list[dict[str, Any]], *, seed: int, owner_count: int, owner_labels: dict[int, int] | None = None) -> dict[str, Any]:
    rng = random.Random(seed)
    order = list(range(len(items)))
    rng.shuffle(order)
    choice_to_item = {i + 1: items[index] for i, index in enumerate(order)}
    canonical_to_presented = dict(owner_labels) if owner_labels else {i: i for i in range(1, owner_count + 1)}
    label_for = {i: chr(64 + canonical_to_presented[i]) for i in range(1, owner_count + 1)}
    catalog = []
    for choice_id, item in choice_to_item.items():
        if "canonical_owner_order" in item:
            body = " ".join(label_for[o] for o in item["canonical_owner_order"])
        else:
            ranges = owner_ranges(item["canonical_owner_order_fixed"], item["run_lengths"])
            body = " ".join(f"{label_for[r['owner']]}[{r['start']}-{r['end']}]" for r in ranges)
        catalog.append(f"CHOICE {choice_id}: {body}")
    return {
        "presentation_seed": seed,
        "choice_to_canonical_hash": {k: item["canonical_choice_hash"] if "canonical_choice_hash" in item else item["canonical_candidate_hash"] for k, item in choice_to_item.items()},
        "choice_to_item": choice_to_item,
        "catalog_order": [item["canonical_choice_hash"] if "canonical_choice_hash" in item else item["canonical_candidate_hash"] for item in choice_to_item.values()],
        "canonical_to_presented": canonical_to_presented,
        "catalog": catalog,
        "none_choice_id": len(items) + 1,
        "owner_count": owner_count,
    }


def make_composition_presentation(compositions: list[dict[str, Any]], *, fixed_order: list[int], seed: int, owner_labels: dict[int, int] | None = None) -> dict[str, Any]:
    items = []
    for composition in compositions:
        item = dict(composition)
        item["canonical_owner_order_fixed"] = list(fixed_order)
        items.append(item)
    return make_stage_presentation(items, seed=seed, owner_count=len(fixed_order), owner_labels=owner_labels)


def build_stage_request(*, stage: str, semantic_group_id: str, owners: list[dict[str, str]], target: str, presentation: dict[str, Any], model: str, fixed_order: list[int] | None = None) -> dict[str, Any]:
    atoms = target_atoms(target)
    labels = presentation["canonical_to_presented"]
    source = "\n".join(f"OWNER {chr(64 + labels[i])}: {owner['source_text']}" for i, owner in enumerate(owners, 1))
    user = [
        f"SEMANTIC_GROUP: {semantic_group_id}",
        f"TASK: SELECT ONE {stage.upper()} CANDIDATE; DO NOT BUILD A MAPPING.",
        "SOURCE OWNERS:", source,
        "TARGET ATOMS (immutable, shown once):",
        *[f"ATOM {i}: {atom}" for i, atom in enumerate(atoms, 1)],
    ]
    if fixed_order is not None:
        user.append("FIXED CANONICAL OWNER ORDER FOR THIS STAGE: " + " ".join(chr(64 + labels[o]) for o in fixed_order))
    user.extend(["COMPLETE CANDIDATE CATALOG:", *presentation["catalog"], f"NONE CHOICE: {presentation['none_choice_id']}", "Return only a JSON root array containing exactly one integer choice ID. Choose NONE only if no listed candidate is semantically correct."])
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": "Select exactly one finite candidate. Return only [choice_id]. Do not return mappings, text, or objects."},
            {"role": "user", "content": "\n".join(user)},
        ],
        "format": json.loads(json.dumps(CHOICE_SCHEMA)),
        "options": {"temperature": 0.0, "num_ctx": 2560, "num_predict": 32},
        "stream": False,
        "think": False,
        "keep_alive": "30m",
    }


def validate_stage_choice(value: Any, presentation: dict[str, Any]) -> tuple[int | None, dict[str, Any]]:
    try:
        jsonschema.Draft7Validator(CHOICE_SCHEMA).validate(value)
    except jsonschema.ValidationError as exc:
        return None, {"valid": False, "reason": "STRICT_RC7_CHOICE_SCHEMA", "detail": exc.message}
    choice = int(value[0])
    if choice == presentation["none_choice_id"]:
        return None, {"valid": False, "reason": NONE_OF_THE_ABOVE}
    if choice not in presentation["choice_to_item"]:
        return None, {"valid": False, "reason": "UNKNOWN_CHOICE_ID", "choice_id": choice}
    choice_hashes = presentation["choice_to_canonical_hash"]
    if choice not in choice_hashes:
        return None, {"valid": False, "reason": "CHOICE_HASH_MAP_MISSING", "choice_id": choice}
    return choice, {"valid": True, "reason": "RC7_CHOICE_VALID", "choice_id": choice, "canonical_hash": choice_hashes[choice]}


def equivalent_rc6_set(owner_count: int, atom_count: int) -> tuple[set[tuple[int, ...]], set[tuple[int, ...]]]:
    from v238_rc6_finite_selector import generate_candidates
    rc6 = {tuple(c["canonical_owner_vector"]) for c in generate_candidates(owner_count, atom_count)}
    rc7 = {tuple(factorized_vector(c["canonical_owner_order"], c["run_lengths"])) for c in factorized_candidates(owner_count, atom_count)}
    return rc6, rc7
