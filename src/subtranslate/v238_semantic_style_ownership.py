"""V2.3.8 semantic style ownership and explicit layer composition.

This module owns static style states that follow a semantic target run.  It
does not translate text, project source offsets, resample graphemes, or own
temporal/glyph programs.  ASS values are copied from the source envelope and
the strict ownership response partitions an already-approved target string.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable

from v233_styled_spans import TAG_GROUP_RE, STYLE_TOKEN_RE, _tag_occurrences, strip_ass_tags
from v237_temporal_transform import (
    TEMPORAL_EVENT_GLOBAL,
    parse_temporal_transform_program,
    preserve_temporal_transform_envelope,
    temporal_ast_equal,
)
from v238_source_payload import rc4_replace_source_payload


SEMANTIC_STYLE_PROPERTIES = frozenset({"c", "1c", "2c", "3c", "4c", "b", "i", "u", "fn", "fs"})
OWNERSHIP_SCHEMA = {
    "type": "object",
    "properties": {
        "ownership_runs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "owner_segment_id": {"type": "string"},
                },
                "required": ["text", "owner_segment_id"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["ownership_runs"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class SemanticStyleSegment:
    segment_id: str
    source_text: str
    source_plain_start: int
    source_plain_end: int
    effective_style_state: dict[str, str]
    source_order: int
    provenance: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SemanticStyleOwnershipProgram:
    program_id: str
    envelope_id: int
    source_visible_text: str
    base_state: dict[str, str]
    source_semantic_segments: tuple[SemanticStyleSegment, ...]
    semantic_properties: tuple[str, ...]
    identity_eligible: bool
    provenance: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["source_semantic_segments"] = [item.to_dict() for item in self.source_semantic_segments]
        value["semantic_properties"] = list(self.semantic_properties)
        return value


def _style_token_rows(raw: str) -> list[tuple[str, str, tuple[int, int]]]:
    rows: list[tuple[str, str, tuple[int, int]]] = []
    for match in STYLE_TOKEN_RE.finditer(raw or ""):
        if match.group("color") is not None:
            rows.append((match.group("color"), match.group("color_value") or "", match.span()))
        elif match.group("toggle") is not None:
            rows.append((match.group("toggle"), match.group("toggle_value") or "", match.span()))
        elif match.group("font") is not None:
            rows.append((match.group("font"), match.group("font_value") or "", match.span()))
        elif match.group("size") is not None:
            rows.append((match.group("size"), match.group("size_value") or "", match.span()))
    return rows


def _reset_state(state: dict[str, str], base_state: dict[str, str], name: str) -> None:
    if name in base_state:
        state[name] = base_state[name]
    else:
        state.pop(name, None)


def _apply_token(state: dict[str, str], base_state: dict[str, str], name: str, value: str) -> None:
    # ASS's empty colour/font reset means the style default.  For ownership,
    # returning to an event-wide base state is represented by that base value;
    # otherwise the property is removed and the envelope's style default owns
    # it.  Toggle 0 is also a reset-to-default in the existing contract.
    if not value or (name in {"b", "i", "u"} and value == "0"):
        _reset_state(state, base_state, name)
    else:
        state[name] = value


def _plain_text(source_ass: str) -> str:
    return TAG_GROUP_RE.sub("", source_ass or "")


def extract_semantic_style_ownership(
    source_ass: str,
    *,
    program_id: str,
    envelope_id: int,
) -> tuple[SemanticStyleOwnershipProgram | None, dict[str, Any]]:
    """Extract effective static states and semantic transition segments.

    Every source text interval is assigned a stable segment ID.  Only inline
    transitions after the leading event state make this an ownership program;
    an envelope-wide style alone remains ordinary ASS ownership.
    """
    plain, tags = _tag_occurrences(source_ass or "")
    base_state: dict[str, str] = {}
    transitions: dict[int, list[tuple[str, str, str]]] = {}
    properties: set[str] = set()
    for tag in tags:
        rows = _style_token_rows(tag.raw)
        for name, value, _span in rows:
            if name not in SEMANTIC_STYLE_PROPERTIES:
                continue
            properties.add(name)
            if tag.plain_offset == 0:
                _apply_token(base_state, base_state, name, value)
            else:
                transitions.setdefault(tag.plain_offset, []).append((name, value, tag.raw))
    if not transitions:
        return None, {
            "valid": False,
            "reason": "NO_INLINE_SEMANTIC_STYLE_TRANSITION",
            "source_visible_text": plain,
            "base_state": base_state,
        }

    state = dict(base_state)
    boundaries = sorted({0, len(plain), *transitions})
    segments: list[SemanticStyleSegment] = []
    transition_trace: list[dict[str, Any]] = []
    for start, end in zip(boundaries, boundaries[1:]):
        # Transitions at the current boundary take effect for this interval.
        if start in transitions and start != 0:
            for name, value, raw in transitions[start]:
                before = state.get(name)
                _apply_token(state, base_state, name, value)
                transition_trace.append({
                    "plain_offset": start,
                    "property": name,
                    "before": before,
                    "after": state.get(name),
                    "raw_tag": raw,
                })
        text = plain[start:end]
        # Consecutive boundaries that leave the effective state unchanged are
        # folded; this keeps runs minimal without losing source provenance.
        if segments and segments[-1].effective_style_state == dict(state):
            previous = segments[-1]
            segments[-1] = SemanticStyleSegment(
                segment_id=previous.segment_id,
                source_text=plain[previous.source_plain_start:end],
                source_plain_start=previous.source_plain_start,
                source_plain_end=end,
                effective_style_state=dict(state),
                source_order=previous.source_order,
                provenance=previous.provenance,
            )
            continue
        ordinal = len(segments) + 1
        segments.append(SemanticStyleSegment(
            segment_id=f"{program_id}:segment-{ordinal}",
            source_text=text,
            source_plain_start=start,
            source_plain_end=end,
            effective_style_state=dict(state),
            source_order=ordinal,
            provenance={"source_boundary": start, "transition_count_before": len(transition_trace)},
        ))
    if len(segments) < 2:
        return None, {"valid": False, "reason": "NO_EFFECTIVE_STATE_CHANGE", "source_visible_text": plain}
    program = SemanticStyleOwnershipProgram(
        program_id=program_id,
        envelope_id=int(envelope_id),
        source_visible_text=plain,
        base_state=dict(base_state),
        source_semantic_segments=tuple(segments),
        semantic_properties=tuple(sorted(properties)),
        identity_eligible=True,
        provenance={
            "classification": "SPARSE_MULTI_TRANSITION_SEMANTIC_STYLE_OWNERSHIP",
            "static": True,
            "temporal_owned_elsewhere": True,
            "visual_glyph_owned_elsewhere": True,
            "transition_trace": transition_trace,
        },
    )
    return program, {
        "valid": True,
        "reason": "SEMANTIC_STYLE_OWNERSHIP_PROGRAM",
        "source_visible_text": plain,
        "base_state": dict(base_state),
        "segment_count": len(segments),
        "transition_count": len(transition_trace),
        "semantic_properties": sorted(properties),
    }


def identity_ownership_mapping(
    program: SemanticStyleOwnershipProgram,
    target_text: str,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Create a mapping only under exact source/target Unicode identity."""
    if program.source_visible_text != target_text:
        return None, {"valid": False, "reason": "IDENTITY_TEXT_MISMATCH"}
    rows = [
        {"text": segment.source_text, "owner_segment_id": segment.segment_id}
        for segment in program.source_semantic_segments
    ]
    value = {"ownership_runs": rows}
    return value, {"valid": True, "reason": "DETERMINISTIC_IDENTITY_MAPPING", "run_count": len(rows)}


