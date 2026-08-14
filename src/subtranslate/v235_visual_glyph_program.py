"""V2.3.5 deterministic visual-glyph programs.

This is deliberately distinct from semantic styled-span alignment.  It accepts
only dense, static primary-colour sequences proven to cover visual graphemes;
the target linguistic text is immutable and no model is consulted.
"""
from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any, Callable

from v233_styled_spans import TAG_GROUP_RE, STYLE_TOKEN_RE, _tag_occurrences, strip_ass_tags

PRIMARY_NAMES = {"c", "1c"}
GLYPH_ALLOWED_PROPERTIES = {"c", "1c"}


def grapheme_clusters(value: str) -> list[str]:
    """A bounded UAX #29-inspired segmentation without an undeclared package.

    It handles the project-relevant cases: combining marks, variation
    selectors, emoji modifiers, ZWJ chains and regional-indicator pairs.
    """
    clusters: list[str] = []
    regional_run = 0
    join_next = False
    for char in value:
        code = ord(char)
        combining = bool(unicodedata.combining(char)) or unicodedata.category(char) in {"Mc", "Me"}
        variation = 0xFE00 <= code <= 0xFE0F or 0xE0100 <= code <= 0xE01EF
        modifier = 0x1F3FB <= code <= 0x1F3FF
        zwj = code == 0x200D
        regional = 0x1F1E6 <= code <= 0x1F1FF
        if not clusters:
            clusters.append(char); regional_run = 1 if regional else 0; join_next = zwj
            continue
        if combining or variation or modifier or join_next or zwj:
            clusters[-1] += char
            join_next = zwj
            if not regional:
                regional_run = 0
            continue
        if regional and regional_run % 2 == 1:
            clusters[-1] += char
            regional_run += 1
            continue
        clusters.append(char)
        regional_run = regional_run + 1 if regional else 0
        join_next = False
    return clusters


def _grapheme_boundaries(value: str) -> list[int]:
    result = [0]
    offset = 0
    for cluster in grapheme_clusters(value):
        offset += len(cluster)
        result.append(offset)
    return result


def _tag_token_matches(raw: str) -> list[tuple[str, str, tuple[int, int]]]:
    values: list[tuple[str, str, tuple[int, int]]] = []
    for match in STYLE_TOKEN_RE.finditer(raw):
        if match.group("color") is not None:
            values.append((match.group("color"), match.group("color_value") or "", match.span()))
        elif match.group("toggle") is not None:
            values.append((match.group("toggle"), match.group("toggle_value") or "", match.span()))
        elif match.group("font") is not None:
            values.append((match.group("font"), match.group("font_value") or "", match.span()))
        elif match.group("size") is not None:
            values.append((match.group("size"), match.group("size_value") or "", match.span()))
    return values


def _offset_to_grapheme(boundaries: list[int], offset: int) -> int | None:
    try:
        return boundaries.index(offset)
    except ValueError:
        return None


@dataclass(frozen=True)
class VisualGlyphProgram:
    program_id: str
    envelope_id: int
    source_graphemes: tuple[str, ...]
    base_visual_state: dict[str, str]
    glyph_states: tuple[dict[str, str], ...]
    transition_runs: tuple[dict[str, Any], ...]
    eligible_properties: tuple[str, ...]
    excluded_properties: tuple[str, ...]
    projection_policy: str
    provenance: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["source_graphemes"] = list(self.source_graphemes)
        value["glyph_states"] = list(self.glyph_states)
        value["transition_runs"] = list(self.transition_runs)
        value["eligible_properties"] = list(self.eligible_properties)
        value["excluded_properties"] = list(self.excluded_properties)
        return value


