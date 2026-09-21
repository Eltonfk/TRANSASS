"""Unit tests for the centralized ass_engine.py module."""

import pytest
import pysubs2

from ass_engine import (
    ASSDocumentAST,
    ASSEventNode,
    clean_residual_override_tags,
    inline_tag_split_word,
    is_drawing_event,
    line_break_inside_word,
    validate_document_structure,
    validate_inline_tags,
    visible_text,
    break_count,
    hard_space_count,
    LEGITIMATE_SHORT_WORDS,
)


def test_clean_residual_override_tags():
    # Tags vazias ou apenas com asterisco devem ser eliminadas
    assert clean_residual_override_tags("Teste {*}de tag") == "Teste de tag"
    assert clean_residual_override_tags("Titulo {* \t}Aqui") == "Titulo Aqui"
    assert clean_residual_override_tags("Normal {} texto") == "Normal texto"
    # Tags com estilo legítimo devem ser preservadas
    assert clean_residual_override_tags(r"{\an8\pos(100,200)}Placa") == r"{\an8\pos(100,200)}Placa"


def test_line_break_inside_word_rejection_and_acceptance():
    # Palavra fatiada ao meio deve falhar
    assert line_break_inside_word(r"Esta pala\Nvra foi quebrada") is True
    assert line_break_inside_word(r"Esta que\Nbra é inválida") is True
    assert line_break_inside_word(r"Fra\Ngil corte") is True

    # Palavras funcionais curtas legítimas (Ep 13 de Your Lie in April) devem passar
    assert line_break_inside_word(r"Festival de Música\Nda Escola Towa") is False
    assert line_break_inside_word(r"Caminhando\Nna rua") is False
    assert line_break_inside_word(r"Olhando\No sol") is False
    assert line_break_inside_word(r"Esperando\Num dia") is False

    # Quebras com tags adjacentes são limites explícitos e devem passar
    assert line_break_inside_word(r"Primeira linha{\i0}\N{\i1}Segunda linha") is False


def test_inline_tag_split_word():
    # Tag no meio de palavra deve ser detectada
    assert inline_tag_split_word(r"Pal{\i1}avra") is True
    # Tag entre palavras ou pontuação é aceitável
    assert inline_tag_split_word(r"Palavra {\i1}outra") is False
    assert inline_tag_split_word(r"Palavra, {\i1}outra") is False


def test_is_drawing_event():
    assert is_drawing_event(r"{\p1}m 0 0 l 100 0 100 100 0 100{\p0}") is True
    assert is_drawing_event(r"{\an8}Texto normal sem vetor") is False
    assert is_drawing_event(r"{\p0}Texto com scale zero") is False


def test_ass_document_ast_in_memory_manipulation():
    raw_ass = (
        "[Script Info]\n"
        "Title: Test Subtitle\n"
        "ScriptType: v4.00+\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,Hello world!\n"
        "Comment: 0,0:00:03.00,0:00:05.00,Default,,0,0,0,,Translator note\n"
        "Dialogue: 0,0:00:05.00,0:00:08.00,Default,,0,0,0,,Line two\\Nwith break\n"
    )
    doc = ASSDocumentAST.from_string(raw_ass)
    assert len(doc) == 3
    assert doc[0].visible == "Hello world!"
    assert doc[1].is_comment is True
    assert doc[2].visible == "Line two\nwith break"

    # Modificação in-memory direta
    doc[0].text = "Olá mundo!"
    doc[2].text = r"Linha dois\Ncom quebra"

    ssa = doc.sync_to_ssa()
    assert ssa.events[0].text == "Olá mundo!"
    assert ssa.events[2].text == r"Linha dois\Ncom quebra"


def test_validate_document_structure():
    raw_source = (
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,{\\b1}Hello{\\b0} world!\n"
        "Comment: 0,0:00:03.00,0:00:05.00,Default,,0,0,0,,Note\n"
    )
    raw_valid = (
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,{\\b1}Olá{\\b0} mundo!\n"
        "Comment: 0,0:00:03.00,0:00:05.00,Default,,0,0,0,,Note\n"
    )
    raw_corrupted_comment = (
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,{\\b1}Olá{\\b0} mundo!\n"
        "Comment: 0,0:00:03.00,0:00:05.00,Default,,0,0,0,,Note alterada\n"
    )

    source_doc = ASSDocumentAST.from_string(raw_source)
    valid_doc = ASSDocumentAST.from_string(raw_valid)
    corrupted_doc = ASSDocumentAST.from_string(raw_corrupted_comment)

    res_valid = validate_document_structure(source_doc, valid_doc)
    assert res_valid["valid"] is True
    assert res_valid["issues"] == []

    res_invalid = validate_document_structure(source_doc, corrupted_doc)
    assert res_invalid["valid"] is False
    assert "ASS_COMMENT_CHANGED" in res_invalid["issues"][0]
