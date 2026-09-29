"""Small, dependency-free primitives for safe ASS text handling.

ASS event text is a mixture of linguistic payload and presentation controls.
This module centralizes the lexical boundary used by the translation
pipelines, so a new control token does not have to be implemented differently
in every pipeline generation.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any


TAG_RE = re.compile(r"\{[^}]*\}")
CONTROL_RE = re.compile(r"\\(?:N|n|h)")
BREAK_RE = re.compile(r"\\(?:N|n)")
TOKEN_RE = re.compile(r"(\{[^}]*\}|\\(?:N|n|h))")
PAYLOAD_TOKEN_RE = re.compile(r"(\{[^}]*\}|\\h)")
PAYLOAD_WORD_RE = re.compile(r"[\wÀ-ÿ]+", re.UNICODE)
DRAWING_MODE_RE = re.compile(r"\\p(?P<scale>[0-9]+)", re.IGNORECASE)
VECTOR_COMMAND_RE = re.compile(r"(?<![A-Za-z])(?:m|n|l|b|s|p|c)(?![A-Za-z])", re.IGNORECASE)


def visible_text(text: str, *, line_break: str = "\n") -> str:
    """Return visible ASS text while removing tags and normalizing controls.

    ``\\N`` is a hard line break, ``\\n`` is a soft line break and ``\\h``
    is a non-breaking space.  For linguistic analysis both break types are
    visible separators; callers may select the separator they need.
    """

    value = TAG_RE.sub("", text or "")
    value = value.replace(r"\N", line_break).replace(r"\n", line_break)
    return value.replace(r"\h", " ")


def control_tokens(text: str) -> list[str]:
    """Return ASS text controls in source order."""

    return [match.group(0) for match in CONTROL_RE.finditer(text or "")]


def break_tokens(text: str) -> list[str]:
    """Return hard/soft line-break controls in source order."""

    return [match.group(0) for match in BREAK_RE.finditer(text or "")]


def break_count(text: str) -> int:
    return len(break_tokens(text))


def hard_space_count(text: str) -> int:
    return (text or "").count(r"\h")


def split_text_with_controls(
    text: str,
) -> tuple[list[str], list[dict[str, Any]], list[int], list[str], list[int]]:
    """Split event text while retaining tag and control metadata.

    The first three return values intentionally match the historical
    ``split_ass_text`` contract.  ``\\h`` becomes an ordinary space in the
    linguistic pieces, while its visible offsets are returned separately so
    the renderer can restore the non-breaking spaces later.
    """

    pieces: list[str] = []
    anchors: list[dict[str, Any]] = []
    breaks: list[int] = []
    break_values: list[str] = []
    hard_spaces: list[int] = []
    current: list[str] = []
    visible_offset = 0
    for token in TOKEN_RE.split(text or ""):
        if not token:
            continue
        if token in (r"\N", r"\n"):
            pieces.append("".join(current))
            current = []
            breaks.append(len(pieces) - 1)
            break_values.append(token)
            continue
        if token == r"\h":
            hard_spaces.append(visible_offset)
            current.append(" ")
            visible_offset += 1
            continue
        if token.startswith("{") and token.endswith("}"):
            anchors.append({"position": visible_offset, "tag": token})
            continue
        current.append(token)
        visible_offset += len(token)
    pieces.append("".join(current))
    return pieces, anchors, breaks, break_values, hard_spaces


def inline_tag_counts(text: str) -> Counter[str]:
    return Counter(TAG_RE.findall(text or ""))


def _is_word_character(value: str) -> bool:
    return bool(value) and (value == "_" or value.isalnum())


def _nearest_payload_boundary(text: str, desired: int) -> int:
    """Snap a source boundary without splitting a target word."""
    desired = max(0, min(len(text), desired))
    if desired in (0, len(text)):
        return desired
    if not (_is_word_character(text[desired - 1]) and _is_word_character(text[desired])):
        return desired
    candidates = [
        index for index in range(len(text) + 1)
        if index in (0, len(text))
        or not (_is_word_character(text[index - 1]) and _is_word_character(text[index]))
    ]
    return min(candidates, key=lambda index: (abs(index - desired), 0 if index >= desired else 1))


def _payload_sequence(source: str) -> list[tuple[str, str]]:
    """Split one ASS line into source-owned styled slots and separators.

    Tags do not create a new linguistic word by themselves.  This distinction
    matters for per-character typesetting such as ``R{\\fs75}OAD``: it is one
    source word, not two chunks to which two translated words may be assigned.
    """
    sequence: list[tuple[str, str]] = []
    current: list[str] = []

    def flush_slot() -> None:
        if current:
            sequence.append(("slot", "".join(current)))
            current.clear()

    def add_separator(value: str) -> None:
        if sequence and sequence[-1][0] == "separator":
            kind, previous = sequence[-1]
            sequence[-1] = (kind, previous + value)
        else:
            sequence.append(("separator", value))

    for token in TOKEN_RE.split(source or ""):
        if not token:
            continue
        if token in (r"\N", r"\n", r"\h"):
            flush_slot()
            add_separator(token)
            continue
        if token.startswith("{") and token.endswith("}"):
            current.append(token)
            continue
        for char in token:
            if char.isspace():
                flush_slot()
                add_separator(char)
            else:
                current.append(char)
    flush_slot()
    return sequence


def _has_payload_word(value: str) -> bool:
    return bool(PAYLOAD_WORD_RE.search(PAYLOAD_TOKEN_RE.sub("", value or "")))


def _is_linguistic_punctuation_slot(value: str) -> bool:
    """Identify visible punctuation that should follow the translation.

    A source such as ``life ?`` has a punctuation-only slot after the last
    word.  Keeping that slot byte-for-byte would duplicate the question mark
    when the model correctly returns ``vida?``.  It is safe to coalesce only
    ordinary whitespace here; ASS controls remain structural and are never
    folded into this linguistic cleanup.
    """
    visible = PAYLOAD_TOKEN_RE.sub("", value or "")
    return bool(visible.strip()) and not _has_payload_word(value)


def _coalesce_linguistic_punctuation(
    sequence: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """Attach punctuation-only slots to the nearest lexical slot.

    This keeps source-owned tags while allowing translated punctuation to
    replace source punctuation.  It deliberately does not cross ``\\N``,
    ``\\n`` or ``\\h`` because those controls belong to the ASS envelope.
    """
    result = list(sequence)
    changed = True
    while changed:
        changed = False
        for index, (kind, value) in enumerate(result):
            if kind != "slot" or not _is_linguistic_punctuation_slot(value):
                continue
            if (
                index >= 2
                and result[index - 1][0] == "separator"
                and result[index - 1][1]
                and all(char.isspace() for char in result[index - 1][1])
                and result[index - 2][0] == "slot"
                and _has_payload_word(result[index - 2][1])
            ):
                previous = result[index - 2][1]
                separator = result[index - 1][1]
                result[index - 2] = ("slot", previous + separator + value)
                del result[index - 1:index + 1]
                changed = True
                break
            if (
                index + 2 < len(result)
                and result[index + 1][0] == "separator"
                and result[index + 1][1]
                and all(char.isspace() for char in result[index + 1][1])
                and result[index + 2][0] == "slot"
                and _has_payload_word(result[index + 2][1])
            ):
                following = result[index + 2][1]
                separator = result[index + 1][1]
                result[index + 2] = ("slot", value + separator + following)
                del result[index:index + 2]
                changed = True
                break
    return result


def _replace_payload_slot(source_slot: str, target_text: str) -> str:
    """Replace one no-whitespace slot while retaining every source tag."""
    source_visible = PAYLOAD_TOKEN_RE.sub("", source_slot or "")
    if not source_visible:
        return source_slot or ""
    if not target_text:
        return "".join(TAG_RE.findall(source_slot or ""))

    source_length = max(1, len(source_visible))
    target_length = len(target_text)
    anchored: dict[int, list[str]] = {}
    for match in TAG_RE.finditer(source_slot or ""):
        source_offset = len(PAYLOAD_TOKEN_RE.sub("", source_slot[:match.start()]))
        desired = round(source_offset * target_length / source_length)
        if 0 < source_offset < len(source_visible):
            source_internal = (
                _is_word_character(source_visible[source_offset - 1])
                and _is_word_character(source_visible[source_offset])
            )
        else:
            source_internal = False
        # A tag between source word characters (per-letter styling) is allowed
        # to follow the proportional target offset.  A tag at a source lexical
        # boundary must land on a target lexical boundary instead.
        target_offset = desired if source_internal else _nearest_payload_boundary(target_text, desired)
        anchored.setdefault(target_offset, []).append(match.group(0))

    rendered = target_text
    for target_offset, tags in sorted(anchored.items(), reverse=True):
        rendered = rendered[:target_offset] + "".join(tags) + rendered[target_offset:]
    return rendered


def _terminal_numeric_anchor(source_piece: str, target_words: list[str]) -> int | None:
    """Return the target cut after a unique number ending a source line.

    Numbers are useful cross-language anchors: when an ASS line ends in a date
    or quantity and the model omits the line break, keep that number on the
    same translated line instead of splitting it from its context.
    """
    source_payload = PAYLOAD_TOKEN_RE.sub("", source_piece or "")
    source_match = re.search(r"(?<!\w)(\d+)[^\w]*$", source_payload)
    if not source_match:
        return None
    source_number = source_match.group(1)
    target_matches = [
        index
        for index, word in enumerate(target_words)
        if source_number in re.findall(r"(?<!\w)\d+(?!\w)", word)
    ]
    return target_matches[0] + 1 if len(target_matches) == 1 else None


def _collapse_generated_horizontal_spacing(source: str, rendered: str) -> str:
    """Remove extra spaces introduced when fewer target words fill source slots.

    Preserve deliberate repeated source whitespace and never normalize inside
    ASS override tags. Whitespace around tags and ASS controls remains intact.
    """
    if re.search(r"[ \t]{2,}", TAG_RE.sub("", source or "")):
        return rendered

    result: list[str] = []
    cursor = 0
    for match in TAG_RE.finditer(rendered or ""):
        result.append(re.sub(r"[ \t]{2,}", " ", rendered[cursor:match.start()]))
        result.append(match.group(0))
        cursor = match.end()
    result.append(re.sub(r"[ \t]{2,}", " ", (rendered or "")[cursor:]))
    normalized = "".join(result)
    source_visible = TAG_RE.sub("", source or "")

    if not source_visible.startswith((" ", "\t")):
        prefix_match = re.match(r"(?:\{[^}]*\})*", normalized)
        prefix = prefix_match.group(0) if prefix_match else ""
        normalized = prefix + normalized[len(prefix):].lstrip(" \t")
    if not source_visible.endswith((" ", "\t")):
        suffix_match = re.search(r"(?:\{[^}]*\})*$", normalized)
        suffix = suffix_match.group(0) if suffix_match else ""
        body = normalized[:len(normalized) - len(suffix)] if suffix else normalized
        normalized = body.rstrip(" \t") + suffix
    return normalized


def replace_source_payload(source: str, target: str) -> str:
    """Replace linguistic text while rebuilding the source-owned ASS envelope.

    A translated payload cannot be spliced into an ASS line by simply joining
    words around the original tags.  In a line such as ``How {\\c&Hfff&}Beetles``
    the space before the tag belongs to the first lexical slot.  Dropping it
    produces ``Como os{\\c&Hfff&}Besouros``.  The allocator below treats all
    text between whitespace controls as one source word slot, even when that
    slot contains many per-character tags, and carries source separators with
    the slot boundary.

    Override blocks, ``\\h`` and source line-break controls are opaque and stay
    under source authority.  Any tags accidentally returned by a model are
    discarded before the target is allocated.  The function is deterministic,
    dependency-free and intentionally does not validate whether an individual
    override command is understood by a renderer.
    """
    target_without_tags = PAYLOAD_TOKEN_RE.sub("", target or "")
    source_breaks = BREAK_RE.findall(source or "")
    source_parts = BREAK_RE.split(source or "")
    if len(source_parts) > 1:
        target_parts = BREAK_RE.split(target_without_tags)
        if len(target_parts) == len(source_parts):
            rendered = [
                replace_source_payload(source_piece, target_piece)
                for source_piece, target_piece in zip(source_parts, target_parts)
            ]
            return "".join(
                piece + (source_breaks[index] if index < len(source_breaks) else "")
                for index, piece in enumerate(rendered)
            )

        target_words = re.findall(r"\S+", BREAK_RE.sub(" ", target_without_tags))
        source_word_counts = [
            len(PAYLOAD_WORD_RE.findall(PAYLOAD_TOKEN_RE.sub("", piece)))
            for piece in source_parts
        ]
        total = max(1, sum(source_word_counts))
        rendered: list[str] = []
        start = 0
        for index, (source_piece, source_count) in enumerate(zip(source_parts, source_word_counts)):
            if index == len(source_parts) - 1:
                end = len(target_words)
            else:
                end = min(
                    len(target_words),
                    max(start, round(len(target_words) * source_count / total)),
                )
                numeric_anchor = _terminal_numeric_anchor(source_piece, target_words)
                later_content = any(source_word_counts[index + 1:])
                if (
                    numeric_anchor is not None
                    and numeric_anchor >= start
                    and (not later_content or numeric_anchor < len(target_words))
                ):
                    end = numeric_anchor
            rendered.append(replace_source_payload(source_piece, " ".join(target_words[start:end])))
            start = end
        return "".join(
            piece + (source_breaks[index] if index < len(source_breaks) else "")
            for index, piece in enumerate(rendered)
        )

    target_words = re.findall(r"\S+", BREAK_RE.sub(" ", target_without_tags))
    if not target_words:
        return source or ""
    sequence = _coalesce_linguistic_punctuation(_payload_sequence(source or ""))
    wordful = [
        index for index, (kind, value) in enumerate(sequence)
        if kind == "slot" and PAYLOAD_WORD_RE.search(PAYLOAD_TOKEN_RE.sub("", value))
    ]
    if not wordful:
        return source or ""
    source_counts = [
        len(PAYLOAD_WORD_RE.findall(PAYLOAD_TOKEN_RE.sub("", sequence[index][1])))
        for index in wordful
    ]
    total = max(1, sum(source_counts))
    replacements: dict[int, str] = {}
    start = 0
    for position, index in enumerate(wordful):
        if position == len(wordful) - 1:
            end = len(target_words)
        else:
            desired = round(len(target_words) * sum(source_counts[:position + 1]) / total)
            remaining_slots = len(wordful) - position - 1
            if len(target_words) >= len(wordful):
                minimum = start + 1
                maximum = len(target_words) - remaining_slots
            else:
                minimum = start
                maximum = len(target_words)
            end = min(maximum, max(minimum, desired))
        replacements[index] = _replace_payload_slot(
            sequence[index][1], " ".join(target_words[start:end])
        )
        start = end
    rendered = "".join(
        replacements.get(index, value)
        for index, (_kind, value) in enumerate(sequence)
    )
    return _collapse_generated_horizontal_spacing(source, rendered)


def is_drawing_event(text: str) -> bool:
    """Recognize ASS vector payloads without treating them as dialogue.

    A drawing event is only classified when drawing mode is enabled and the
    payload contains vector commands.  This avoids misclassifying ordinary
    prose containing letters such as ``m`` or ``l``.
    """

    raw = text or ""
    drawing = False
    payload_parts: list[str] = []
    cursor = 0
    for match in TAG_RE.finditer(raw):
        payload_parts.append(raw[cursor:match.start()])
        for mode in DRAWING_MODE_RE.finditer(match.group(0)):
            drawing = int(mode.group("scale")) > 0
        cursor = match.end()
    payload_parts.append(raw[cursor:])
    return drawing and bool(VECTOR_COMMAND_RE.search(" ".join(payload_parts)))


def restore_hard_spaces(source: str, target: str) -> str | None:
    """Restore source ``\\h`` controls onto target whitespace conservatively.

    Translation may change word lengths, so offsets are projected
    proportionally and snapped to unused target spaces.  If the target does
    not contain enough spaces, returning ``None`` keeps the caller fail-closed
    instead of silently changing ASS layout semantics.
    """

    source_positions = [match.start() for match in re.finditer(r"\\h", source or "")]
    if not source_positions:
        return target
    target_value = target or ""
    target_hard_spaces = target_value.count(r"\h")
    if target_hard_spaces == len(source_positions):
        return target_value
    if target_hard_spaces > len(source_positions):
        return None
    target_spaces: list[tuple[int, int]] = []
    target_visible_offset = 0
    index = 0
    while index < len(target_value):
        tag = TAG_RE.match(target_value, index)
        if tag:
            index = tag.end()
            continue
        control = CONTROL_RE.match(target_value, index)
        if control:
            if control.group(0) in (r"\N", r"\n"):
                target_visible_offset += 1
            else:
                target_visible_offset += 1
            index = control.end()
            continue
        char = target_value[index]
        if char.isspace():
            target_spaces.append((index, target_visible_offset))
        target_visible_offset += 1
        index += 1
    if len(target_spaces) < len(source_positions):
        return None
    source_visible = visible_text(source, line_break=" ")
    target_visible = visible_text(target_value, line_break=" ")
    source_offsets: list[int] = []
    for position in source_positions:
        source_offsets.append(len(visible_text(source[:position], line_break=" ")))
    used: set[int] = set()
    replacements: dict[int, str] = {}
    for offset in source_offsets:
        desired = round(offset * len(target_visible) / max(1, len(source_visible)))
        candidate, _candidate_offset = min(
            (item for item in target_spaces if item[0] not in used),
            key=lambda item: abs(item[1] - desired),
        )
        used.add(candidate)
        replacements[candidate] = r"\h"
    return "".join(replacements.get(index, char) for index, char in enumerate(target_value))


__all__ = [
    "BREAK_RE", "CONTROL_RE", "DRAWING_MODE_RE", "PAYLOAD_TOKEN_RE", "TAG_RE", "TOKEN_RE",
    "break_count", "break_tokens", "control_tokens", "hard_space_count",
    "inline_tag_counts", "is_drawing_event", "replace_source_payload", "restore_hard_spaces",
    "split_text_with_controls", "visible_text",
]