def extract_visual_glyph_program(source_ass: str, *, program_id: str, envelope_id: int) -> tuple[VisualGlyphProgram | None, dict[str, Any]]:
    """Extract only static, dense per-grapheme primary-colour programs."""
    if r"\t(" in source_ass:
        return None, {"valid": False, "reason": "UNSUPPORTED_TEMPORAL_STATE_FAIL_CLOSED"}
    if re.search(r"\\p(?:[1-9]\d*)?(?![A-Za-z\d])", source_ass):
        return None, {"valid": False, "reason": "DRAWING_MODE_FAIL_CLOSED"}
    plain, tags = _tag_occurrences(source_ass)
    if r"\N" in plain:
        return None, {"valid": False, "reason": "MULTILINE_VISUAL_GLYPH_PROGRAM_UNSUPPORTED"}
    clusters = grapheme_clusters(plain)
    if not clusters:
        return None, {"valid": False, "reason": "TARGET_OR_SOURCE_EMPTY"}
    boundaries = _grapheme_boundaries(plain)
    state: dict[str, str] = {}
    state_by_index: list[dict[str, str]] = []
    all_tokens: list[tuple[int, str, str, str]] = []
    for tag in tags:
        index = _offset_to_grapheme(boundaries, tag.plain_offset)
        if index is None:
            return None, {"valid": False, "reason": "TAG_NOT_ON_GRAPHEME_BOUNDARY", "tag_index": tag.index, "plain_offset": tag.plain_offset}
        for name, value, _span in _tag_token_matches(tag.raw):
            all_tokens.append((index, name, value, tag.raw))
    token_by_index: dict[int, list[tuple[str, str]]] = {}
    for index, name, value, _raw in all_tokens:
        token_by_index.setdefault(index, []).append((name, value))
    for index in range(len(clusters)):
        for name, value in token_by_index.get(index, []):
            state[name] = value
        state_by_index.append(dict(state))
    primary_values = [item.get("c", item.get("1c")) for item in state_by_index]
    if any(value is None for value in primary_values):
        return None, {"valid": False, "reason": "PRIMARY_STATE_NOT_TOTAL"}
    transitions = sum(left != right for left, right in zip(primary_values, primary_values[1:]))
    lexical_clusters = [cluster for cluster in clusters if not cluster.isspace()]
    # Dense primary-colour variation over every grapheme, including whitespace
    # and punctuation, establishes a purely visual sequence rather than a
    # lexical category. A sparse/word-delimited style stays fail-closed for the
    # semantic styled-span layer.
    if len(clusters) < 4 or transitions < 3 or len(set(primary_values)) < 3:
        return None, {"valid": False, "reason": "SEMANTICALLY_MEANINGFUL_GLYPH_STYLE_UNPROVEN", "transitions": transitions}
    if not lexical_clusters:
        return None, {"valid": False, "reason": "NON_LINGUISTIC_TEXT_FAIL_CLOSED"}
    unsupported = sorted({name for _index, name, _value, _raw in all_tokens if name not in GLYPH_ALLOWED_PROPERTIES and name in {"b", "i", "u", "fn", "fs"}})
    if unsupported:
        return None, {"valid": False, "reason": "UNSUPPORTED_GLYPH_PROPERTY", "properties": unsupported}
    base = {name: value for name, value in state_by_index[0].items() if name not in PRIMARY_NAMES}
    glyph_states = tuple({"c": str(value)} for value in primary_values)
    runs: list[dict[str, Any]] = []
    start = 0
    for index in range(1, len(glyph_states) + 1):
        if index == len(glyph_states) or glyph_states[index] != glyph_states[start]:
            runs.append({"start": start, "end": index, "state": glyph_states[start]})
            start = index
    return VisualGlyphProgram(
        program_id=program_id, envelope_id=int(envelope_id), source_graphemes=tuple(clusters),
        base_visual_state=base, glyph_states=glyph_states, transition_runs=tuple(runs),
        eligible_properties=("c",), excluded_properties=tuple(sorted(set(unsupported))),
        projection_policy="NORMALIZED_NEAREST_SOURCE_GLYPH",
        provenance={"classification": "STATIC_PER_GLYPH_DISCRETE_STATE", "static": True, "semantic_ownership": False, "source_primary_transition_count": transitions},
    ), {"valid": True, "reason": "STATIC_PER_GLYPH_DISCRETE_STATE", "plain": plain, "tag_count": len(tags)}


def project_indices(source_count: int, target_count: int, policy: str) -> list[int] | None:
    if source_count < 1 or target_count < 1:
        return None
    if source_count == 1:
        return [0] * target_count
    if target_count == 1:
        return [0]
    if policy == "NORMALIZED_NEAREST_SOURCE_GLYPH" or policy == "STATE_SEQUENCE_RESAMPLING":
        return [int(math.floor((index * (source_count - 1) / (target_count - 1)) + 0.5)) for index in range(target_count)]
    if policy == "RUN_LENGTH_PROPORTIONAL_WITH_ENDPOINT_PRESERVATION":
        result = [min(source_count - 1, int(math.floor(index * source_count / target_count))) for index in range(target_count)]
        result[-1] = source_count - 1
        return result
    if policy == "NORMALIZED_RUN_BOUNDARY_PROJECTION":
        return [int(math.floor(index * (source_count - 1) / (target_count - 1))) for index in range(target_count - 1)] + [source_count - 1]
    return None


