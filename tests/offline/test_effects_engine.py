"""Unit tests for the in-memory effects_engine.py module."""

import pytest
from ass_engine import ASSDocumentAST, ASSEventNode
from effects_engine import (
    InMemoryEffectsEngine,
    apply_glyph_color_gradient,
    extract_primary_colors,
    grapheme_clusters,
    has_karaoke_tags,
    is_song_or_karaoke_node,
)


def test_grapheme_clusters():
    assert grapheme_clusters("Olá") == ["O", "l", "á"]
    assert grapheme_clusters("Kaori") == ["K", "a", "o", "r", "i"]
    # Emojis com modificadores ou variantes
    clusters = grapheme_clusters("👋🏽")
    assert len(clusters) == 1


def test_extract_primary_colors():
    raw = r"{\c&HFFFFFF&}A{\c&HFF0000&}B{\1c&H00FF00&}C"
    colors = extract_primary_colors(raw)
    assert colors == ["&HFFFFFF&", "&HFF0000&", "&H00FF00&"]


def test_apply_glyph_color_gradient():
    colors = ["&HFF0000&", "&H00FF00&", "&H0000FF&"]
    text = "Sol"
    gradient = apply_glyph_color_gradient(text, colors)
    assert gradient == r"{\c&HFF0000&}S{\c&H00FF00&}o{\c&H0000FF&}l"


def test_karaoke_detection():
    node_karaoke = ASSEventNode(index=0, style="ED", text=r"{\k20}A{\k30}me{\k25}ga{\k40}furu")
    node_normal = ASSEventNode(index=1, style="Default", text="Hello world")
    node_song_style = ASSEventNode(index=2, style="OP-Romaji", text="Hikaru nara")

    assert has_karaoke_tags(node_karaoke.text) is True
    assert has_karaoke_tags(node_normal.text) is False
    assert is_song_or_karaoke_node(node_karaoke) is True
    assert is_song_or_karaoke_node(node_normal) is False
    assert is_song_or_karaoke_node(node_song_style) is True


def test_in_memory_effects_engine_pipeline():
    raw_source = (
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:01.00,0:00:03.00,Title,,0,0,0,,{\\an8}{\\c&HFF0000&}T{\\c&H00FF00&}w{\\c&H0000FF&}o\n"
        "Dialogue: 0,0:00:05.00,0:00:08.00,OP,,0,0,0,,{\\k10}Hi{\\k20}ka{\\k30}ru\n"
        "Dialogue: 0,0:00:09.00,0:00:11.00,Default,,0,0,0,,{\\p1}m 0 0 l 10 10\n"
    )
    raw_translated = (
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:01.00,0:00:03.00,Title,,0,0,0,,{\\an8}Dois\n"
        "Dialogue: 0,0:00:05.00,0:00:08.00,OP,,0,0,0,,Hikaru\n"
        "Dialogue: 0,0:00:09.00,0:00:11.00,Default,,0,0,0,,{\\p1}m 0 0 l 10 10\n"
    )

    orig_doc = ASSDocumentAST.from_string(raw_source)
    trans_doc = ASSDocumentAST.from_string(raw_translated)

    engine = InMemoryEffectsEngine(enable_visual_glyphs=True, enable_karaoke=True)
    stats = engine.process_effects(trans_doc, orig_doc)

    assert stats["visual_glyphs_applied"] == 1
    assert stats["karaoke_preserved"] == 1
    assert stats["drawings_preserved"] == 1

    # O evento de título deve ter recebido o gradiente de cores projetado sobre "Dois"
    assert r"\c&H" in trans_doc[0].text
    # O evento de karaokê deve ter preservado o timing original da música
    assert r"\k10" in trans_doc[1].text


def test_animated_transform_colors_ignored_by_effects_engine():
    raw_source = (
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        r"Dialogue: 0,0:00:33.76,0:00:41.06,Signs,,0,0,0,,{=2}{\move(225,396,281.865,396.045,25,7283)\c&H0C1E00&\t(835,1485,\c&H332419&)}Eat" "\n"
    )
    raw_translated = (
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        r"Dialogue: 0,0:00:33.76,0:00:41.06,Signs,,0,0,0,,{=2}{\move(225,396,281.865,396.045,25,7283)\c&H0C1E00&\t(835,1485,\c&H332419&)}Comer" "\n"
    )
    orig_doc = ASSDocumentAST.from_string(raw_source)
    trans_doc = ASSDocumentAST.from_string(raw_translated)

    engine = InMemoryEffectsEngine(enable_visual_glyphs=True, enable_karaoke=True)
    stats = engine.process_effects(trans_doc, orig_doc)

    assert stats["visual_glyphs_applied"] == 0
    assert trans_doc[0].text == r"{=2}{\move(225,396,281.865,396.045,25,7283)\c&H0C1E00&\t(835,1485,\c&H332419&)}Comer"

