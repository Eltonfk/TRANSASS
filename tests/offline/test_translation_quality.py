"""Offline regressions for high-confidence French-to-pt-BR translation risks."""
from __future__ import annotations

from translation_quality import (
    extract_repeated_french_names,
    extract_repeated_names,
    french_ptbr_context_hints,
    is_likely_romanized_japanese_name_token,
    is_reviewed_romanized_japanese_name_token,
    names_in_source,
    progressive_protected_name_prefixes_in_sequence,
    restore_source_enclosing_ascii_quotes,
    source_name_spellings,
    translation_quality_flags,
)


def _flags(source: str, translated: str, *, target: str = "pt-BR") -> tuple[str, ...]:
    return translation_quality_flags(
        source,
        translated,
        source_language="francês",
        target_language=target,
    )


def test_repeated_mid_clause_name_is_protected_but_sentence_starters_are_not():
    names = extract_repeated_french_names(
        (
            "Tu crois que Nao reviendra ce soir ?",
            "Nao m'appelle !",
            "Vraiment ?",
            "Vraiment.",
            "Je pense que oui.",
        ),
        "francês",
    )

    assert names == ("Nao",)
    assert names_in_source("Nao m'appelle !", names) == ("Nao",)
    assert names_in_source("Je ne sais pas.", names) == ()


def test_ellipsized_fragment_alone_never_authorizes_name_identity():
    names = ("Toshio",)
    assert progressive_protected_name_prefixes_in_sequence(
        ("To...", "Toshi...", "Toshio..."), 0, names,
    ) == ("Toshio",)
    assert progressive_protected_name_prefixes_in_sequence(
        ("To...", "Toshi...", "Toshio..."), 1, names,
    ) == ("Toshio",)
    assert progressive_protected_name_prefixes_in_sequence(
        ("To...", "Toshi...", "Toshio..."), 2, names,
    ) == ()
    assert progressive_protected_name_prefixes_in_sequence(
        ("To...", "Toshi..."), 0, names,
    ) == ()
    assert progressive_protected_name_prefixes_in_sequence(
        ("To...", "Toshi...", "Tokujirô..."), 0, ("Tokujirô", "Toshio"),
    ) == ()
    assert progressive_protected_name_prefixes_in_sequence(
        ("To...", "Toshi...", "Toshio..."), 0, ("Tokujirô",),
    ) == ()
    assert progressive_protected_name_prefixes_in_sequence(
        ("To...", "Toshio...", "Toshi..."), 0, names,
    ) == ()
    assert progressive_protected_name_prefixes_in_sequence(
        ("To...", "Toshi...", "Toshio..."), 3, names,
    ) == ()
    assert extract_repeated_french_names(("Tu crois que Nao reviendra ?",), "francês") == ()


def test_all_caps_event_corroborates_an_existing_title_case_name_only():
    assert extract_repeated_french_names(
        ("Les Shi Ki traquent les hommes.", "SHI KI"),
        "francês",
    ) == ("Ki", "Shi")
    assert extract_repeated_french_names(("SHI KI",), "francês") == ()


def test_progressive_name_resolution_checks_real_ellipsis_and_non_stopwords():
    names = ("Toshio",)
    assert progressive_protected_name_prefixes_in_sequence(
        ("To..", "Toshi...", "Toshio..."), 0, names,
    ) == ()
    assert progressive_protected_name_prefixes_in_sequence(
        ("To...", "Le...", "Toshio..."), 0, names,
    ) == ()
    assert progressive_protected_name_prefixes_in_sequence(
        ("To…", "Toshi…", "Toshio…"), 0, names,
    ) == ("Toshio",)


def test_protected_name_source_spellings_preserve_uppercase_and_mixed_case():
    names = ("Muroi", "Seishin")

    assert source_name_spellings("SHINMEI MUROI, PÈRE DE SEISHIN", names) == (
        "MUROI", "SEISHIN",
    )
    assert source_name_spellings("Muroi puis MUROI", ("Muroi",)) == (
        "Muroi", "MUROI",
    )