def project_visual_glyph_program(program: VisualGlyphProgram, target_text: str, *, policy: str | None = None) -> tuple[list[dict[str, str]] | None, dict[str, Any]]:
    target = grapheme_clusters(target_text)
    selected = policy or program.projection_policy
    indices = project_indices(len(program.source_graphemes), len(target), selected)
    if indices is None:
        return None, {"valid": False, "reason": "TARGET_OR_SOURCE_EMPTY"}
    states = [dict(program.glyph_states[index]) for index in indices]
    order = [state["c"] for state in states]
    if any(left > right for left, right in zip(indices, indices[1:])):
        return None, {"valid": False, "reason": "PROJECTION_NON_MONOTONIC"}
    if states[0] != program.glyph_states[0] or states[-1] != program.glyph_states[-1]:
        return None, {"valid": False, "reason": "PROJECTION_ENDPOINT_FAILURE"}
    invented = sorted({state["c"] for state in states} - {state["c"] for state in program.glyph_states})
    if invented:
        return None, {"valid": False, "reason": "NO_INVENTED_VISUAL_STATE", "invented": invented}
    return states, {"valid": True, "policy": selected, "target_graphemes": target, "target_to_source_index": indices,
        "first_state_preserved": True, "last_state_preserved": True, "projection_monotonic": True,
        "invented_state_count": 0, "lost_state_count": len({state["c"] for state in program.glyph_states} - {state["c"] for state in states})}


def _remove_primary_tokens(source: str) -> str:
    out: list[str] = []; cursor = 0
    for match in TAG_GROUP_RE.finditer(source):
        out.append(source[cursor:match.start()])
        raw = match.group(0); removals: list[tuple[int, int]] = []
        for name, _value, span in _tag_token_matches(raw):
            if name in PRIMARY_NAMES: removals.append(span)
        if removals:
            body = raw
            for start, end in reversed(removals): body = body[:start] + body[end:]
            if body != "{}": out.append(body)
        else: out.append(raw)
        cursor = match.end()
    out.append(source[cursor:])
    return "".join(out)


def _insert_primary_runs(provisional: str, target_text: str, states: list[dict[str, str]]) -> str | None:
    target_clusters = grapheme_clusters(target_text)
    if len(target_clusters) != len(states): return None
    offsets = _grapheme_boundaries(target_text)[:-1]
    insertions: dict[int, str] = {}
    prior: dict[str, str] | None = None
    for offset, state in zip(offsets, states):
        if state != prior:
            insertions[offset] = "{\\c" + state["c"] + "}"
            prior = state
    output: list[str] = []; plain_offset = 0; cursor = 0
    for match in TAG_GROUP_RE.finditer(provisional):
        literal = provisional[cursor:match.start()]
        for char in literal:
            if plain_offset in insertions: output.append(insertions[plain_offset])
            output.append(char); plain_offset += 1
        output.append(match.group(0)); cursor = match.end()
    for char in provisional[cursor:]:
        if plain_offset in insertions: output.append(insertions[plain_offset])
        output.append(char); plain_offset += 1
    if plain_offset in insertions: output.append(insertions[plain_offset])
    final = "".join(output)
    return final if strip_ass_tags(final) == target_text else None


def reconstruct_visual_glyph_envelope(source_ass: str, target_text: str, *, program: VisualGlyphProgram, base_rebuilder: Callable[[str, str], str | None]) -> tuple[str | None, dict[str, Any]]:
    states, projection = project_visual_glyph_program(program, target_text)
    if states is None: return None, projection
    provisional = base_rebuilder(_remove_primary_tokens(source_ass), target_text)
    if provisional is None or strip_ass_tags(provisional) != target_text:
        return None, {"valid": False, "reason": "BASE_ENVELOPE_REINJECTION_FAILED"}
    final = _insert_primary_runs(provisional, target_text, states)
    if final is None: return None, {"valid": False, "reason": "TARGET_TEXT_IDENTITY"}
    final_plain, final_tags = _tag_occurrences(final)
    if final_plain != target_text:
        return None, {"valid": False, "reason": "TARGET_TEXT_IDENTITY"}
    return final, {"valid": True, "projection": projection, "target_grapheme_count": len(grapheme_clusters(target_text)),
        "source_grapheme_count": len(program.source_graphemes), "target_transition_count": max(0, len({}) + sum(left != right for left, right in zip(states, states[1:])),),
        "all_target_graphemes_accounted": True, "non_glyph_tag_count": len(final_tags)}
