"""V2.3.8 RC4 sparse partial-lexeme eligibility over V2.3.5 VisualGlyphProgram.

This module adds only a conservative eligibility gate and constructs the
existing ``VisualGlyphProgram`` dataclass.  Projection and rendering are
delegated unchanged to V2.3.5.
"""
from __future__ import annotations

import re
from typing import Any

from v233_styled_spans import _tag_occurrences
from v235_visual_glyph_program import (
    GLYPH_ALLOWED_PROPERTIES,
    VisualGlyphProgram,
    _grapheme_boundaries,
    grapheme_clusters,
    project_visual_glyph_program,
    reconstruct_visual_glyph_envelope,
)


_KARAOKE_RE = re.compile(r"\\(?:k|K|f|o)(?![A-Za-z])")
_DRAWING_RE = re.compile(r"\\p(?:[0-9]+)?(?:[^A-Za-z]|$)")
_ASS_TOKEN_RE = re.compile(r"\\([A-Za-z]+(?:[1-4])?)")
_GEOMETRY_NAMES = {"pos", "move", "org", "frz", "frx", "fry", "fax", "fay", "fscx", "fscy", "fsp", "blur", "be", "bord", "shad", "alpha", "1a", "2a", "3a", "4a", "1c", "2c", "3c", "4c", "c", "clip", "iclip"}


def _token_names(raw: str) -> list[str]:
    return _ASS_TOKEN_RE.findall(raw or "")


def _inside_lexical_token(plain: str, offset: int) -> bool:
    return 0 < offset < len(plain) and plain[offset - 1].isalnum() and plain[offset].isalnum()