def test_name_detection_avoids_capitalized_discourse_words_and_keeps_short_names():
    names = extract_repeated_french_names(
        (
            "Je pense que Alors, il est parti.",
            "Tu sais que Alors, c'est fini.",
            "Je crois que Ai reviendra.",
            "Tu sais que Ai est là.",
        ),
        "français",
    )

    assert names == ("Ai",)


def test_name_detection_does_not_treat_comma_following_discourse_word_as_name():
    names = extract_repeated_french_names(
        (
            "Je pense, Alors il est parti.",
            "Tu sais, Alors c'est fini.",
        ),
        "francais",
    )

    assert names == ()


def test_single_name_candidate_requires_romaji_shape_and_excludes_french_call_words():
    assert is_likely_romanized_japanese_name_token("Shinmei")
    assert is_likely_romanized_japanese_name_token("Tokujirô")
    assert not is_likely_romanized_japanese_name_token("Akira")
    assert is_reviewed_romanized_japanese_name_token("Akira")
    assert is_reviewed_romanized_japanese_name_token("Kaori")
    assert is_reviewed_romanized_japanese_name_token("Sachiko")
    assert is_reviewed_romanized_japanese_name_token("Kiyomi")
    assert is_reviewed_romanized_japanese_name_token("Atsushi")
    for unreviewed_word in (
        "Banane", "Marianne", "Papaye", "Sakura",
    ):
        assert not is_reviewed_romanized_japanese_name_token(unreviewed_word)
    for word in (
        "Bonjour", "Merci", "Oui", "Bravo", "Bonsoir", "Attention",
        "Attends", "Vite", "Chérie", "Tsunami", "Chanson", "Chapeau",
        "Chimie", "Chimère", "Chinoise", "Chienne", "Pyjama",
    ):
        assert not is_likely_romanized_japanese_name_token(word)


def test_french_dieu_is_translated_as_deus_not_protected_as_a_person_name():
    sources = (
        "Serais-tu vraiment\nl'abandonnée de Dieu ?",
        "Mon Dieu,\nquelle tête tu as !",
    )
    names = extract_repeated_french_names(sources, "francês")

    assert "Dieu" not in names
    for source, translated in zip(
        sources,
        (
            "Será que você é realmente\na abandonada por Deus?",
            "Meu Deus,\nque cara você está!",
        ),
    ):
        flags = translation_quality_flags(
            source,
            translated,
            source_language="francês",
            target_language="pt-BR",
            protected_names=names_in_source(source, names),
        )
        assert not any(flag.startswith("PROTECTED_NAME_NOT_PRESERVED:") for flag in flags)


def test_known_french_false_friends_and_idiom_literal_are_flagged():
    cases = (
        (
            "- Puisque vous insistez...",
            "Como você insiste... Tudo bem...",
            "FRENCH_INSISTEZ_ADDED_CONCESSION",
        ),
        (
            "– Parce que ce sont des okiagari !",
            "Ouça-me!\nSe não os caçarmos,",
            "FRENCH_OKIAGARI_MEANING_SHIFT",
        ),
        (
            "Vade retro !",
            "Vade retro!",
            "LATIN_VADE_RETRO_UNTRANSLATED",
        ),
        (
            '"Vade retro !"',
            '"Vade retro!"',
            "LATIN_VADE_RETRO_UNTRANSLATED",
        ),
        (
            "“Vade retro !”",
            "“Vade retro!”",
            "LATIN_VADE_RETRO_UNTRANSLATED",
        ),
        (
            "D'accord. Dors bien et rétablis-toi vite.",
            "Tudo bem. Dê bom sono e se recupere logo.",
            "FRENCH_DORS_BIEN_LITERAL",
        ),
        ("Une panne de courant ?", "Um blefe de energia?", "FRENCH_POWER_OUTAGE_FALSE_FRIEND"),
        ("Ils sont peut-être nyctalopes.", "Eles podem ser notívagos.", "FRENCH_NYCTALOPE_FALSE_FRIEND"),
        ("Rester de garde tout seul !", "Devolver a guarda sozinho!", "FRENCH_GARDE_LITERAL"),
        ("Honte à toi !", "Você tem vergonha de mim!", "FRENCH_HONTE_ROLE_REVERSAL"),
        ("Belle-maman !", "Cunhada!", "FRENCH_BELLE_MAMAN_KINSHIP"),
        (
            "C'est la série chez eux...",
            "É a vez deles terem problemas...",
            "FRENCH_SERIE_IDIOM_LITERAL",
        ),
        (
            "C'est à sa femme de s'occuper de ça !",
            "É com a sua mulher de se ocupar disso!",
            "FRENCH_WIFE_DUTY_UNGRAMMATICAL",
        ),
        (
            "Jauge un peu l'humeur de ma mère et repars.",
            "Avalio um pouco o humor da minha mãe e vou embora.",
            "FRENCH_IMPERATIVE_PERSON_SHIFT",
        ),
        (
            "POMPES FUNÈBRES DE SOTOBA",
            "POMPES FUNÈBRES DE SOTOBA",
            "FRENCH_POMPES_FUNEBRES_UNTRANSLATED",
        ),
    )

    for source, output, expected_flag in cases:
        assert expected_flag in _flags(source, output), (source, output)


