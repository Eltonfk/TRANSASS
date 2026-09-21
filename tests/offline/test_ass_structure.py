from __future__ import annotations

import pysubs2

import ass_structure
import pipeline_v2_1_3 as pipeline
from v238_source_payload import rc4_replace_source_payload


def test_ass_controls_are_split_and_normalized_without_losing_metadata():
    raw = r"{\i1}One\hthing\NTwo\nThree"

    pieces, anchors, boundaries, breaks, hard_spaces = ass_structure.split_text_with_controls(raw)

    assert pieces == ["One thing", "Two", "Three"]
    assert anchors == [{"position": 0, "tag": r"{\i1}"}]
    assert boundaries == [0, 1]
    assert breaks == [r"\N", r"\n"]
    assert hard_spaces == [3]
    assert ass_structure.visible_text(raw, line_break=" ") == "One thing Two Three"


def test_drawing_mode_is_technical_but_prose_with_p_letter_is_not():
    assert ass_structure.is_drawing_event(r"{\p1}m 0 0 l 100 100")
    assert not ass_structure.is_drawing_event(r"{\p1}move this title")


def test_hard_space_is_restored_at_projected_target_boundary():
    assert ass_structure.restore_hard_spaces(r"Hello\hworld", "Olá mundo") == r"Olá\hmundo"
    assert ass_structure.restore_hard_spaces(r"{\fnFont Name}Hello\hworld", r"{\fnFont Name}Olá mundo") == r"{\fnFont Name}Olá\hmundo"


def test_source_payload_preserves_spaces_owned_by_inline_style_chunks():
    source = r"{\blur1\c&HCB91E0&\pos(497.252,168.333)}How {\c&HE8A070&}Beetles {\c&HCB91E0&}Transform"
    target = "Como os Besouros se Transformam"

    rendered = ass_structure.replace_source_payload(source, target)

    assert rendered == r"{\blur1\c&HCB91E0&\pos(497.252,168.333)}Como os {\c&HE8A070&}Besouros {\c&HCB91E0&}se Transformam"
    assert ass_structure.inline_tag_counts(rendered) == ass_structure.inline_tag_counts(source)


def test_source_payload_handles_per_character_tags_and_nested_transform_envelopes():
    source = r"{\blur2\t(0,100,1,\fsp0\blur0.4)}R{\fs75}OAD{\fs} TO H{\fs75}ERO"
    target = "ESTRADA PARA UM HERÓI"

    rendered = ass_structure.replace_source_payload(source, target)

    assert rendered.startswith(r"{\blur2\t(0,100,1,\fsp0\blur0.4)}")
    assert r"{\fs75}" in rendered
    assert r"{\fs}" in rendered
    assert ass_structure.inline_tag_counts(rendered) == ass_structure.inline_tag_counts(source)
    assert ass_structure.visible_text(rendered, line_break=" ") == target


def test_source_payload_handles_legacy_karaoke_blank_lines_and_punctuation():
    # The E110 track uses consecutive \N controls and two adjacent override
    # blocks on its song lines.  The source also places the question mark in a
    # separate whitespace slot (``life ?``); it must not be duplicated when
    # the Portuguese result supplies ``vida?``.
    source = r"{\a6\pos(316,379)}{\c&HFFFFFF&\3c&H000000&}The solitude you carry on your\N\N\Nback, does it end with your life ?"
    target = "A solidão que você carrega nas costas, isso termina com a sua vida?"

    rendered = ass_structure.replace_source_payload(source, target)

    assert rendered == r"{\a6\pos(316,379)}{\c&HFFFFFF&\3c&H000000&}A solidão que você carrega nas\N\N\Ncostas, isso termina com a sua vida?"
    assert ass_structure.break_tokens(rendered) == [r"\N", r"\N", r"\N"]
    assert ass_structure.inline_tag_counts(rendered) == ass_structure.inline_tag_counts(source)
    assert ass_structure.visible_text(rendered, line_break=" ") == "A solidão que você carrega nas   costas, isso termina com a sua vida?"


def test_source_payload_replaces_source_punctuation_without_losing_inline_tags():
    source = r"What {\c&HFFFFFF&}now ?"

    rendered = ass_structure.replace_source_payload(source, "O quê agora?")

    assert rendered == r"O quê {\c&HFFFFFF&}agora?"
    assert ass_structure.inline_tag_counts(rendered) == ass_structure.inline_tag_counts(source)


def test_source_payload_preserves_original_soft_break_token():
    assert rc4_replace_source_payload(r"One\nTwo", "Um dois") == r"Um\ndois"
    assert rc4_replace_source_payload(r"One\nTwo", r"Um\nDois") == r"Um\nDois"


def test_pipeline_preserves_soft_break_and_hard_space_during_reconstruction(tmp_path):
    subs = pysubs2.SSAFile()
    subs.events.append(pysubs2.SSAEvent(text=r"One\hthing\nTwo"))
    source = tmp_path / "source.ass"
    subs.save(str(source))

    _loaded, events, _profile = pipeline.load_events(source, {})
    output, flags = pipeline.reconstruct_event(events[0], {"text": "Uma coisa dois"})

    assert flags == []
    assert ass_structure.break_tokens(output) == [r"\n"]
    assert ass_structure.hard_space_count(output) == 1
    assert ass_structure.visible_text(output, line_break=" ") == "Uma coisa dois"


def test_pipeline_rejects_all_ass_text_controls_from_model_output():
    pieces, anchors, breaks = pipeline.split_ass_text("Hello")
    event = pipeline.Event(
        id=0, original_index=0, layer=0, start=0, end=1000, style="Default",
        name="", marginl=0, marginr=0, marginv=0, effect="",
        original_text="Hello", visible_text="Hello", clean_text="Hello",
        segments=[pipeline.CleanSegment(0, "Hello", "Hello")],
        tag_anchors=anchors, line_break_boundaries=breaks, has_positioning=False,
    )
    assert pieces == ["Hello"]
    for control in (r"\N", r"\n", r"\h"):
        _output, flags = pipeline.reconstruct_event(event, {"text": "Olá" + control})
        assert flags == ["MODEL_EMITTED_STRUCTURAL_TOKEN"]


def test_karaoke_detector_includes_relative_timing_tag():
    assert pipeline.KARAOKE_RE.search(r"{\kt30}word")
