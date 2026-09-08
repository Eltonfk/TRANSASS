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


def test_v230_rejects_non_lyric_events_that_reuse_karaoke_style():
    cases = [
        pysubs2.SSAEvent(
            style="Karaoke Translation op",
            name="CAST1:....",
            text=r"Reinhard:\N\NHilda:\N\NAnnerose:",
        ),
        pysubs2.SSAEvent(
            style="Karaoke Translation op",
            name="NARRATOR:..",
            text="The End of the Dream",
        ),
        pysubs2.SSAEvent(
            style="Karaoke Translation op",
            name="D",
            text=r"Translation:\N{\c&H0000FF&}Toshiaki",
        ),
    ]
    comment = pysubs2.SSAEvent(
        style="Karaoke Translation op",
        name="Comment",
        text="0:26:09.81 0:26:20.49 VP350CF1",
    )
    comment.is_comment = True
    cases.append(comment)

    assert all(karaoke.classify_song_event(event) == "SONG_NON_LYRIC" for event in cases)


def test_v230_accepts_song_metadata_even_for_lexically_difficult_verse():
    event = pysubs2.SSAEvent(
        style="Karaoke Translation op",
        name="SONG:3....",
        text="Surely, this life, given birth unasked...",
    )

    assert karaoke.classify_song_event(event) == "SONG_TRANSLATION"


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


def test_v230_rebuilds_source_linebreaks_when_model_returns_one_line():
    source = r"{\an8}First lyric\N{\an8}second lyric"
    rendered = karaoke._replace_payload(source, "Primeira letra segunda letra")

    assert rendered == r"{\an8}Primeira letra\N{\an8}segunda letra"
    assert rendered.count(r"\N") == source.count(r"\N")


def test_v230_retries_source_copy_on_the_selected_provider(tmp_path):
    source = tmp_path / "episode.ass"
    output = tmp_path / "episode.pt-BR.ass"
    subs = pysubs2.SSAFile()
    subs.events = [pysubs2.SSAEvent(style="OP Bottom", text=r"{\be2}Moonlight signpost")]
    subs.save(str(source), encoding="utf-8")

    attempts = []

    def fake_translator(text, _before, _after):
        attempts.append(("initial", text))
        return text

    def retry_translator(text, _before, _after):
        attempts.append(("retry", text))
        return "Poste de luz lunar"

    fake_translator.retry = retry_translator
    result = karaoke.augment_karaoke_candidate_v2_3_0(
        source, output, translator=fake_translator
    )
    translated = pysubs2.load(str(output))

    assert attempts == [
        ("initial", "Moonlight signpost"),
        ("retry", "Moonlight signpost"),
    ]
    assert result["provider_calls"] == 2
    assert result["translated_units"] == 1
    assert result["failures"] == []
    assert translated[0].text == r"{\be2}Poste de luz lunar"


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


def test_karaoke_can_use_two_distinct_recovery_attempts_for_qwen_echo(tmp_path):
    source = tmp_path / "episode.ass"
    output = tmp_path / "episode.pt-BR.ass"
    subs = pysubs2.SSAFile()
    subs.events = [pysubs2.SSAEvent(style="OP Bottom", text=r"{\be2}Moonlight signpost")]
    subs.save(str(source), encoding="utf-8")
    attempts = []

    def fake_translator(text, _before, _after):
        attempts.append("initial")
        return text

    def retry_translator(text, _before, _after):
        attempts.append("retry")
        return text if len(attempts) == 2 else "Poste de luz lunar"

    fake_translator.retry = retry_translator
    fake_translator.karaoke_retry_limit = 2
    result = karaoke.augment_karaoke_candidate_v2_3_0(source, output, translator=fake_translator)

    assert attempts == ["initial", "retry", "retry"]
    assert result["provider_calls"] == 3
    assert result["translated_units"] == 1
    assert result["failures"] == []