def test_ptbr_gender_gate_rejects_observed_agreement_errors():
    assert "PTBR_GENDER_MISMATCH:uma:balde" in _flags(
        "Il changea le contenu d'un seau.", "Ele mudou o conteúdo de uma balde.",
    )
    assert "PTBR_GENDER_MISMATCH:um:parada" in _flags(
        "Un arrêt cardiaque.", "Um parada cardíaca.",
    )
    assert "PTBR_GENDER_MISMATCH:os:vítimas" in _flags(
        "Les victimes devraient se réveiller.", "Os vítimas deveriam acordar.",
    )


def test_correct_translations_and_unrelated_context_are_not_flagged():
    assert _flags("Puisque vous insistez...", "Já que você insiste...") == ()
    assert _flags("Puisque vous insistez...", "Tudo bem, já que você insiste.") == ()
    assert _flags("Parce que ce sont des okiagari !", "Porque são okiagari!") == ()
    assert _flags("Vade retro !", "Para trás!") == ()
    assert _flags("Vade retro !", "Afaste-se!") == ()
    assert _flags("Dors bien et rétablis-toi vite.", "Durma bem e se recupere logo.") == ()
    assert _flags("Une panne de courant ?", "Falta de energia?") == ()
    assert _flags("Ils sont peut-être nyctalopes.", "Talvez eles enxerguem no escuro.") == ()
    assert _flags("Une phrase ordinaire.", "Uma frase comum.") == ()
    assert _flags("Une panne de courant.", "O blefe dele deu certo.") == ()


def test_french_ces_demonstrative_residue_is_rejected_without_blocking_correct_translation():
    source = "C'est comment éliminer\\Nces Shi Ki."

    assert "FRENCH_CES_DEMONSTRATIVE_UNTRANSLATED" in _flags(
        source, "É como eliminar\\Nces Shi Ki."
    )
    assert _flags(source, "É como eliminar\\Nesses Shi Ki.") == ()
    assert "FRENCH_CES_DEMONSTRATIVE_UNTRANSLATED" in _flags(
        source, "É como eliminar\\NCES Shi Ki."
    )
    assert _flags("Cesaria chegou.", "Cesaria chegou.") == ()
    assert any(
        "demonstrativo francês ‘ces’" in hint
        for hint in french_ptbr_context_hints((source,), "francês", "pt-BR")
    )


def test_french_ces_rule_preserves_sigla_and_rejects_known_lowercase_residue():
    source = "Le CES publie ces avis."

    assert _flags(source, "O CES publica esses avis.") == ()
    assert "FRENCH_CES_DEMONSTRATIVE_UNTRANSLATED" in _flags(
        source, "O CES publica ces avis."
    )
    assert "FRENCH_CES_DEMONSTRATIVE_UNTRANSLATED" in _flags(
        source, "O CES publica CES avis."
    )
    assert french_ptbr_context_hints(
        ("CES arrive au laboratoire.",), "francês", "pt-BR",
    ) == ()


