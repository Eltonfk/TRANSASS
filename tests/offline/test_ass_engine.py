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
    prose_delimiter_token_counts,
    prose_delimiter_token_sequence,
    break_count,
    hard_space_count,
    LEGITIMATE_SHORT_WORDS,
)


def test_prose_delimiter_tokens_cover_curly_single_quotes_not_elisions():
    source = "‘Libres ?"
    overflow = "‘Livres?’’"

    assert prose_delimiter_token_sequence(source) == ("‘",)
    assert prose_delimiter_token_sequence(overflow) == ("‘", "’", "’")
    assert prose_delimiter_token_counts(source)["single_curly_quotes"] == 1
    assert prose_delimiter_token_counts(overflow)["single_curly_quotes"] == 3
    assert prose_delimiter_token_counts("l’heure d’água")["single_curly_quotes"] == 0


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
    assert line_break_inside_word(r"possibili\Ndade") is True
    assert line_break_inside_word(r"felicida\Nde") is True
    assert line_break_inside_word(r"qual\Nidade dos serviços") is False
    assert line_break_inside_word(r"ver\Ndade") is True
    assert line_break_inside_word(
        r"Vou ver\NDade amanhã", allowed_short_words=("Dade",),
    ) is False
    # These are valid boundaries between complete words, not guaranteed splits
    # of “qualidade”; the heuristic must not reject them.
    assert line_break_inside_word(r"Minha\Nidade era diferente.") is False
    assert line_break_inside_word(r"Não lembro qual\Nidade você tinha?") is False
    assert line_break_inside_word(r"Qual\Nidade você tinha?") is False

    # Palavras funcionais curtas legítimas (Ep 13 de Your Lie in April) devem passar
    assert line_break_inside_word(r"Festival de Música\Nda Escola Towa") is False
    assert line_break_inside_word(r"Caminhando\Nna rua") is False
    assert line_break_inside_word(r"Olhando\No sol") is False
    assert line_break_inside_word(r"Esperando\Num dia") is False
    # "ir" is a complete Portuguese infinitive, not a fragment before "ao".
    assert line_break_inside_word(r"podemos ir\Nao hospital amanhã.") is False

    # Complete words and hyphenated clitics at a visual break are valid.
    assert line_break_inside_word(r"Vamos colocá-lo\Nna sepultura") is False
    assert line_break_inside_word(r"Eu provavelmente fui\Ntão gentil") is False
    assert line_break_inside_word(r"Mas eu não sei\Nquando ele volta") is False
    assert line_break_inside_word(r"Então tentou explicá-la\Nde várias formas") is False
    # A quebra pode cair depois de uma palavra curta completa (captura Shiki E08).
    assert line_break_inside_word(r"eles são mais de dez\Ncontando os Kirishiki.") is False
    assert line_break_inside_word(r"Contando\Ndez casos") is False
    # Palavras de conteúdo curtas também podem ficar completas em lados opostos da quebra.
    assert line_break_inside_word(r"Dê bom\Nsono e se recupere logo.") is False
    assert line_break_inside_word(r"Uma boa\Nnoite") is False
    # ``há`` is a complete PT-BR verb/adverb, not a fragment split from the
    # preceding word when it begins the next subtitle line.
    assert line_break_inside_word(r"em que você vive\Nhá um ano?") is False
    assert line_break_inside_word(r"viv\Nhá") is True
    assert line_break_inside_word(r"Nunca vi\Ndesse tipo de ritual de perto.") is False
    assert line_break_inside_word(r"rezado para mim\Nquando eu estava rezando!") is False
    assert line_break_inside_word(r"Corre o boato por aí\Nque você tem um poder.") is False
    assert line_break_inside_word(r"Susumu e\NNao morreram, não é?") is True
    assert line_break_inside_word(
        r"Susumu e\NNao morreram, não é?", allowed_short_words=("Nao",),
    ) is False
    # A exceção não pode validar um fragmento que, unido, forma "dezena".
    assert line_break_inside_word(r"dez\Nena") is True
    # `dez` is a valid complete numeral, but this exact boundary slices dezembro.
    assert line_break_inside_word(r"dez\Nembro") is True
    # A source uppercase-token exception cannot override a known fused word.
    assert line_break_inside_word(
        r"dez\Nembro", allowed_short_words=("DEZ",),
    ) is True
    assert line_break_inside_word(
        r"dez\Ncasos", allowed_short_words=("DEZ",),
    ) is False
    assert line_break_inside_word(r"colocá-\Nlo na sepultura") is True

    # Tags adjacentes são ignoradas para verificar os caracteres visíveis.
    assert line_break_inside_word(r"Primeira linha{\i0}\N{\i1}Segunda linha") is False
    assert line_break_inside_word(r"pala{\i0}\N{\i1}vra") is True


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