def test_v230_does_not_retranslate_song_already_converted_by_v238(tmp_path):
    original = tmp_path / "episode.original.ass"
    intermediate = tmp_path / "episode.v238.ass"
    output = tmp_path / "episode.pt-BR.ass"

    original_subs = pysubs2.SSAFile()
    original_subs.events = [
        pysubs2.SSAEvent(style="OP English", text=r"{\be2}Moonlight signpost"),
    ]
    original_subs.save(str(original), encoding="utf-8")

    candidate_subs = pysubs2.SSAFile()
    candidate_subs.events = [
        pysubs2.SSAEvent(style="OP English", text=r"{\be2}Poste de luz lunar"),
    ]
    candidate_subs.save(str(intermediate), encoding="utf-8")

    def unexpected_translator(*_args):
        raise AssertionError("a linha já traduzida não deve voltar ao provedor")

    result = karaoke.augment_karaoke_candidate_v2_3_0(
        intermediate,
        output,
        translator=unexpected_translator,
        original_source_path=original,
    )
    translated = pysubs2.load(str(output))

    assert result["song_units"] == 1
    assert result["translated_units"] == 1
    assert result["translated_events"] == 1
    assert result["already_translated_units"] == 1
    assert result["already_translated_events"] == 1
    assert result["provider_calls"] == 0
    assert result["failures"] == []
    assert translated[0].text == r"{\be2}Poste de luz lunar"


def test_v230_calls_provider_only_for_song_events_still_in_english(tmp_path):
    original = tmp_path / "episode.original.ass"
    intermediate = tmp_path / "episode.v238.ass"
    output = tmp_path / "episode.pt-BR.ass"

    original_subs = pysubs2.SSAFile()
    original_subs.events = [
        pysubs2.SSAEvent(style="OP English", text=r"{\be2}Moonlight signpost"),
        pysubs2.SSAEvent(
            style="ED Bottom",
            text=r"{\be2}I start walking wherever my shadow leads",
        ),
    ]
    original_subs.save(str(original), encoding="utf-8")

    candidate_subs = pysubs2.SSAFile()
    candidate_subs.events = [
        pysubs2.SSAEvent(style="OP English", text=r"{\be2}Poste de luz lunar"),
        pysubs2.SSAEvent(
            style="ED Bottom",
            text=r"{\be2}I start walking wherever my shadow leads",
        ),
    ]
    candidate_subs.save(str(intermediate), encoding="utf-8")
    calls = []

    def fake_translator(text, _before, _after):
        calls.append(text)
        return "Começo a caminhar por onde minha sombra me leva"

    result = karaoke.augment_karaoke_candidate_v2_3_0(
        intermediate,
        output,
        translator=fake_translator,
        original_source_path=original,
    )
    translated = pysubs2.load(str(output))

    assert calls == ["I start walking wherever my shadow leads"]
    assert result["song_units"] == 2
    assert result["translated_units"] == 2
    assert result["translated_events"] == 2
    assert result["already_translated_units"] == 1
    assert result["already_translated_events"] == 1
    assert result["provider_calls"] == 1
    assert result["failures"] == []
    assert translated[0].text == r"{\be2}Poste de luz lunar"
    assert translated[1].text == r"{\be2}Começo a caminhar por onde minha sombra me leva"


def test_v230_accepts_pretranslated_syllabic_event_without_rewriting_tags(tmp_path):
    original = tmp_path / "episode.original.ass"
    intermediate = tmp_path / "episode.v238.ass"
    output = tmp_path / "episode.pt-BR.ass"

    original_subs = pysubs2.SSAFile()
    original_subs.events = [
        pysubs2.SSAEvent(style="OP English", text=r"{\k20}Moonlight {\k30}signpost"),
    ]
    original_subs.save(str(original), encoding="utf-8")
    candidate_subs = pysubs2.SSAFile()
    candidate_subs.events = [
        pysubs2.SSAEvent(style="OP English", text=r"{\k20}Poste {\k30}lunar"),
    ]
    candidate_subs.save(str(intermediate), encoding="utf-8")

    result = karaoke.augment_karaoke_candidate_v2_3_0(
        intermediate,
        output,
        translator=lambda *_args: (_ for _ in ()).throw(AssertionError("sem chamada")),
        original_source_path=original,
    )

    assert result["unsupported"] == 0
    assert result["translated_units"] == 1
    assert result["already_translated_units"] == 1
    assert pysubs2.load(str(output))[0].text == r"{\k20}Poste {\k30}lunar"