def test_french_ces_all_caps_detection_is_limited_to_clear_contexts():
    all_caps_source = "LES MEMBRES DU CES COMMENTENT CES PHOTOS."
    assert _flags(
        all_caps_source,
        "OS MEMBROS DO CES COMENTAM ESSAS FOTOS.",
    ) == ()
    assert "FRENCH_CES_DEMONSTRATIVE_UNTRANSLATED" in _flags(
        all_caps_source,
        "OS MEMBROS DO CES COMENTAM CES FOTOS.",
    )
    assert "FRENCH_CES_DEMONSTRATIVE_UNTRANSLATED" in _flags(
        "ILS PARLENT DE CES PHOTOS.",
        "ELES FALAM DE CES FOTOS.",
    )
    assert _flags(
        "Le sigle CES représente ces données.",
        "A sigla CES representa esses dados.",
    ) == ()


def test_french_ces_title_case_is_preserved_only_with_quoted_source_evidence():
    source = "La diffusion de « Ces Séries » remplace la sélection de ces séries."
    assert _flags(
        source,
        "A transmissão de « Ces Séries » substitui a seleção dessas séries.",
    ) == ()
    assert "FRENCH_CES_DEMONSTRATIVE_UNTRANSLATED" in _flags(
        source,
        "A transmissão de « Ces Séries » substitui a seleção de ces séries.",
    )
    assert "FRENCH_CES_DEMONSTRATIVE_UNTRANSLATED" in _flags(
        source,
        "A transmissão de « Ces Séries » substitui a seleção de Ces séries.",
    )


def test_ass_quote_delimiter_ownership_mismatch_is_a_repairable_risk():
    source = '"Libres ?'

    assert "ASS_DELIMITER_TOKEN_COUNT_MISMATCH" in _flags(source, '"Livres?"')
    assert "ASS_DELIMITER_TOKEN_COUNT_MISMATCH" not in _flags(source, '"Livres?')


def test_only_unowned_terminal_ascii_quote_closures_are_removed():
    from translation_quality import remove_unowned_terminal_ascii_quote_closures

    repair = remove_unowned_terminal_ascii_quote_closures
    assert repair('"Libres ?', '"Livres?"') == '"Livres?'
    assert repair('"Libres ?', '"Livres?".') == '"Livres?.'
    assert repair('"Libres ?', '"Livres?" "autre"') == '"Livres?" "autre"'
    assert repair('"Texte"', '"Texto""') == '"Texto""'
    assert repair('“Texte', '“Texto”') == '“Texto”'
    assert repair('(Texte', '(Texto)') == '(Texto)'


def test_ass_delimiter_order_mismatch_is_a_repairable_risk_even_when_counts_match():
    assert "ASS_DELIMITER_SEQUENCE_MISMATCH" in _flags("(Texte)", ")Texto(")
    assert "ASS_DELIMITER_SEQUENCE_MISMATCH" not in _flags("(Texte)", "(Texto)")


def test_curly_single_quote_overflow_is_rejected_but_cross_event_open_quote_is_allowed():
    source = "‘Libres ?"

    assert _flags(source, "‘Livres ?") == ()
    assert "ASS_DELIMITER_TOKEN_COUNT_MISMATCH" in _flags(source, "‘Livres?’’")
    assert "ASS_DELIMITER_SEQUENCE_MISMATCH" in _flags(source, "‘Livres?’’")


def test_risks_are_scoped_to_french_to_brazilian_portuguese():
    assert _flags("Une panne de courant.", "Um blefe de energia.", target="português europeu") == ()
    assert translation_quality_flags(
        "A power outage.",
        "A blefe de energia.",
        source_language="inglês",
        target_language="pt-BR",
    ) == ()
    assert "FRENCH_POWER_OUTAGE_FALSE_FRIEND" in translation_quality_flags(
        "Une panne de courant.",
        "Um blefe de energia.",
        source_language="français",
        target_language="pt-BR",
    )
    assert french_ptbr_context_hints(
        ("Une panne de courant.",), "français", "pt-BR",
    )