def validate_ownership_mapping(
    program: SemanticStyleOwnershipProgram,
    target_text: str,
    value: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Strictly validate a full target partition and owner set."""
    expected = {segment.segment_id for segment in program.source_semantic_segments}
    trace: dict[str, Any] = {
        "schema": "v238_semantic_style_ownership.v1",
        "target_text": target_text,
        "expected_segment_ids": sorted(expected),
    }
    if not isinstance(value, dict) or set(value) != {"ownership_runs"} or not isinstance(value.get("ownership_runs"), list):
        return [], {**trace, "valid": False, "reason": "STRICT_SCHEMA_KEYS"}
    rows = value["ownership_runs"]
    if not rows:
        return [], {**trace, "valid": False, "reason": "EMPTY_OWNERSHIP_RUNS"}
    seen: set[str] = set()
    concatenated: list[str] = []
    validated: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != {"text", "owner_segment_id"}:
            return [], {**trace, "valid": False, "reason": "STRICT_SCHEMA_KEYS", "row_index": index}
        text, owner = row["text"], row["owner_segment_id"]
        if not isinstance(text, str) or not isinstance(owner, str):
            return [], {**trace, "valid": False, "reason": "STRICT_SCHEMA_TYPES", "row_index": index}
        if not text:
            return [], {**trace, "valid": False, "reason": "EMPTY_TARGET_RUN", "row_index": index}
        if owner not in expected:
            return [], {**trace, "valid": False, "reason": "UNKNOWN_OWNER_SEGMENT", "owner_segment_id": owner}
        if validated and validated[-1]["owner_segment_id"] == owner:
            return [], {**trace, "valid": False, "reason": "ADJACENT_DUPLICATE_OWNER_RUN", "owner_segment_id": owner}
        seen.add(owner)
        concatenated.append(text)
        validated.append({"text": text, "owner_segment_id": owner, "run_index": index})
    joined = "".join(concatenated)
    if joined != target_text:
        return [], {**trace, "valid": False, "reason": "TARGET_CONCATENATION_MISMATCH", "actual": joined}
    if seen != expected:
        return [], {**trace, "valid": False, "reason": "MISSING_OWNER_SEGMENT", "actual_segment_ids": sorted(seen)}
    return validated, {
        **trace,
        "valid": True,
        "reason": "TARGET_OWNERSHIP_PARTITION_VALID",
        "run_count": len(validated),
        "owner_segment_ids": [item["owner_segment_id"] for item in validated],
    }


def _token_text(name: str, value: str | None) -> str:
    if name in {"c", "1c", "2c", "3c", "4c"}:
        return "\\" + name + (value or "")
    if name in {"b", "i", "u", "fs"}:
        return "\\" + name + (value or "")
    if name == "fn":
        return "\\fn" + (value or "")
    return "\\" + name + (value or "")


def _state_delta(previous: dict[str, str], current: dict[str, str], base: dict[str, str]) -> list[str]:
    tokens: list[str] = []
    for name in sorted(set(previous) | set(current)):
        before = previous.get(name)
        after = current.get(name)
        if before == after:
            continue
        if name not in current:
            # Explicitly return to the source base when available; otherwise
            # use the ASS reset form.  Values are never invented.
            tokens.append(_token_text(name, base.get(name, "")))
        else:
            tokens.append(_token_text(name, after))
    return tokens


def _remove_inline_semantic_tokens(source_ass: str) -> str:
    """Remove only post-text ownership tokens, retaining all other ASS."""
    plain, tags = _tag_occurrences(source_ass or "")
    by_raw_offset = {(tag.raw, tag.plain_offset, tag.index): tag for tag in tags}
    cursor = 0
    out: list[str] = []
    for index, match in enumerate(TAG_GROUP_RE.finditer(source_ass or "")):
        out.append((source_ass or "")[cursor:match.start()])
        raw = match.group(0)
        tag = next((item for item in tags if item.index == index), None)
        if tag is None or tag.plain_offset == 0:
            out.append(raw)
        else:
            body = raw[1:-1]
            for token in reversed(list(STYLE_TOKEN_RE.finditer(body))):
                body = body[: token.start()] + body[token.end() :]
            if body:
                out.append("{" + body + "}")
        cursor = match.end()
    out.append((source_ass or "")[cursor:])
    return "".join(out)


def render_target_ownership(
    source_ass: str,
    target_text: str,
    program: SemanticStyleOwnershipProgram,
    mapping: Any,
) -> tuple[str | None, dict[str, Any]]:
    """Emit deterministic target runs while preserving nonsemantic ASS."""
    rows, validation = validate_ownership_mapping(program, target_text, mapping)
    if not validation.get("valid"):
        return None, validation
    segment_map = {segment.segment_id: segment for segment in program.source_semantic_segments}
    envelope = _remove_inline_semantic_tokens(source_ass)
    # Leading ASS blocks are the only prefix before visible text in all
    # ownership programs.  Keep them in place and append target text after the
    # prefix; nonsemantic tags that occur later remain in their source scope.
    leading = "".join(match.group(0) for match in TAG_GROUP_RE.finditer(envelope) if match.start() == 0)
    suffixless = envelope[len(leading):] if envelope.startswith(leading) else envelope
    # Source ownership programs are only eligible when inline semantic tokens
    # are the movable state.  Any remaining visible source payload is removed;
    # nonsemantic tag scopes are retained only when they are leading.
    if TAG_GROUP_RE.search(suffixless):
        # Preserve a bounded nonsemantic suffix only if it has no visible text;
        # otherwise scope ownership is not proven by this renderer.
        if re.sub(r"\{[^{}]*\}", "", suffixless):
            return None, {"valid": False, "reason": "NONSEMANTIC_TEXT_SCOPE_NOT_PROVEN"}
        suffixless = ""
    current = dict(program.base_state)
    output: list[str] = [leading]
    for row in rows:
        segment = segment_map[row["owner_segment_id"]]
        delta = _state_delta(current, segment.effective_style_state, program.base_state)
        if delta:
            output.append("{" + "".join(delta) + "}")
        output.append(row["text"])
        current = dict(segment.effective_style_state)
    result = "".join(output)
    if strip_ass_tags(result) != target_text:
        return None, {"valid": False, "reason": "TARGET_TEXT_IDENTITY", "actual": strip_ass_tags(result)}
    return result, {
        "valid": True,
        "reason": "SEMANTIC_STYLE_OWNERSHIP_RENDERED",
        "target_text_identity": True,
        "owner_run_count": len(rows),
        "semantic_style_properties": list(program.semantic_properties),
    }


def temporal_semantic_composition_trace(
    source_ass: str,
    final_ass_without_semantic_tags: str,
) -> dict[str, Any]:
    """Prove temporal AST preservation independently of static ownership."""
    source_program, source_trace = parse_temporal_transform_program(_remove_inline_semantic_tokens(source_ass))
    final_program, final_trace = parse_temporal_transform_program(final_ass_without_semantic_tags)
    if source_program is None or final_program is None:
        return {
            "valid": False,
            "reason": "TEMPORAL_CLASS_A_NOT_PROVEN",
            "source_trace": source_trace,
            "final_trace": final_trace,
        }
    equal = temporal_ast_equal(source_program, final_program)
    return {
        "valid": bool(equal and source_trace.get("classification") == TEMPORAL_EVENT_GLOBAL and final_trace.get("classification") == TEMPORAL_EVENT_GLOBAL),
        "reason": "TEMPORAL_AST_IDENTICAL" if equal else "TEMPORAL_AST_MISMATCH",
        "source_trace": source_trace,
        "final_trace": final_trace,
        "ast_equal": bool(equal),
        "t1_t2_accel_modifiers_preserved": bool(equal),
    }


def compose_temporal_class_a(source_ass: str, target_text: str) -> tuple[str | None, dict[str, Any]]:
    """Compose known class-A temporal envelopes over the full multiline text."""
    result, trace = preserve_temporal_transform_envelope(
        source_ass,
        target_text,
        base_rebuilder=rc4_replace_source_payload,
    )
    if result is None:
        return None, trace
    return result, {**trace, "owner": "TemporalAssTransformProgram", "semantic_style_ownership": False}


def build_alignment_request(program: SemanticStyleOwnershipProgram, target_text: str) -> dict[str, Any]:
    """Build the future model-visible payload; no ASS is included."""
    return {
        "task": "SEMANTIC_STYLE_OWNERSHIP",
        "candidate_linguistic_text": target_text,
        "source_semantic_segments": [
            {"segment_id": segment.segment_id, "source_text": segment.source_text}
            for segment in program.source_semantic_segments
        ],
        "target_locale": "pt-BR",
        "format_schema": OWNERSHIP_SCHEMA,
    }
