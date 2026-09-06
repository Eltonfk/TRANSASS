from __future__ import annotations

import pipeline_v2_1_3 as pipeline
from v238_semantic_style_ownership import extract_semantic_style_ownership


def _event(raw: str, event_id: int = 0) -> pipeline.Event:
    pieces, anchors, breaks = pipeline.split_ass_text(raw)
    segments = [
        pipeline.CleanSegment(index, piece, piece.strip(), bool(pipeline.SPEAKER_RE.match(piece.strip())))
        for index, piece in enumerate(pieces)
    ]
    return pipeline.Event(
        id=event_id,
        original_index=event_id,
        layer=0,
        start=0,
        end=1000,
        style="Dialogue1",
        name="",
        marginl=0,
        marginr=0,
        marginv=0,
        effect="",
        original_text=raw,
        visible_text=pipeline.TAG_RE.sub("", raw).replace(r"\N", "\n"),
        clean_text=" ".join(piece.strip() for piece in pieces),
        segments=segments,
        tag_anchors=anchors,
        line_break_boundaries=breaks,
        has_positioning=False,
    )


def test_wrapped_two_speaker_event_accepts_semantic_segments():
    event = _event(
        r"--It's clear out now, but it's going\Nto rain tonight, so take an umbrella.\N"
        r"--I don't have time to just kick\Nback and look at the night sky.",
        event_id=119,
    )
    output, flags = pipeline.reconstruct_event(
        event,
        {
            "segments": [
                {"segment_id": 0, "text": "--Agora está limpo, mas vai chover hoje à noite, então leve um guarda-chuva."},
                {"segment_id": 1, "text": "--Não tenho tempo para apenas olhar para o céu noturno."},
            ]
        },
    )
    assert flags == []
    assert output.count(r"\N") == 3
    assert "It's clear" not in output


def test_shorter_translation_keeps_collapsed_style_tag_order():
    source = r"You're late. And {\i1}you're{\i0} the one who called {\i1}us.{\i0}"
    event = _event(source)
    output, flags = pipeline.reconstruct_event(
        event,
        {"text": "Você atrasou. E foi você quem nos ligou."},
    )
    assert flags == []
    source_program, _ = extract_semantic_style_ownership(source, program_id="source", envelope_id=0)
    output_program, _ = extract_semantic_style_ownership(output, program_id="output", envelope_id=0)
    assert [
        (item["property"], item["after"])
        for item in source_program.provenance["transition_trace"]
    ] == [
        (item["property"], item["after"])
        for item in output_program.provenance["transition_trace"]
    ]


def test_unknown_gemini_id_is_repaired_only_when_order_and_cardinality_match():
    expected = {
        416: _event("first", event_id=416),
        417: _event("second", event_id=417),
    }
    found, issues = pipeline.validate_response(
        {
            "translations": [
                {"id": 416, "text": "primeiro"},
                {"id": 429, "text": "segundo"},
            ]
        },
        expected,
        allow_positional_repair=True,
    )
    assert issues == []
    assert sorted(found) == [416, 417]
    assert found[417]["text"] == "segundo"


def test_unknown_gemini_id_with_wrong_order_remains_fail_closed():
    expected = {
        416: _event("first", event_id=416),
        417: _event("second", event_id=417),
    }
    found, issues = pipeline.validate_response(
        {
            "translations": [
                {"id": 417, "text": "segundo"},
                {"id": 429, "text": "primeiro"},
            ]
        },
        expected,
    )
    assert sorted(found) == [417]
    assert 416 not in found
    assert any("ids ausentes" in issue for issue in issues)


def test_isolated_gemini_placeholder_cleanup_keeps_translation_text():
    expected = {453: _event("A line", event_id=453)}
    found, issues = pipeline.validate_response(
        {
            "translations": [
                {"id": 453, "text": r"{\\i1}Uma fala{\\i0}§T0§"},
            ]
        },
        expected,
        allow_positional_repair=True,
    )
    assert issues == []
    assert found[453]["text"] == "Uma fala"
