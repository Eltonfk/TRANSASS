"""Offline tests for source-language residue detection feeding retries.

Closes the gap where untranslated French residue never triggered retries:
the historical detectors only measured ENGLISH lexical evidence, so French
output landed in audit-only flags (SHORT_ENGLISH_POSSIBLE) while the bounded
retry path required SHORT_ENGLISH_HIGH_CONFIDENCE (English hits).

Design under test: precision-first marker tables (every marker is guaranteed
NOT to be a valid pt-BR word) + elision/ne..pas patterns + French-only
diacritics. English sources keep their dedicated detectors untouched.

No ffprobe/ffmpeg, no model calls, no Library writes.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "subtranslate"))

import pipeline_v2_1_3 as engine  # noqa: E402
from production_v2_2_3_adapter import (  # noqa: E402
    NOT_SHORT_ENGLISH,
    SHORT_ENGLISH_HIGH_CONFIDENCE,
    SHORT_ENGLISH_POSSIBLE,
    classify_short_english_fragment,
)


def _event(clean_text: str, classification: str = "MAIN_DIALOGUE") -> SimpleNamespace:
    return SimpleNamespace(
        id=1, classification=classification,
        clean_text=clean_text, original_text=clean_text,
    )


# ---------------------------------------------------------------------------
# Evidence function
# ---------------------------------------------------------------------------


def test_evidence_french_sentence_is_strong():
    ev = engine.source_residue_evidence("J'assure que je ne voulais pas de ça.", "francês")
    assert ev["language"] == "french"
    assert "je" in ev["word_hits"]
    assert ev["pattern_hits"] >= 2  # elisão j' + ne..pas
    assert engine.source_residue_strong(ev)


def test_evidence_identical_copy_strong_via_overlap():
    text = "Le garçon est sur la plaque!"
    ev = engine.source_residue_evidence(text, "francês")
    assert "est" in ev["word_hits"]
    # cópia idêntica => overlap 1.0 => um único marcador basta
    assert engine.source_residue_strong(ev, overlap=1.0)


def test_evidence_clean_portuguese_has_no_hits():
    for text in ("O menino está na placa!", "Você mente! Você inventou tudo!",
                 "A verdade vai aparecer!", "Não durmo mais."):
        ev = engine.source_residue_evidence(text, "francês")
        assert ev["count"] == 0, f"falso positivo em {text!r}: {ev}"


def test_evidence_disabled_for_english_and_unknown():
    text = "J'assure que je ne voulais pas de ça."
    assert engine.source_residue_evidence(text, "inglês")["count"] == 0
    assert engine.source_residue_evidence(text, None)["count"] == 0
    assert engine.source_residue_evidence(text, "alemão")["count"] == 0


def test_french_dialogue_is_not_classified_as_romaji():
    line = SimpleNamespace(text="Je ne trouve pas.", style="Default", name="", effect="")
    classification, reason, _confidence = engine.classify_event(
        line,
        "Je ne trouve pas.",
        {"style_hypotheses": {}},
        set(),
        source_language="francês",
    )
    assert classification == "MAIN_DIALOGUE", reason


# ---------------------------------------------------------------------------
# Short-fragment classifier feeds the retry path
# ---------------------------------------------------------------------------


def test_classify_french_copy_is_high_confidence_retry_eligible():
    source = "J'assure que je ne voulais pas de ça."
    assessment = classify_short_english_fragment(
        _event(source), source, {}, None, source_language="francês")
    assert assessment["status"] == SHORT_ENGLISH_HIGH_CONFIDENCE
    assert assessment["retry_eligible"] is True
    assert any(str(e).startswith("source_residue:french") for e in assessment["evidence"])


def test_classify_same_input_with_english_source_keeps_old_behavior():
    """Regression: with the default English source the detector stays off."""
    source = "J'assure que je ne voulais pas de ça."
    assessment = classify_short_english_fragment(
        _event(source), source, {}, None, source_language="inglês")
    assert assessment["status"] != SHORT_ENGLISH_HIGH_CONFIDENCE
    assert assessment["retry_eligible"] is False


def test_classify_legit_portuguese_translation_not_flagged():
    source = "Je ne dormais plus."
    output = "Eu não dormia mais."
    assessment = classify_short_english_fragment(
        _event(source), output, {}, None, source_language="francês")
    assert assessment["status"] in {NOT_SHORT_ENGLISH, SHORT_ENGLISH_POSSIBLE}
    assert assessment["retry_eligible"] is False


def test_classify_pt_br_sports_loan_phrase_not_high_confidence_residue():
    assessment = classify_short_english_fragment(
        _event("- Un home run !"),
        "- Um home run !",
        {}, None, source_language="francês",
    )
    assert assessment["status"] != SHORT_ENGLISH_HIGH_CONFIDENCE
    assert assessment["retry_eligible"] is False
    assert "accepted_pt_br_loan:home run" in assessment["evidence"]


def test_classify_standalone_run_remains_detectable():
    assessment = classify_short_english_fragment(
        _event("Run!"), "Run!", {}, None, source_language="inglês",
    )
    assert assessment["status"] == SHORT_ENGLISH_HIGH_CONFIDENCE
    assert assessment["retry_eligible"] is True


# ---------------------------------------------------------------------------
# Long-dialogue detector
# ---------------------------------------------------------------------------


def test_long_french_residue_dialogue_detected():
    source = "Je ne voulais pas de ça, tu sais, personne ne voulait vraiment."
    output = "J'assure que je ne voulais pas de ça, tu sais, personne ne voulait."
    assert engine.high_confidence_untranslated_dialogue(
        _event(source), source, output, None, None, source_language="francês") is True


def test_long_clean_translation_not_detected():
    source = "Je ne voulais pas de ça, tu sais, personne ne voulait vraiment."
    output = "Eu não queria isso, sabia? Ninguém realmente queria."
    assert engine.high_confidence_untranslated_dialogue(
        _event(source), source, output, None, None, source_language="francês") is False


def test_long_detector_english_source_unchanged():
    """Regression: English path must behave exactly as before."""
    source = "Switching to search mode right now."
    output = "Switching to search mode right now."
    assert engine.high_confidence_untranslated_dialogue(
        _event(source), source, output, None, None, source_language="inglês") is True


# ---------------------------------------------------------------------------
# _block_has_french_text — detecção de francês para classificação de blocos
# ---------------------------------------------------------------------------

def _block_event(clean_text: str, idx: int = 0) -> SimpleNamespace:
    """Cria evento com atributos necessários para _block_has_french_text."""
    return SimpleNamespace(
        id=idx, clean_text=clean_text, original_text=clean_text,
        classification="MAIN_DIALOGUE",
    )


def test_french_block_detected():
    """Bloco com texto em francês (noticiário) deve ser detectado."""
    events = [
        _block_event("La tempête a déjà rejoint l'île d'Izu"),
        _block_event("Des rafales de 130 km par heure sont signalées"),
        _block_event("L'agence météorologique a déclenché l'alerte"),
        _block_event("Les zones côtières sont en alerte maximale"),
    ]
    assert engine._block_has_french_text(events) is True


def test_english_block_not_detected():
    """Bloco com texto em inglês NÃO deve ser detectado como francês."""
    events = [
        _block_event("The storm has already reached Izu island"),
        _block_event("Gusts of 130 km per hour are reported"),
        _block_event("The weather agency has triggered the alert"),
        _block_event("Coastal zones are on maximum alert"),
    ]
    assert engine._block_has_french_text(events) is False


def test_portuguese_block_not_detected():
    """Bloco com texto em português NÃO deve ser detectado como francês."""
    events = [
        _block_event("A tempestade já atingiu a ilha de Izu"),
        _block_event("Rajadas de 130 km por hora são relatadas"),
        _block_event("A agência meteorológica acionou o alerta"),
        _block_event("As zonas costeiras estão em alerta máximo"),
    ]
    assert engine._block_has_french_text(events) is False


def test_french_threshold_not_met():
    """Bloco com poucos indicadores franceses não deve ser detectado."""
    events = [
        _block_event("Bem, eu acho que sim"),
        _block_event("Talvez seja melhor assim"),
    ]
    assert engine._block_has_french_text(events) is False
