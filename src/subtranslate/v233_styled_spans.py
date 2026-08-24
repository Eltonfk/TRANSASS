"""Strict semantic styled-span alignment for the V2.3.3 candidate.

This module keeps translation and ASS presentation separate.  It extracts
meaningful inline style intervals from a source event, validates a mapping to
an unchanged semantic target string, and moves only simple source-owned style
tag groups to the approved target boundaries.  It never exposes ASS syntax to
the alignment model and it fails closed for compound/nested style state.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any, Callable, Iterable


TAG_GROUP_RE = re.compile(r"\{[^{}]*\}")
STYLE_TOKEN_RE = re.compile(
    r"\\(?:"
    # Border width (longest first: bord before b)
    r"(?P<border>bord|xbord|ybord)(?P<border_value>-?\d*\.?\d*)"
    # Shadow depth
    r"|(?P<shadow>shad|xshad|yshad)(?P<shadow_value>-?\d*\.?\d*)"
    # Blur / edge
    r"|(?P<blur>blur|be)(?P<blur_value>\d+)"
    # Rotation (frx, fry, frz, fr)
    r"|(?P<rotation>fr[xyz]?)(?P<rotation_value>-?\d*\.?\d*)"
    # Scaling (fscx, fscy)
    r"|(?P<scaling>fsc[xy])(?P<scaling_value>-?\d*\.?\d*)"
    # Shearing (fax, fay)
    r"|(?P<shear>fa[xy])(?P<shear_value>-?\d*\.?\d*)"
    # Alpha / transparency
    r"|(?P<alpha>alpha|[1234]a)&H(?P<alpha_value>[0-9A-Fa-f]{1,2})"
    # Colors
    r"|(?P<color>1c|2c|3c|4c|c)(?P<color_value>&H[0-9A-Fa-f]+&)?(?![A-Za-z])"
    # Font name and size
    r"|(?P<font>fn)(?P<font_value>[^\\}]*)"
    r"|(?P<size>fs)(?P<size_value>\d*)(?![A-Za-z])"
    # Toggles: bold, italic, underline, strikethrough
    r"|(?P<toggle>b|i|u|s)(?P<toggle_value>[01]?)(?![0-9A-Za-z])"
    ")"
)


@dataclass(frozen=True)
class SemanticStyledSpan:
    span_id: str
    semantic_unit_id: str
    envelope_event_id: int
    source_text: str
    source_plain_start: int
    source_plain_end: int
    style_delta_fingerprint: str
    style_open_tokens: tuple[str, ...]
    style_reset_tokens: tuple[str, ...]
    source_left_context: str
    source_right_context: str
    required_mapping: bool
    property_name: str
    open_tag_index: int
    reset_tag_index: int
    open_tag_is_simple: bool
    reset_tag_is_simple: bool

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["style_open_tokens"] = list(self.style_open_tokens)
        value["style_reset_tokens"] = list(self.style_reset_tokens)
        return value


@dataclass(frozen=True)
class TagOccurrence:
    index: int
    raw: str
    plain_offset: int
    style_tokens: tuple[tuple[str, str], ...]
    simple_style_property: str | None


def strip_ass_tags(value: str) -> str:
    """Remove only ASS overrides; semantic text is never normalized here."""
    return TAG_GROUP_RE.sub("", value or "")


def _is_word_char(value: str) -> bool:
    return bool(value) and (value.isalnum() or value == "_")


def _style_is_reset(name: str, value: str) -> bool:
    value = value.strip()
    if name in {"c", "1c", "2c", "3c", "4c", "fn"}:
        return not value
    if name in {"b", "i", "u", "s", "fs"}:
        return not value or value == "0"
    if name in {"bord", "xbord", "ybord", "shad", "xshad", "yshad",
                "be", "blur", "frx", "fry", "frz", "fr",
                "fscx", "fscy", "fax", "fay"}:
        return value in ("0", "0.0")
    if name in {"alpha", "1a", "2a", "3a", "4a"}:
        return value.upper() == "&H00" or value == "0"
    return False


def _style_tokens(raw: str) -> tuple[tuple[str, str], ...]:
    values: list[tuple[str, str]] = []
    for item in STYLE_TOKEN_RE.finditer(raw):
        if item.group("color") is not None:
            values.append((item.group("color"), item.group("color_value") or ""))
        elif item.group("toggle") is not None:
            values.append((item.group("toggle"), item.group("toggle_value") or ""))
        elif item.group("font") is not None:
            values.append((item.group("font"), item.group("font_value") or ""))
        elif item.group("size") is not None:
            values.append((item.group("size"), item.group("size_value") or ""))
    return tuple(values)


def _tag_occurrences(source: str) -> tuple[str, list[TagOccurrence]]:
    plain_parts: list[str] = []
    tags: list[TagOccurrence] = []
    raw_index = 0
    plain_offset = 0
    for match in TAG_GROUP_RE.finditer(source or ""):
        before = (source or "")[raw_index:match.start()]
        plain_parts.append(before)
        plain_offset += len(before)
        raw = match.group(0)
        tokens = _style_tokens(raw)
        body = raw[1:-1]
        single = STYLE_TOKEN_RE.fullmatch(body)
        tags.append(TagOccurrence(
            index=len(tags), raw=raw, plain_offset=plain_offset, style_tokens=tokens,
            simple_style_property=(
                _style_tokens(single.group(0))[0][0]
                if single and len(_style_tokens(single.group(0))) == 1 else None
            ),
        ))
        raw_index = match.end()
    tail = (source or "")[raw_index:]
    plain_parts.append(tail)
    return "".join(plain_parts), tags


def extract_semantic_styled_spans(
    source_ass: str,
    *,
    semantic_unit_id: str,
    envelope_event_id: int,
) -> tuple[list[SemanticStyledSpan], list[str]]:
    """Extract closed, lexical inline style intervals from a source envelope.

    A style becomes semantically movable only when it is set and then reset
    around visible lexical material.  Uniform event-level styling remains in
    its original envelope and therefore requires no semantic mapping.
    """
    plain, tags = _tag_occurrences(source_ass)
    active: dict[str, tuple[TagOccurrence, str]] = {}
    spans: list[SemanticStyledSpan] = []
    issues: list[str] = []
    for tag in tags:
        for name, value in tag.style_tokens:
            if _style_is_reset(name, value):
                opened = active.pop(name, None)
                if not opened:
                    continue
                open_tag, open_value = opened
                start, end = open_tag.plain_offset, tag.plain_offset
                text = plain[start:end]
                if not text.strip() or not re.search(r"[\wÀ-ÿ]", text, re.UNICODE):
                    continue
                ordinal = len(spans) + 1
                fingerprint = hashlib.sha256(json.dumps({
                    "property": name, "open": open_value, "reset": value,
                    "source": text,
                }, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:20]
                spans.append(SemanticStyledSpan(
                    span_id=f"{semantic_unit_id}:event{envelope_event_id}:span{ordinal}",
                    semantic_unit_id=semantic_unit_id,
                    envelope_event_id=int(envelope_event_id),
                    source_text=text,
                    source_plain_start=start,
                    source_plain_end=end,
                    style_delta_fingerprint=fingerprint,
                    style_open_tokens=(open_tag.raw,),
                    style_reset_tokens=(tag.raw,),
                    source_left_context=plain[max(0, start - 40):start],
                    source_right_context=plain[end:end + 40],
                    required_mapping=True,
                    property_name=name,
                    open_tag_index=open_tag.index,
                    reset_tag_index=tag.index,
                    open_tag_is_simple=open_tag.simple_style_property == name,
                    reset_tag_is_simple=tag.simple_style_property == name,
                ))
            elif name in active:
                # A changed value without a reset is a compound state.  It
                # cannot be moved safely by a one-span mapping.
                issues.append("NESTED_OR_COMPOUND_STYLE_STATE")
            else:
                active[name] = (tag, value)
    for left in spans:
        for right in spans:
            if left.span_id >= right.span_id:
                continue
            overlap = left.source_plain_start < right.source_plain_end and right.source_plain_start < left.source_plain_end
            same = (left.source_plain_start, left.source_plain_end) == (right.source_plain_start, right.source_plain_end)
            if overlap and not same:
                issues.append("STYLED_SPAN_ALIGNMENT_AMBIGUOUS")
    return spans, sorted(set(issues))


def _target_occurrences(target: str, fragment: str) -> list[int]:
    if not fragment:
        return []
    starts: list[int] = []
    offset = 0
    while True:
        found = target.find(fragment, offset)
        if found < 0:
            break
        end = found + len(fragment)
        if (found == 0 or not (_is_word_char(target[found - 1]) and _is_word_char(target[found]))) and (end == len(target) or not (_is_word_char(target[end - 1]) and _is_word_char(target[end]))):
            starts.append(found)
        offset = found + 1
    return starts


def deterministic_span_mapping(spans: Iterable[SemanticStyledSpan], target_text: str) -> list[dict[str, Any]] | None:
    """Return a mapping only when every source span survives literally once."""
    mappings: list[dict[str, Any]] = []
    for span in spans:
        matches = _target_occurrences(target_text, span.source_text)
        if len(matches) != 1:
            return None
        mappings.append({"span_id": span.span_id, "target_text": span.source_text, "occurrence": 1})
    return mappings


def validate_span_mapping(
    spans: Iterable[SemanticStyledSpan], target_text: str, value: Any,
) -> tuple[dict[str, tuple[int, int]], dict[str, Any]]:
    """Validate a strict alignment response without changing target text."""
    expected = {span.span_id for span in spans}
    trace: dict[str, Any] = {"expected_span_ids": sorted(expected), "target_text": target_text}
    if not isinstance(value, dict) or set(value) != {"span_mapping"} or not isinstance(value.get("span_mapping"), list):
        return {}, {**trace, "valid": False, "reason": "STRICT_SCHEMA_KEYS"}
    rows = value["span_mapping"]
    seen: set[str] = set()
    resolved: dict[str, tuple[int, int]] = {}
    row_trace: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"span_id", "target_text", "occurrence"}:
            return {}, {**trace, "valid": False, "reason": "STRICT_SCHEMA_KEYS"}
        span_id, fragment, occurrence = row["span_id"], row["target_text"], row["occurrence"]
        if not isinstance(span_id, str) or not isinstance(fragment, str) or isinstance(occurrence, bool) or not isinstance(occurrence, int):
            return {}, {**trace, "valid": False, "reason": "STRICT_SCHEMA_TYPES"}
        if span_id not in expected or span_id in seen:
            return {}, {**trace, "valid": False, "reason": "UNKNOWN_OR_DUPLICATE_SPAN"}
        matches = _target_occurrences(target_text, fragment)
        if occurrence < 1 or occurrence > len(matches):
            return {}, {**trace, "valid": False, "reason": "TARGET_SUBSTRING_NOT_UNIQUE_OR_ABSENT", "span_id": span_id}
        start = matches[occurrence - 1]
        resolved[span_id] = (start, start + len(fragment))
        seen.add(span_id)
        row_trace.append({"span_id": span_id, "target_text": fragment, "occurrence": occurrence, "target_offsets": [start, start + len(fragment)]})
    if seen != expected:
        return {}, {**trace, "valid": False, "reason": "MISSING_SPAN_MAPPING", "actual_span_ids": sorted(seen)}
    ranges = list(resolved.items())
    for index, (left_id, (left_start, left_end)) in enumerate(ranges):
        for right_id, (right_start, right_end) in ranges[index + 1:]:
            overlap = left_start < right_end and right_start < left_end
            same = (left_start, left_end) == (right_start, right_end)
            if overlap and not same:
                return {}, {**trace, "valid": False, "reason": "STYLED_SPAN_ALIGNMENT_AMBIGUOUS", "span_ids": [left_id, right_id]}
    return resolved, {**trace, "valid": True, "rows": row_trace}


def alignment_schema(span_count: int) -> dict[str, Any]:
    item = {
        "type": "object",
        "properties": {
            "span_id": {"type": "string"},
            "target_text": {"type": "string"},
            "occurrence": {"type": "integer", "minimum": 1},
        },
        "required": ["span_id", "target_text", "occurrence"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {"span_mapping": {"type": "array", "items": item, "minItems": span_count, "maxItems": span_count}},
        "required": ["span_mapping"],
        "additionalProperties": False,
    }


def build_alignment_request(
    spans: Iterable[SemanticStyledSpan], *, source_text: str, target_text: str,
    model: str, num_ctx: int = 4096, num_predict: int = 384,
) -> dict[str, Any]:
    """Build a model request with linguistic data only, never raw ASS."""
    span_rows = [
        {"span_id": span.span_id, "source_text": span.source_text,
         "left_context": span.source_left_context, "right_context": span.source_right_context}
        for span in spans
    ]
    schema = alignment_schema(len(span_rows))
    output_shape = {
        "span_mapping": [
            {"span_id": "<required span_id>", "target_text": "<literal substring from TARGET_TEXT>", "occurrence": 1}
        ]
    }
    prompt = (
        "Mapeie spans semânticos da SOURCE para substrings contíguas exatas da TARGET. "
        "SOURCE é inglês e TARGET é português brasileiro já aprovado. Não traduza, não reescreva TARGET, "
        "não retorne tags ASS e não explique. Para cada span, devolva exatamente o texto que já existe em TARGET "
        "e a ocorrência (1 = primeira ocorrência válida).\n\n"
        "CONTRATO_JSON_OBRIGATÓRIO: responda exatamente UM objeto JSON, nunca uma lista JSON. "
        "A raiz deve possuir exatamente a chave `span_mapping`; `[]`, `{}`, explicações, markdown e chaves extras são inválidos. "
        "span_mapping deve conter exatamente um item para cada span_id listado, e cada item deve possuir somente "
        "span_id, target_text e occurrence.\n"
        "ESCOPO_DE_OCCURRENCE_OBRIGATÓRIO: occurrence é local a CADA row de span_mapping. Para uma row, "
        "occurrence é o índice 1-based da ocorrência de target_text dentro de TARGET_TEXT. Cada row reinicia "
        "essa contagem independentemente: rows não consomem ocorrências umas das outras. occurrence NÃO é o "
        "número da row e NÃO é contador global. Vários span_ids independentes podem mapear para o mesmo "
        "target_text, mesmos offsets e occurrence 1.\n"
        "EXEMPLO_SINTÉTICO_DE_ESCOPO: TARGET_TEXT = \"o motor azul está ligado\". Quatro rows independentes "
        "(event1/span1, event2/span1, event3/span1, event4/span1) que mapeiam para \"motor azul\" devem "
        "todas retornar occurrence 1. Retornar occurrence 1, 2, 3, 4 para essas rows é inválido.\n"
        f"FORMATO_DE_SAÍDA: {json.dumps(output_shape, ensure_ascii=False)}\n"
        f"REQUIRED_SPAN_IDS: {json.dumps([row['span_id'] for row in span_rows], ensure_ascii=False)}\n"
        f"SCHEMA: {json.dumps(schema, ensure_ascii=False, sort_keys=True)}\n\n"
        f"SOURCE_TEXT: {json.dumps(source_text, ensure_ascii=False)}\n"
        f"TARGET_TEXT: {json.dumps(target_text, ensure_ascii=False)}\n"
        f"SOURCE_STYLED_SPANS: {json.dumps(span_rows, ensure_ascii=False)}"
    )
    return {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "format": schema,
        "think": False,
        "options": {"temperature": 0.0, "num_ctx": num_ctx, "num_predict": num_predict},
    }


def decode_alignment_response(content: str) -> tuple[Any, Any | None]:
    """Decode without collapsing the root response into its mapping member.

    The strict validator remains the authority.  Keeping both values avoids a
    diagnostic bug where a top-level list/object can be incorrectly reported
    as an empty mapping array.
    """
    if not isinstance(content, str) or not content.strip():
        raise ValueError("ALIGNMENT_RESPONSE_EMPTY")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"ALIGNMENT_DUPLICATE_JSON_KEY:{key}")
            result[key] = value
        return result

    decoded_root_json = json.loads(
        content.strip(), object_pairs_hook=pairs,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
    )
    extracted_span_mapping = decoded_root_json.get("span_mapping") if isinstance(decoded_root_json, dict) else None
    return decoded_root_json, extracted_span_mapping


def _remove_and_schedule_style_tags(
    source: str,
    source_tags: list[TagOccurrence],
    spans: list[SemanticStyledSpan],
    resolved: dict[str, tuple[int, int]],
) -> tuple[str | None, dict[int, list[tuple[int, str]]], dict[str, Any]]:
    """Remove only tag groups that must change semantic boundary.

    The base envelope rebuilder is then given a source with those groups
    absent.  This is essential: reflowing around a source boundary that is
    known to move can otherwise erase target whitespace before the semantic
    alignment has a chance to place the tag correctly.
    """
    removals: set[int] = set()
    insertions: dict[int, list[tuple[int, str]]] = {}
    for span in spans:
        target_start, target_end = resolved[span.span_id]
        for tag_index, source_position, target_position, simple in (
            (span.open_tag_index, span.source_plain_start, target_start, span.open_tag_is_simple),
            (span.reset_tag_index, span.source_plain_end, target_end, span.reset_tag_is_simple),
        ):
            if not simple and source_position != target_position:
                return None, {}, {"valid": False, "reason": "NESTED_OR_COMPOUND_STYLE_STATE", "span_id": span.span_id, "tag_index": tag_index}
            # A simple style group is detached even when its lexical offset
            # happens to coincide.  This makes the semantic boundary explicit
            # and prevents a base reflow from splitting/merging text around a
            # tag before reinsertion.  Compound groups may remain only when
            # their source and target boundaries are already identical.
            if simple:
                removals.add(tag_index)
                insertions.setdefault(target_position, []).append((tag_index, source_tags[tag_index].raw))
    for values in insertions.values():
        values.sort(key=lambda value: value[0])

    if not removals:
        return source, insertions, {"valid": True, "moved_tag_count": 0, "moved_tag_indices": []}
    pieces: list[str] = []
    raw_index = 0
    for tag in source_tags:
        match = next(TAG_GROUP_RE.finditer(source, raw_index), None)
        if match is None:
            return None, {}, {"valid": False, "reason": "ASS_TAG_SEQUENCE_CHANGED_BEFORE_ALIGNMENT"}
        pieces.append(source[raw_index:match.start()])
        if tag.index not in removals:
            pieces.append(match.group(0))
        raw_index = match.end()
    pieces.append(source[raw_index:])
    return "".join(pieces), insertions, {"valid": True, "moved_tag_count": len(removals), "moved_tag_indices": sorted(removals)}


def _insert_style_tags(
    provisional: str,
    insertions: dict[int, list[tuple[int, str]]],
) -> tuple[str | None, dict[str, Any]]:
    """Insert source-owned tag groups at validated target offsets."""
    provisional_plain, _ = _tag_occurrences(provisional)
    output: list[str] = []
    cursor = 0
    remaining = {position: list(values) for position, values in insertions.items()}

    def emit_inserts() -> None:
        for _, raw in remaining.pop(cursor, []):
            output.append(raw)

    raw_index = 0
    for match in TAG_GROUP_RE.finditer(provisional):
        literal = provisional[raw_index:match.start()]
        for char in literal:
            emit_inserts()
            output.append(char)
            cursor += 1
        output.append(match.group(0))
        raw_index = match.end()
    tail = provisional[raw_index:]
    for char in tail:
        emit_inserts()
        output.append(char)
        cursor += 1
    emit_inserts()
    if remaining:
        return None, {"valid": False, "reason": "TARGET_OFFSET_OUT_OF_RANGE"}
    final = "".join(output)
    if strip_ass_tags(final) != provisional_plain:
        return None, {"valid": False, "reason": "TARGET_TEXT_INVARIANT_FAILED"}
    return final, {"valid": True}


def reconstruct_styled_envelope(
    source_ass: str,
    target_text: str,
    *,
    semantic_unit_id: str,
    envelope_event_id: int,
    mappings: Any | None,
    base_rebuilder: Callable[[str, str], str | None],
) -> tuple[str | None, dict[str, Any]]:
    """Rebuild an envelope while proving semantic text and tag invariants."""
    spans, extraction_issues = extract_semantic_styled_spans(
        source_ass, semantic_unit_id=semantic_unit_id, envelope_event_id=envelope_event_id,
    )
    source_plain, source_tags = _tag_occurrences(source_ass)
    if extraction_issues:
        return None, {"valid": False, "reason": extraction_issues[0], "issues": extraction_issues, "spans": [span.to_dict() for span in spans]}
    if not spans:
        provisional = base_rebuilder(source_ass, target_text)
        if provisional is None:
            return None, {"valid": False, "reason": "BASE_ENVELOPE_REINJECTION_FAILED"}
        if strip_ass_tags(provisional) != target_text:
            return None, {"valid": False, "reason": "TARGET_TEXT_INVARIANT_FAILED", "provisional_text": strip_ass_tags(provisional), "target_text": target_text}
        return provisional, {"valid": True, "alignment_required": False, "spans": []}
    if mappings is None:
        mappings = deterministic_span_mapping(spans, target_text)
        if mappings is None:
            return None, {"valid": False, "reason": "SEMANTIC_STYLED_SPAN_ALIGNMENT_REQUIRED", "spans": [span.to_dict() for span in spans]}
    resolved, trace = validate_span_mapping(spans, target_text, mappings)
    if not trace.get("valid"):
        return None, {"valid": False, "reason": trace["reason"], "alignment": trace, "spans": [span.to_dict() for span in spans]}
    source_without_moved, insertions, move_trace = _remove_and_schedule_style_tags(
        source_ass, source_tags, spans, resolved,
    )
    if source_without_moved is None:
        return None, {"valid": False, "reason": move_trace["reason"], "alignment": trace, "move": move_trace, "spans": [span.to_dict() for span in spans]}
    provisional = base_rebuilder(source_without_moved, target_text)
    if provisional is None:
        return None, {"valid": False, "reason": "BASE_ENVELOPE_REINJECTION_FAILED"}
    if strip_ass_tags(provisional) != target_text:
        return None, {"valid": False, "reason": "TARGET_TEXT_INVARIANT_FAILED", "provisional_text": strip_ass_tags(provisional), "target_text": target_text}
    final, insert_trace = _insert_style_tags(provisional, insertions)
    if final is None:
        return None, {"valid": False, "reason": insert_trace["reason"], "alignment": trace, "move": move_trace, "spans": [span.to_dict() for span in spans]}
    if strip_ass_tags(final) != target_text:
        return None, {"valid": False, "reason": "TARGET_TEXT_INVARIANT_FAILED", "alignment": trace, "move": move_trace}
    if Counter(tag.raw for tag in _tag_occurrences(final)[1]) != Counter(tag.raw for tag in source_tags):
        return None, {"valid": False, "reason": "ASS_TAG_MULTIPLICITY_CHANGED", "alignment": trace, "move": move_trace}
    return final, {
        "valid": True,
        "alignment_required": True,
        "spans": [span.to_dict() for span in spans],
        "alignment": trace,
        "move": move_trace,
        "source_plain": source_plain,
        "target_text": target_text,
    }
