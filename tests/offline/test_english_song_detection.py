"""Regression tests for English content in mislabeled OP/ED subtitle tracks."""

from __future__ import annotations

from types import SimpleNamespace

import pysubs2

import pipeline_v2_1_3 as pipeline
import production_v2_3_0_adapter as karaoke


def test_short_english_song_lines_are_not_treated_as_romaji():
    assert pipeline._block_has_english_source_text([
        SimpleNamespace(clean_text="Moonlight signpost"),
    ]) is True
    assert pipeline._block_has_english_source_text([
        SimpleNamespace(clean_text="I start walking wherever my shadow leads"),
    ]) is True
    assert pipeline._block_has_english_source_text([
        SimpleNamespace(clean_text="Tsukiakari no michishirube"),
    ]) is False
    assert pipeline._block_has_english_source_text([
        SimpleNamespace(clean_text="sore de ii to omoeru tte"),
    ]) is False


def test_content_heavy_english_lyrics_veto_romaji_heuristic():
    assert pipeline.probable_romaji("I start walking wherever my shadow leads")[0] is False
    assert pipeline.probable_romaji("shine above the clouds to reach me")[0] is False
    assert pipeline.probable_romaji("Tsukiakari no michishirube")[0] is True
    assert pipeline.probable_romaji("sore de ii to omoeru tte")[0] is True


def test_wrong_style_label_still_reaches_music_classification():
    line = SimpleNamespace(
        is_comment=False,
        text=r"{\be2}Moonlight signpost",
        style="OP Bottom",
        name="",
        effect="",
    )
    classification, _, _ = pipeline.classify_event(
        line, "Moonlight signpost", {}, english_dictionary=set(), source_language="inglês"
    )
    assert classification == "MUSIC_OR_KARAOKE"


def test_v230_discovers_position_only_op_ed_translation_styles():
    assert karaoke.classify_song_translation("OP Bottom", "Moonlight signpost") == "SONG_TRANSLATION"
    assert karaoke.classify_song_translation(
        "ED Bottom", "I start walking wherever my shadow leads"
    ) == "SONG_TRANSLATION"
    assert karaoke.classify_song_translation(
        "OP Top", "Tsukiakari no michishirube"
    ) != "SONG_TRANSLATION"


def test_v230_translates_english_bottom_lines_and_preserves_ass_envelope(tmp_path):
    source = tmp_path / "episode.ass"
    output = tmp_path / "episode.pt-BR.ass"
    subs = pysubs2.SSAFile()
    subs.events = [
        pysubs2.SSAEvent(start=0, end=1000, style="OP Top", text=r"{\be1}Tsukiakari no michishirube"),
        pysubs2.SSAEvent(start=1000, end=2000, style="OP Bottom", text=r"{\be2}Moonlight signpost"),
        pysubs2.SSAEvent(start=2000, end=3000, style="ED Top", text=r"{\i1}sore de ii to omoeru tte"),
        pysubs2.SSAEvent(
            start=3000,
            end=4000,
            style="ED Bottom",
            text=r"{\be2}I start walking wherever my shadow leads",
        ),
    ]
    before = [karaoke._structural_signature(line) for line in subs]
    subs.save(str(source), encoding="utf-8")

    translations = {
        "Moonlight signpost": "Poste de luz lunar",
        "I start walking wherever my shadow leads": "Começo a caminhar por onde minha sombra me leva",
    }

    def fake_translator(text, _before, _after):
        return translations[text]

    result = karaoke.augment_karaoke_candidate_v2_3_0(
        source, output, translator=fake_translator
    )
    translated = pysubs2.load(str(output))

    assert result["song_units"] == 2
    assert result["translated_units"] == 2
    assert result["translated_events"] == 2
    assert result["ollama_calls"] == 0
    assert result["provider_calls"] == 2
    assert result["failures"] == []
    assert translated[0].text == r"{\be1}Tsukiakari no michishirube"
    assert translated[1].text == r"{\be2}Poste de luz lunar"
    assert translated[2].text == r"{\i1}sore de ii to omoeru tte"
    assert translated[3].text == r"{\be2}Começo a caminhar por onde minha sombra me leva"
    assert before == [karaoke._structural_signature(line) for line in translated]


def test_music_english_copy_is_high_confidence_untranslated_residue():
    event = SimpleNamespace(
        classification="MUSIC_OR_KARAOKE",
        clean_text="Moonlight signpost",
    )
    assert pipeline.high_confidence_untranslated_dialogue(
        event, "Moonlight signpost", "Moonlight signpost", source_language="inglês"
    ) is True
    assert pipeline.high_confidence_untranslated_dialogue(
        event, "Moonlight signpost", "Poste de luz lunar", source_language="inglês"
    ) is False
    assert pipeline.high_confidence_untranslated_dialogue(
        event, "Moonlight signpost", "Moonlight signpost", source_language="japonês"
    ) is False