def test_context_prompt_hints_are_specific_to_terms_in_the_batch():
    hints = french_ptbr_context_hints(
        ("Dors bien. Une panne de courant ?", "Ils sont peut-être nyctalopes."),
        "fre",
        "português do Brasil (pt-BR)",
    )
    assert len(hints) == 3
    assert any("‘durma bem’/‘descanse bem’" in hint for hint in hints)
    assert any("falta/queda de energia" in hint for hint in hints)
    assert any("enxerga no escuro" in hint for hint in hints)
    assert french_ptbr_context_hints(("Une panne de courant ?",), "fre", "inglês") == ()


def test_vade_retro_prompt_hint_is_scoped_to_french_ptbr():
    hints = french_ptbr_context_hints(("Vade retro !",), "francês", "pt-BR")
    assert len(hints) == 1
    assert "‘Para trás!’/‘Afaste-se!’" in hints[0]
    assert french_ptbr_context_hints(("Vade retro !",), "inglês", "pt-BR") == ()
    assert translation_quality_flags(
        "Vade retro !",
        "Vade retro!",
        source_language="inglês",
        target_language="pt-BR",
    ) == ()


def test_observed_french_hallucination_hints_are_batch_specific():
    hints = french_ptbr_context_hints(
        (
            "- Puisque vous insistez...",
            "– Parce que ce sont des okiagari !",
        ),
        "francês",
        "pt-BR",
    )
    assert any("não acrescente uma segunda fala" in hint for hint in hints)
    assert any("afirma causalmente que eles são okiagari" in hint for hint in hints)


def test_ambiguous_lexical_choices_are_advisory_not_hard_failures():
    hints = french_ptbr_context_hints(
        ("Une femme volatile. Dis-moi ce que tu en penses. Sœur cadette. Ma clinique !",),
        "francês",
        "pt-BR",
    )
    assert any("volúvel/inconstante" in hint for hint in hints)
    assert any("me diga" in hint for hint in hints)
    assert any("irmã mais nova" in hint for hint in hints)
    assert any("escolha ‘hospital’ apenas se o contexto" in hint for hint in hints)
    assert _flags("Une femme volatile.", "Uma mulher volátil.") == ()
    assert _flags("C'est ma clinique, ici !", "Este é meu hospital, aqui!") == ()


def test_restore_source_enclosing_ascii_quotes():
    assert restore_source_enclosing_ascii_quotes(
        '"On ne tue jamais sans raison."',
        'Não se mata jamais sem motivo.',
    ) == '"Não se mata jamais sem motivo."'
    assert restore_source_enclosing_ascii_quotes(
        '"On ne tue jamais\\Nsans raison."',
        'Não se mata jamais\\Nsem motivo.',
    ) == '"Não se mata jamais\\Nsem motivo."'
    assert restore_source_enclosing_ascii_quotes(
        'Il a dit "bonjour".',
        'Ele disse "olá".',
    ) == 'Ele disse "olá".'
    assert restore_source_enclosing_ascii_quotes(
        '"Déjà cité"',
        '"Já citado"',
    ) == '"Já citado"'


def test_extract_repeated_names_english_and_french():
    sources_en = (
        "I know Chao He is dangerous.",
        "We must arrest Chao He immediately!",
        "His Hex Talent magic is powerful.",
        "The Hex power came from Bow City.",
    )
    names_en = extract_repeated_names(sources_en, "inglês")
    assert "Chao" in names_en
    assert "Hex" in names_en

    # French legacy behavior preserved
    sources_fr = (
        "Je pense que Toshio est parti.",
        "Tu sais bien que Toshio reviendra.",
    )
    names_fr = extract_repeated_names(sources_fr, "francês")
    assert "Toshio" in names_fr
    assert extract_repeated_french_names(sources_fr, "francês") == names_fr