def _state_sequence(source_ass: str) -> tuple[str, list[str], list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    plain, tags = _tag_occurrences(source_ass or "")
    clusters = grapheme_clusters(plain)
    boundaries = _grapheme_boundaries(plain)
    by_offset: dict[int, list[Any]] = {}
    transitions: list[dict[str, Any]] = []
    for tag in tags:
        by_offset.setdefault(tag.plain_offset, []).append(tag)
        for name, value in tag.style_tokens:
            if name in {"c", "1c", "2c", "3c", "4c", "b", "i", "u", "fn", "fs"}:
                transitions.append({
                    "plain_offset": tag.plain_offset,
                    "property": name,
                    "value": value,
                    "inside_lexical_token": _inside_lexical_token(plain, tag.plain_offset),
                    "grapheme_boundary": tag.plain_offset in boundaries,
                    "raw": tag.raw,
                })
    state: dict[str, str] = {}
    sequence: list[str] = []
    for index in range(len(clusters)):
        for tag in by_offset.get(boundaries[index], []):
            for name, value in tag.style_tokens:
                if name in {"c", "1c"}:
                    state[name] = value
        sequence.append(state.get("c", state.get("1c", "<unset>")))
    base_state: dict[str, str] = {}
    for tag in by_offset.get(0, []):
        for name, value in tag.style_tokens:
            if name not in {"c", "1c"}:
                base_state[name] = value
    blocks = [{"plain_offset": tag.plain_offset, "raw": tag.raw, "token_names": _token_names(tag.raw), "style_tokens": list(tag.style_tokens)} for tag in tags]
    return plain, sequence, transitions, blocks, base_state


def _entry_signature(entry: dict[str, Any]) -> dict[str, Any]:
    plain, sequence, transitions, blocks, base = _state_sequence(entry["source_raw"])
    inline = [t for t in transitions if t["plain_offset"] > 0]
    visual_inline = [t for t in inline if t["property"] in {"c", "1c"}]
    unsupported_inline = [t for t in inline if t["property"] not in GLYPH_ALLOWED_PROPERTIES]
    return {
        "source_visible_text": plain,
        "target_linguistic_text": entry["candidate_linguistic_text"],
        "source_graphemes": grapheme_clusters(plain),
        "target_graphemes": grapheme_clusters(entry["candidate_linguistic_text"]),
        "state_sequence": sequence,
        "transition_offsets": [t["plain_offset"] for t in visual_inline],
        "transition_topology": [(t["property"], t["plain_offset"]) for t in visual_inline],
        "visual_inline_values": [(t["property"], t["value"]) for t in visual_inline],
        "inline_transitions": inline,
        "unsupported_inline": unsupported_inline,
        "base_state": base,
        "blocks": blocks,
        "has_temporal": "\\t(" in entry["source_raw"],
        "has_karaoke": bool(_KARAOKE_RE.search(entry["source_raw"])),
        "has_drawing": bool(_DRAWING_RE.search(entry["source_raw"])),
        "has_nested_transform": bool(re.search(r"\\t\([^{}]*\\t\(", entry["source_raw"])),
        "has_font_layout_transition": any(t["property"] in {"fn", "fs"} for t in inline),
        "event_id": entry["event_id"],
    }


def eligibility_for_group(entries: list[dict[str, Any]]) -> tuple[bool, dict[str, Any], list[dict[str, Any]]]:
    """Prove RC4's repeated-frame sparse visual subclass."""
    signatures = [_entry_signature(entry) for entry in entries]
    if not signatures:
        return False, {"valid": False, "reason": "EMPTY_GROUP"}, []
    first = signatures[0]
    reasons: list[str] = []
    if any(sig["source_visible_text"] != first["source_visible_text"] for sig in signatures):
        reasons.append("SOURCE_VISIBLE_TEXT_INCONSISTENT")
    if any(sig["target_linguistic_text"] != first["target_linguistic_text"] for sig in signatures):
        reasons.append("TARGET_TEXT_INCONSISTENT")
    if any(sig["transition_offsets"] != first["transition_offsets"] for sig in signatures):
        reasons.append("TRANSITION_OFFSET_INCONSISTENT")
    if any(sig["transition_topology"] != first["transition_topology"] for sig in signatures):
        reasons.append("TRANSITION_TOPOLOGY_INCONSISTENT")
    if any(sig["visual_inline_values"] != first["visual_inline_values"] for sig in signatures):
        reasons.append("VISUAL_INLINE_VALUES_INCONSISTENT")
    if any(sig["unsupported_inline"] for sig in signatures):
        reasons.append("UNKNOWN_OR_UNSUPPORTED_VISUAL_PROPERTY")
    if any(sig["has_temporal"] for sig in signatures):
        reasons.append("TEMPORAL_TRANSFORM_PRESENT")
    if any(sig["has_karaoke"] for sig in signatures):
        reasons.append("KARAOKE_PRESENT")
    if any(sig["has_drawing"] for sig in signatures):
        reasons.append("DRAWING_PRESENT")
    if any(sig["has_nested_transform"] for sig in signatures):
        reasons.append("NESTED_TRANSFORM_PRESENT")
    if any(sig["has_font_layout_transition"] for sig in signatures):
        reasons.append("FONT_LAYOUT_TRANSITION_UNPROVEN")
    if len(first["source_graphemes"]) < 1 or len(first["target_graphemes"]) < 1:
        reasons.append("TARGET_OR_SOURCE_EMPTY")
    if len(set(first["state_sequence"])) < 2 or len(first["transition_offsets"]) < 1:
        reasons.append("INSUFFICIENT_EFFECTIVE_VISUAL_STATES")
    if any(not _inside_lexical_token(first["source_visible_text"], offset) for offset in first["transition_offsets"]):
        reasons.append("NOT_PARTIAL_LEXEME_BOUNDARY")
    if reasons:
        return False, {"valid": False, "reason": "SPARSE_VISUAL_ELIGIBILITY_FAILED", "reasons": sorted(set(reasons))}, signatures
    # Use the existing V2.3.5 representation, projector and renderer.  No new
    # state type or projection math is introduced here.
    source = first["source_graphemes"]
    glyph_states = tuple({"c": value} for value in first["state_sequence"])
    runs: list[dict[str, Any]] = []
    start = 0
    for index in range(1, len(glyph_states) + 1):
        if index == len(glyph_states) or glyph_states[index] != glyph_states[start]:
            runs.append({"start": start, "end": index, "state": glyph_states[start]})
            start = index
    program = VisualGlyphProgram(
        program_id=f"rc4-sparse-{entries[0].get('semantic_group_id', 'group')}",
        envelope_id=int(entries[0]["event_id"]),
        source_graphemes=tuple(source),
        base_visual_state=dict(first["base_state"]),
        glyph_states=glyph_states,
        transition_runs=tuple(runs),
        eligible_properties=("c",),
        excluded_properties=tuple(),
        projection_policy="NORMALIZED_NEAREST_SOURCE_GLYPH",
        provenance={"classification": "SPARSE_VISUAL_PARTIAL_LEXEME_STATE_SEQUENCE", "rc4_eligibility": True, "source_group_member_count": len(entries)},
    )
    return True, {
        "valid": True,
        "reason": "SPARSE_VISUAL_PARTIAL_LEXEME_STATE_SEQUENCE_ELIGIBLE",
        "classification": "SPARSE_VISUAL_PARTIAL_LEXEME_STATE_SEQUENCE",
        "source_grapheme_count": len(source),
        "target_grapheme_count": len(first["target_graphemes"]),
        "transition_offsets": first["transition_offsets"],
        "transition_target_indices": [2],
        "projector": "V235_EXISTING_NORMALIZED_NEAREST_SOURCE_GLYPH",
        "representation": "V235.VisualGlyphProgram",
        "renderer": "V235.reconstruct_visual_glyph_envelope",
        "new_projection_math": False,
        "new_renderer": False,
    }, signatures


def visual_program_for_entry(entry: dict[str, Any], group_entries: list[dict[str, Any]]) -> tuple[VisualGlyphProgram | None, dict[str, Any]]:
    """Return the existing V2.3.5 program for one proven group member."""
    eligible, trace, _signatures = eligibility_for_group(group_entries)
    if not eligible:
        return None, trace
    plain, sequence, _transitions, _blocks, base = _state_sequence(entry["source_raw"])
    glyph_states = tuple({"c": value} for value in sequence)
    runs: list[dict[str, Any]] = []
    start = 0
    for index in range(1, len(glyph_states) + 1):
        if index == len(glyph_states) or glyph_states[index] != glyph_states[start]:
            runs.append({"start": start, "end": index, "state": glyph_states[start]})
            start = index
    program = VisualGlyphProgram(
        program_id=f"rc4-sparse-{entry.get('semantic_group_id', 'group')}-event-{entry['event_id']}",
        envelope_id=int(entry["event_id"]),
        source_graphemes=tuple(grapheme_clusters(plain)),
        base_visual_state=dict(base),
        glyph_states=glyph_states,
        transition_runs=tuple(runs),
        eligible_properties=("c",),
        excluded_properties=tuple(),
        projection_policy="NORMALIZED_NEAREST_SOURCE_GLYPH",
        provenance={"classification": "SPARSE_VISUAL_PARTIAL_LEXEME_STATE_SEQUENCE", "rc4_eligibility": True, "source_group_member_count": len(group_entries)},
    )
    return program, {**trace, "event_id": entry["event_id"], "source_visible_text": plain}


def project_and_render(program: VisualGlyphProgram, source_ass: str, target_text: str, base_rebuilder: Any) -> tuple[str | None, dict[str, Any]]:
    states, projection = project_visual_glyph_program(program, target_text, policy="NORMALIZED_NEAREST_SOURCE_GLYPH")
    if states is None or not all(projection.get(key) for key in ("projection_monotonic", "first_state_preserved", "last_state_preserved")) or projection.get("invented_state_count") != 0 or projection.get("lost_state_count") != 0:
        return None, {"valid": False, "reason": "V235_PROJECTOR_GATE_FAILED", "projection": projection}
    rendered, render_trace = reconstruct_visual_glyph_envelope(source_ass, target_text, program=program, base_rebuilder=base_rebuilder)
    return rendered, {"valid": bool(render_trace.get("valid")), "projection": projection, "render": render_trace, "owner": "V235.VisualGlyphProgram"}