def test_document_structure_accepts_shiki_e08_complete_word_break():
    source = ASSDocumentAST.from_string(
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        r"Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,ils sont plus de dix\Nen comptant les Kirishiki."
        "\n"
    )
    candidate = ASSDocumentAST.from_string(
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        r"Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,eles são mais de dez\Ncontando os Kirishiki."
        "\n"
    )

    result = validate_document_structure(source, candidate, selected_indices={0})

    assert result["valid"] is True
    assert result["issues"] == []


def test_document_structure_accepts_shiki_e09_break_between_complete_words():
    source = ASSDocumentAST.from_string(
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        r"Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,D'accord. Dors bien\Net rétablis-toi vite."
        "\n"
    )
    candidate = ASSDocumentAST.from_string(
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        r"Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,Tudo bem. Dê bom\Nsono e se recupere logo."
        "\n"
    )

    result = validate_document_structure(source, candidate, selected_indices={0})

    assert result["valid"] is True
    assert result["issues"] == []


def test_document_structure_uses_only_event_local_protected_name_at_break():
    source = ASSDocumentAST.from_string(
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        r"Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,Setsuko, Mikiyasu, Susumu et Nao\Nsont tous morts, non ?"
        "\n"
    )
    candidate = ASSDocumentAST.from_string(
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        r"Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,Setsuko, Mikiyasu, Susumu e\NNao morreram, não é?"
        "\n"
    )

    rejected = validate_document_structure(source, candidate, selected_indices={0})
    accepted = validate_document_structure(
        source,
        candidate,
        selected_indices={0},
        allowed_break_words_by_event={0: ("Nao",)},
    )

    assert rejected["valid"] is False
    assert "evento 0: LINE_BREAK_INSIDE_WORD" in rejected["issues"]
    assert accepted["valid"] is True
    assert accepted["issues"] == []


def test_line_break_legitimate_short_verbs():
    assert line_break_inside_word(r"nada do que eu injei\Nfez efeito.") is False
    assert line_break_inside_word(r"Eles\Ndão o aviso.") is False
    assert line_break_inside_word(r"Ele\Npõe o copo na mesa.") is False
    assert line_break_inside_word(r"Ela\Nriu da situação.") is False
    assert line_break_inside_word(r"Isso\Ndói demais.") is False
    assert line_break_inside_word(r"Eles\Ntêm razão.") is False
    assert line_break_inside_word(r"Ele\Nlê muito.") is False
    assert line_break_inside_word(r"Quem\Ncrê sempre alcança.") is False
    assert line_break_inside_word(r"Eles voltarão a si\Ndentro de algum tempo.") is False
    assert line_break_inside_word(r"Eu disse a ti\Nque viria.") is False


def test_validate_document_structure_centisecond_quantization_tolerance():
    source = ASSDocumentAST.from_string(
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        r"Dialogue: 0,0:00:01.23,0:00:03.45,Default,,0,0,0,,Olá mundo!"
        "\n"
    )
    # 4ms difference (within 10ms centisecond tolerance)
    candidate_close = ASSDocumentAST.from_string(
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        r"Dialogue: 0,0:00:01.23,0:00:03.45,Default,,0,0,0,,Olá mundo!"
        "\n"
    )
    candidate_close.events[0].start = source.events[0].start + 4
    candidate_close.events[0].end = source.events[0].end - 5
    result_close = validate_document_structure(source, candidate_close)
    assert result_close["valid"] is True

    # 15ms difference (exceeds 10ms tolerance)
    candidate_far = ASSDocumentAST.from_string(
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        r"Dialogue: 0,0:00:01.23,0:00:03.45,Default,,0,0,0,,Olá mundo!"
        "\n"
    )
    candidate_far.events[0].start = source.events[0].start + 15
    result_far = validate_document_structure(source, candidate_far)
    assert result_far["valid"] is False
    assert "evento 0: campo start alterado" in result_far["issues"]
