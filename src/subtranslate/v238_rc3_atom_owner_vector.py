"""V2.3.8 RC3 deterministic target atoms and integer owner vectors."""
from __future__ import annotations

import json
import unicodedata
from typing import Any

import jsonschema


ATOM_VECTOR_SCHEMA = {
    "type": "array",
    "minItems": 1,
    "items": {"type": "integer", "minimum": 1},
}


def _lexical_char(ch: str) -> bool:
    return bool(ch) and (ch.isalnum() or unicodedata.category(ch) in {"Mn", "Mc", "Me"})


def _connector_inside(text: str, index: int) -> bool:
    if index <= 0 or index >= len(text) - 1:
        return False
    return text[index] in {"-", "‐", "‑", "–", "—", "'", "’", "_"} and _lexical_char(text[index - 1]) and _lexical_char(text[index + 1])


def lexical_token_spans(text: str) -> list[tuple[int, int]]:
    """Find lexical spans without model tokenization.

    Internal hyphen/apostrophe connectors stay in one lexical token.  All
    separators outside tokens are retained by the surrounding atom.
    """
    if not text:
        return []
    spans: list[tuple[int, int]] = []
    index = 0
    while index < len(text):
        if not _lexical_char(text[index]):
            index += 1
            continue
        start = index
        index += 1
        while index < len(text):
            if _lexical_char(text[index]) or _connector_inside(text, index):
                index += 1
                continue
            break
        spans.append((start, index))
    return spans


def target_atoms(text: str) -> list[str]:
    """Create reversible atoms; each lexical token owns following separators."""
    if not isinstance(text, str):
        raise TypeError("TARGET_TEXT_MUST_BE_STRING")
    spans = lexical_token_spans(text)
    if not spans:
        return [text] if text else []
    atoms: list[str] = []
    cursor = 0
    for position, (start, end) in enumerate(spans):
        # Leading punctuation/separators attach to the first token; the gap
        # after each token attaches to that token until the next token.
        atom_start = cursor
        atom_end = spans[position + 1][0] if position + 1 < len(spans) else len(text)
        atoms.append(text[atom_start:atom_end])
        cursor = atom_end
    if "".join(atoms) != text:
        raise AssertionError("ATOMIZATION_NOT_REVERSIBLE")
    return atoms


def strict_atom_vector_schema(atom_count: int) -> dict[str, Any]:
    schema = json.loads(json.dumps(ATOM_VECTOR_SCHEMA))
    schema["minItems"] = atom_count
    schema["maxItems"] = atom_count
    return schema


def validate_owner_vector(
    value: Any,
    *,
    atoms: list[str],
    canonical_segment_ids: list[str],
    target_text: str,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    trace: dict[str, Any] = {
        "schema": "v238_rc3_atom_owner_vector.v1",
        "atom_count": len(atoms),
        "owner_count": len(canonical_segment_ids),
        "target_text": target_text,
        "atoms": list(atoms),
    }
    if not atoms or "".join(atoms) != target_text:
        return None, {**trace, "valid": False, "reason": "TARGET_ATOM_CONCATENATION_INVALID"}
    schema = strict_atom_vector_schema(len(atoms))
    try:
        jsonschema.Draft7Validator(schema).validate(value)
    except jsonschema.ValidationError as exc:
        return None, {**trace, "valid": False, "reason": "STRICT_ATOM_VECTOR_SCHEMA", "detail": exc.message}
    if any(isinstance(owner, bool) or not isinstance(owner, int) for owner in value):
        return None, {**trace, "valid": False, "reason": "OWNER_INDEX_NOT_INTEGER"}
    if any(owner < 1 or owner > len(canonical_segment_ids) for owner in value):
        return None, {**trace, "valid": False, "reason": "OWNER_INDEX_OUT_OF_RANGE"}
    used = set(value)
    expected = set(range(1, len(canonical_segment_ids) + 1))
    if used != expected:
        return None, {**trace, "valid": False, "reason": "MISSING_REQUIRED_OWNER", "used": sorted(used)}
    runs: list[dict[str, Any]] = []
    for atom, owner in zip(atoms, value):
        if runs and runs[-1]["owner_index"] == owner:
            runs[-1]["text"] += atom
            runs[-1]["atom_end"] += 1
        else:
            runs.append({"owner_index": owner, "text": atom, "atom_start": len(runs) and runs[-1]["atom_end"] or 0, "atom_end": (len(runs) and runs[-1]["atom_end"] or 0) + 1})
    internal = {
        "ownership_runs": [
            {"text": run["text"], "owner_segment_id": canonical_segment_ids[run["owner_index"] - 1]}
            for run in runs
        ]
    }
    trace.update({
        "valid": True,
        "reason": "ATOM_OWNER_VECTOR_VALID",
        "owner_vector": list(value),
        "used_owner_indexes": sorted(used),
        "owner_index_mapping": [
            {"owner_index": owner, "canonical_segment_id": canonical_segment_ids[owner - 1], "atom": atom}
            for atom, owner in zip(atoms, value)
        ],
        "internal_runs": runs,
        "target_text_identity": "".join(atoms) == target_text,
    })
    return internal, trace


def build_atom_vector_request(*, semantic_group_id: str, target_text: str, source_segments: list[dict[str, str]], schema: dict[str, Any], model: str) -> dict[str, Any]:
    atoms = target_atoms(target_text)
    owners = [{"owner_index": i, "source_text": seg["source_text"]} for i, seg in enumerate(source_segments, 1)]
    contract = {
        "semantic_group_id": semantic_group_id,
        "task": "ATOM_OWNER_VECTOR_ONLY",
        "source_owners": owners,
        "target_atoms": [{"atom_index": i, "text": atom} for i, atom in enumerate(atoms, 1)],
        "approved_target_linguistic_text": target_text,
        "target_locale": "pt-BR",
        "rules": [
            "Return only a JSON root array of integers.",
            "The array length must equal the target atom count.",
            "Each integer assigns that immutable target atom to one 1-based owner_index.",
            "Do not return text, objects, tuples, keys, or translated content.",
            "Use every source owner exactly once; owner order may follow target semantics.",
        ],
    }
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": "Return only the strict integer owner vector. Do not return target text."},
            {"role": "user", "content": json.dumps(contract, ensure_ascii=False, separators=(",", ":"))},
        ],
        "format": schema,
        "options": {"temperature": 0.0, "num_ctx": 2560, "num_predict": 384},
        "stream": False,
        "think": False,
        "keep_alive": "30m",
    }
