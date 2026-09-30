"""Consolidated in-memory visual effects and karaoke engine for the V3 unified architecture.

Unifies per-character visual glyph reconstruction, style gradient tracking,
and karaoke timing augmentation in a single pass over the in-memory ASSDocumentAST.
Includes proven fail-safe fallbacks to protect against malformed asterisk tags
or unaligned glyph tracking.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from ass_engine import (
    ASSDocumentAST,
    ASSEventNode,
    clean_residual_override_tags,
    is_drawing_event,
    visible_text,
    TAG_RE,
    CONTROL_RE,
    inline_tag_anchor_signature,
)

# ---------------------------------------------------------------------------
# Karaoke & Song Detection
# ---------------------------------------------------------------------------

KARAOKE_TAG_RE = re.compile(r"\\(?:kt|kf|ko|k)\d+", re.I)
SONG_STYLE_RE = re.compile(r"(?<![a-z])(?:song|op|ed|opening|ending|insert)(?![a-z])", re.IGNORECASE)
PRIMARY_COLOR_RE = re.compile(r"\\(?:1c|c)&H(?P<color>[0-9A-Fa-f]{6})&")


def has_karaoke_tags(text: str) -> bool:
    """Return whether the text contains relative syllable timing tags."""
    return bool(KARAOKE_TAG_RE.search(text or ""))


def is_song_or_karaoke_node(node: ASSEventNode) -> bool:
    """Identify if an event is a karaoke line or song lyric."""
    if node.is_comment or node.is_drawing:
        return False
    if has_karaoke_tags(node.text):
        return True
    style = (node.style or "").casefold()
    return bool(SONG_STYLE_RE.search(style))


# ---------------------------------------------------------------------------
# Visual Glyphs & Grapheme Processing
# ---------------------------------------------------------------------------

def grapheme_clusters(text: str) -> list[str]:
    """Segment text into individual graphemes (UAX #29 compliant for Unicode)."""
    clusters: list[str] = []
    regional_run = 0
    join_next = False
    for char in text:
        code = ord(char)
        combining = bool(unicodedata.combining(char)) or unicodedata.category(char) in {"Mc", "Me"}
        variation = 0xFE00 <= code <= 0xFE0F or 0xE0100 <= code <= 0xE01EF
        modifier = 0x1F3FB <= code <= 0x1F3FF
        zwj = code == 0x200D
        regional = 0x1F1E6 <= code <= 0x1F1FF
        if not clusters:
            clusters.append(char)
            regional_run = 1 if regional else 0
            join_next = zwj
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


ANIMATION_TAG_RE = re.compile(r"\\t\([^)]*\)")


def extract_primary_colors(text: str) -> list[str]:
    """Extract ordered sequence of primary colors from override tags, ignoring dynamic transforms."""
    static_text = ANIMATION_TAG_RE.sub("", text or "")
    colors: list[str] = []
    for match in PRIMARY_COLOR_RE.finditer(static_text):
        colors.append(f"&H{match.group('color').upper()}&")
    return colors



def apply_glyph_color_gradient(target_text: str, colors: list[str], prefix_tags: str = "") -> str:
    """Project a source per-character color gradient onto translated graphemes."""
    graphemes = grapheme_clusters(target_text)
    if not graphemes or not colors:
        return f"{prefix_tags}{target_text}"

    # If count matches 1:1, apply directly
    if len(colors) == len(graphemes):
        parts = []
        for i, g in enumerate(graphemes):
            tag = f"{prefix_tags}{{\\c{colors[i]}}}" if i == 0 else f"{{\\c{colors[i]}}}"
            parts.append(f"{tag}{g}")
        return "".join(parts)

    # If counts differ (due to translation length), interpolate colors gracefully
    parts = []
    num_colors = len(colors)
    num_graphemes = len(graphemes)
    for i, g in enumerate(graphemes):
        color_idx = min(int(i * num_colors / num_graphemes), num_colors - 1)
        tag = f"{prefix_tags}{{\\c{colors[color_idx]}}}" if i == 0 else f"{{\\c{colors[color_idx]}}}"
        parts.append(f"{tag}{g}")
    return "".join(parts)


def rebuild_source_glyph_gradient(source: str, target: str) -> str | None:
    """Project static source colors without hoisting other override commands.

    Non-color blocks keep proportional grapheme anchors. If a non-empty
    source interval would collapse, or reset/animated color scope is involved,
    the transformation has no proven safe reconstruction and must fail closed.
    """
    colors = extract_primary_colors(source)
    source_visible = visible_text(source)
    source_graphemes = grapheme_clusters(source_visible)
    if not (
        re.search(r"[\wÀ-ÿ]\s*\{", source)
        and len(colors) >= 2
        and len(colors) >= len(source_graphemes) - 2
    ):
        return None
    target_graphemes = grapheme_clusters(target)
    if (
        not source_graphemes or not target_graphemes
        or CONTROL_RE.search(source) or CONTROL_RE.search(target)
        or "\n" in source_visible or "\n" in target
        or re.search(r"\\(?:t\(|r)", "".join(TAG_RE.findall(source)))
    ):
        raise ValueError("ASS_INLINE_TAG_ANCHOR_FAILURE:UNSUPPORTED_GRADIENT_SCOPE")

    source_offsets = {0: 0}
    offset = 0
    for index, grapheme in enumerate(source_graphemes, 1):
        offset += len(grapheme)
        source_offsets[offset] = index
    anchors: dict[int, list[str]] = {}
    owner_by_target = {0: 0, len(target_graphemes): len(source_graphemes)}
    for match, (_tag, source_offset) in zip(TAG_RE.finditer(source), inline_tag_anchor_signature(source)):
        non_color = clean_residual_override_tags(PRIMARY_COLOR_RE.sub("", match.group(0)))
        if not non_color:
            continue
        if source_offset not in source_offsets:
            raise ValueError("ASS_INLINE_TAG_ANCHOR_FAILURE:GRAPHEME_SPLIT")
        source_anchor = source_offsets[source_offset]
        target_anchor = round(source_anchor * len(target_graphemes) / len(source_graphemes))
        previous_owner = owner_by_target.get(target_anchor)
        if previous_owner is not None and previous_owner != source_anchor:
            raise ValueError("ASS_INLINE_TAG_ANCHOR_FAILURE:COLLAPSED_GRADIENT_SCOPE")
        owner_by_target[target_anchor] = source_anchor
        anchors.setdefault(target_anchor, []).append(non_color)

    rendered = apply_glyph_color_gradient(target, colors)
    color_blocks = list(TAG_RE.finditer(rendered))
    if len(color_blocks) != len(target_graphemes):
        raise ValueError("ASS_INLINE_TAG_ANCHOR_FAILURE:GRADIENT_RECONSTRUCTION")
    parts = []
    for index, block in enumerate(color_blocks):
        parts.extend(anchors.get(index, ()))
        end = color_blocks[index + 1].start() if index + 1 < len(color_blocks) else len(rendered)
        parts.append(rendered[block.start():end])
    parts.extend(anchors.get(len(color_blocks), ()))
    return "".join(parts)


# ---------------------------------------------------------------------------
# Unified Post-Processing Engine
# ---------------------------------------------------------------------------

class InMemoryEffectsEngine:
    """Unified engine to process visual glyphs and karaoke effects in memory."""

    def __init__(self, enable_visual_glyphs: bool = True, enable_karaoke: bool = True) -> None:
        self.enable_visual_glyphs = enable_visual_glyphs
        self.enable_karaoke = enable_karaoke

    def process_effects(self, doc: ASSDocumentAST, original_doc: ASSDocumentAST | None = None) -> dict[str, Any]:
        """Apply visual glyphs and karaoke formatting directly to nodes in doc."""
        stats = {
            "visual_glyphs_applied": 0,
            "visual_glyphs_fallback": 0,
            "karaoke_preserved": 0,
            "drawings_preserved": 0,
        }

        orig_map = {node.index: node for node in original_doc.events} if original_doc else {}

        for node in doc.events:
            # 1. Skip comments and vector drawings
            if node.is_comment or node.is_drawing:
                if node.is_drawing:
                    stats["drawings_preserved"] += 1
                continue

            orig_node = orig_map.get(node.index)
            orig_text = orig_node.text if orig_node else node.text

            # 2. Karaoke handling
            if self.enable_karaoke and is_song_or_karaoke_node(node):
                if has_karaoke_tags(orig_text):
                    # Reaplica o envelope ASS da fonte sobre o texto traduzido.
                    # Isso ancora os tempos/tags no alvo sem substituir a
                    # tradução pelo payload original.
                    from ass_structure import replace_source_payload

                    node.text = replace_source_payload(orig_text, node.visible)
                    stats["karaoke_preserved"] += 1

            # 3. Visual glyph gradient handling
            if self.enable_visual_glyphs and orig_node:
                if node.visible == orig_node.visible:
                    # Identity needs no projection and preserves even complex
                    # animated/reset scopes exactly under source authority.
                    node.text = orig_text
                else:
                    try:
                        gradient = rebuild_source_glyph_gradient(orig_text, node.visible)
                    except Exception as exc:
                        raise ValueError("ASS_INLINE_TAG_ANCHOR_FAILURE:GRADIENT_RECONSTRUCTION") from exc
                    if gradient is not None:
                        node.text = gradient
                        stats["visual_glyphs_applied"] += 1

            # 4. Global override tag cleanup (remove empty or asterisk residual tags)
            node.text = clean_residual_override_tags(node.text)

        return stats
