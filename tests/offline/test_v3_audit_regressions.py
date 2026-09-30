"""Deterministic regressions for audit findings C, D, E and F; no real models."""

import copy
import json
from types import SimpleNamespace

import pysubs2
import pytest

from ass_engine import ASSDocumentAST, ASSEventNode, validate_document_structure, validate_inline_tags
from effects_engine import InMemoryEffectsEngine
from pipeline_v3 import (
    PipelineV3Error,
    _is_untranslated_source_copy,
    make_v3_transport_call,
    translate_subtitle_file_v3,
)
from semantic_orchestrator import InMemorySemanticOrchestrator, build_sign_groups
from translation_quality import remove_unowned_terminal_ascii_quote_closures, translation_quality_flags


@pytest.mark.parametrize("text", [
    r"{\i1}Hello {\i0}world", r"{\c&HFF0000&}A\N{\c&H00FF00&}B",
    "{opaque}Olá{\\i1}e\u0301{\\i0}", r"A\{\i1}N{\i0}B", r"\{\i1}{\i0}hB",
])
def test_linear_anchor_scan_matches_prefix_reference(text):
    from ass_engine import TAG_RE, inline_tag_anchor_signature, visible_text
    expected = tuple((m.group(0), len(visible_text(text[:m.start()]))) for m in TAG_RE.finditer(text))
    assert inline_tag_anchor_signature(text) == expected


def test_dense_tag_anchor_scan_does_not_rescan_growing_prefixes(monkeypatch):
    import ass_engine
    source = (r"{\c&HFF0000&}A" * 2000)
    visited = []
    visible = ass_engine.visible_text
    def track(text, **kwargs):
        visited.append(len(text))
        return visible(text, **kwargs)
    monkeypatch.setattr(ass_engine, "visible_text", track)
    anchors = ass_engine.inline_tag_anchor_signature(source)
    assert len(anchors) == 2000 and anchors[-1][1] == 1999
    assert sum(visited) <= len(source)


def _document(*texts, style="Default"):
    ssa = pysubs2.SSAFile()
    ssa.styles.setdefault(style, pysubs2.SSAStyle())
    ssa.events = [
        pysubs2.SSAEvent(start=1000, end=3000, text=text, style=style)
        for text in texts
    ]
    return ASSDocumentAST(ssa)


class _Provider:
    mode = "TEST_FAKE"
    operation_budget = None

    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def respond(self, payload, *, capture_id):
        self.calls.append(payload)
        return {"translation": next(self.responses)}


def _response(*texts):
    return json.dumps({"translations": [
        {"id": str(index), "translation": text}
        for index, text in enumerate(texts)
    ]}, ensure_ascii=False)


@pytest.fixture(autouse=True)
def _forbid_real_http(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Real HTTP is forbidden in audit regressions")
    monkeypatch.setattr("web_durable_provider._http_post", forbidden)


@pytest.mark.parametrize("source,target", [
    ("(Texte)", ")Texto("),
    ('mon ami"', '"meu amigo'),
    ('mon ami"', 'meu amigo'),
    ('"Bonjour', 'Olá"'),
    ('Il dit "bonjour".', '"Ele diz olá".'),
])
def test_invalid_delimiters_block_direct_and_failed_repair(tmp_path, source, target):
    input_path = tmp_path / "source.ass"
    output_path = tmp_path / "candidate.ass"
    _document(source).save(input_path)

    with pytest.raises(PipelineV3Error, match="ASS_DELIMITER"):
        translate_subtitle_file_v3(
            input_path, output_path, source_language="francês",
            transport_call=lambda payload: {str(item["id"]): target for item in payload},
        )
    assert not output_path.exists()

    provider = _Provider([_response(target), _response(target)])
    caller = make_v3_transport_call(
        SimpleNamespace(model="offline-fake", name="offline-fake"),
        response_provider=provider, source_language="francês",
    )
    with pytest.raises(PipelineV3Error, match="ASS_DELIMITER.*REPAIR_FAILED"):
        translate_subtitle_file_v3(input_path, output_path, transport_call=caller, source_language="francês")
    assert len(provider.calls) == 2
    assert not output_path.exists()


def test_fallback_cannot_lose_a_source_owned_closing_quote(tmp_path):
    input_path, output_path = tmp_path / "source.ass", tmp_path / "candidate.ass"
    _document('mon ami"').save(input_path)
    provider = _Provider(['{"translations": []}', 'meu amigo"', _response('meu amigo')])
    caller = make_v3_transport_call(
        SimpleNamespace(model="offline-fake", name="offline-fake"),
        response_provider=provider, source_language="francês",
    )
    # A correct fallback needs no quote repair; its source-owned closure stays.
    result = translate_subtitle_file_v3(input_path, output_path, transport_call=caller, source_language="francês")
    assert result["status"] == "COMPLETED"
    assert ASSDocumentAST.from_file(output_path)[0].text == 'meu amigo"'
    assert len(provider.calls) == 2


@pytest.mark.parametrize("source,target", [
    ('mon ami"', '"meu amigo"'),
    ('dit "bonjour', 'diz "olá"'),
])
def test_quote_cleanup_never_confuses_closing_with_opening_ownership(source, target):
    corrected = remove_unowned_terminal_ascii_quote_closures(source, target)
    if source == 'mon ami"':
        assert corrected == target
        assert translation_quality_flags(source, corrected, source_language="francês", target_language="pt-BR")
    else:
        assert corrected == 'diz "olá'


@pytest.mark.parametrize("targets,valid", [
    (('"Olá', 'meu amigo"'), True),
    (('"Olá', '"meu amigo'), False),
    (('Olá"', 'meu amigo"'), False),
])
def test_cross_event_quote_roles_are_owned_by_each_source_event(tmp_path, targets, valid):
    input_path, output_path = tmp_path / "source.ass", tmp_path / "candidate.ass"
    _document('"Bonjour', 'mon ami"').save(input_path)
    translations = {str(index): text for index, text in enumerate(targets)}
    if valid:
        result = translate_subtitle_file_v3(
            input_path, output_path, source_language="francês", transport_call=lambda payload: translations,
        )
        assert result["serialized_validation"]["valid"]
        assert [node.text for node in ASSDocumentAST.from_file(output_path).events] == list(targets)
    else:
        with pytest.raises(PipelineV3Error, match="ASS_DELIMITER"):
            translate_subtitle_file_v3(
                input_path, output_path, source_language="francês", transport_call=lambda payload: translations,
            )
        assert not output_path.exists()


@pytest.mark.parametrize("source", ["Stop Laughing!", "Kill Him!", "Keep Walking!", "Open The Door!"])
def test_titlecase_dialogue_copy_requires_translation(tmp_path, source):
    assert _is_untranslated_source_copy(source, source, "inglês")
    input_path, output_path = tmp_path / "source.ass", tmp_path / "candidate.ass"
    _document(source).save(input_path)
    provider = _Provider([_response(source), _response(source)])
    caller = make_v3_transport_call(
        SimpleNamespace(model="offline-fake", name="offline-fake"), response_provider=provider,
    )
    with pytest.raises(PipelineV3Error, match="V3_TRANSLATION_SOURCE_COPY.*REPAIR_FAILED"):
        translate_subtitle_file_v3(input_path, output_path, transport_call=caller)
    assert len(provider.calls) == 2
    assert not output_path.exists()


def test_protected_title_and_name_identity_remain_valid():
    for text, names in [
        ("The Legend of the Galactic Heroes", ("The Legend of the Galactic Heroes",)),
        ("He Yu.", ("He Yu",)),
    ]:
        assert not _is_untranslated_source_copy(text, text, "inglês", protected_names=names)
        assert _is_untranslated_source_copy(text, text, "inglês")


def test_catalog_backed_title_survives_but_titlecase_dialogue_does_not(tmp_path):
    source, output = tmp_path / "source.ass", tmp_path / "candidate.ass"
    _document("[Full-Time Magister 2]", "Stop Laughing!").save(source)
    result = translate_subtitle_file_v3(
        source, output, protected_names=("Full-Time Magister",),
        transport_call=lambda payload: {"0": "[Full-Time Magister 2]", "1": "Pare de rir!"},
    )
    assert result["serialized_validation"]["valid"]
    assert ASSDocumentAST.from_file(output)[0].visible == "[Full-Time Magister 2]"
    output = tmp_path / "copied.ass"
    with pytest.raises(PipelineV3Error, match="SOURCE_COPY"):
        translate_subtitle_file_v3(source, output, protected_names=("Full-Time Magister",),
            transport_call=lambda payload: {"0": "[Full-Time Magister 2]", "1": "Stop Laughing!"})
    assert not output.exists()


def test_source_evidenced_repeated_name_is_not_capitalization_only(tmp_path):
    source, output = tmp_path / "source.ass", tmp_path / "candidate.ass"
    _document("Your friend Mo Fan is here.", "Mo Fan.", "Stop Laughing!", "Stop Laughing!").save(source)
    targets = {"0": "Seu amigo Mo Fan está aqui.", "1": "Mo Fan.", "2": "Pare de rir!", "3": "Pare de rir!"}
    result = translate_subtitle_file_v3(source, output, transport_call=lambda payload: targets)
    assert result["serialized_validation"]["valid"]
    assert ASSDocumentAST.from_file(output)[1].visible == "Mo Fan."
    targets["2"] = "Stop Laughing!"
    with pytest.raises(PipelineV3Error, match="SOURCE_COPY"):
        translate_subtitle_file_v3(source, tmp_path / "copied.ass", transport_call=lambda payload: targets)


@pytest.mark.parametrize("left,right", [
    ("Tu viens ?", "Tu viens !"), ("Wait...", "Wait!"),
    ("Don't go", "Dont go"), ('"Help"', "Help"),
    ("A-B", "A B"), ("A, B", "A B"),
    (r"A\NB", r"A\nB"), (r"A\NB", r"AB\N"),
])
def test_sign_groups_keep_punctuation_and_break_ownership(left, right):
    groups = build_sign_groups([
        ASSEventNode(index=0, start=1000, end=3000, style="Sign", text=left),
        ASSEventNode(index=1, start=1000, end=3000, style="Sign", text=right),
    ])
    assert len(groups) == 2


def test_equivalent_signs_with_different_visual_tags_still_share_translation():
    doc = _document(r"{\an8}Tu viens ?", r"{\an8\fs30}Tu viens ?", style="Sign")
    batches, groups = InMemorySemanticOrchestrator().plan_batches(doc)
    assert len(groups) == 1
    assert sum(len(batch.units) for batch in batches) == 1


def test_forged_or_stale_sign_membership_is_rejected_before_mutation():
    doc = _document("Tu viens ?", "Tu viens !", style="Sign")
    orchestrator = InMemorySemanticOrchestrator()
    _, groups = orchestrator.plan_batches(doc)
    groups[0]["member_indices"] = [0, 1]
    before = [node.text for node in doc.events]
    with pytest.raises(ValueError, match="SIGN_GROUP"):
        orchestrator.apply_translations(doc, {groups[0]["group_id"]: "Você vem?"}, groups)
    assert [node.text for node in doc.events] == before


@pytest.mark.parametrize("target", [
    r"{\i0}Olá {\i1}mundo", r"{\i1}{\i0}Olá mundo",
    r"Olá {\i1}mundo{\i0}",
])
@pytest.mark.parametrize("reflow", [False, True])
def test_tag_order_and_scope_cannot_be_lost_even_on_signs(target, reflow):
    source = r"{\i1}Hello {\i0}world"
    assert validate_inline_tags(source, target)
    result = validate_document_structure(
        _document(source), _document(target), allow_tag_reflow_indices={0} if reflow else None,
    )
    assert not result["valid"]


def test_structural_validation_also_checks_delimiters():
    result = validate_document_structure(_document('mon ami"'), _document('"meu amigo'))
    assert not result["valid"]
    assert any("ASS_DELIMITER" in issue for issue in result["issues"])


@pytest.mark.parametrize("mutation_stage", ["effects", "serialization"])
def test_final_delimiter_checks_cannot_be_bypassed(tmp_path, monkeypatch, mutation_stage):
    input_path, output_path = tmp_path / "source.ass", tmp_path / "candidate.ass"
    _document('mon ami"').save(input_path)
    if mutation_stage == "effects":
        process = InMemoryEffectsEngine.process_effects
        def corrupt(engine, doc, original):
            stats = process(engine, doc, original)
            doc[0].text = '"meu amigo'
            return stats
        monkeypatch.setattr(InMemoryEffectsEngine, "process_effects", corrupt)
    else:
        save = ASSDocumentAST.save
        def corrupt(doc, path):
            doc[0].text = '"meu amigo'
            save(doc, path)
        monkeypatch.setattr(ASSDocumentAST, "save", corrupt)
    with pytest.raises(PipelineV3Error, match="ASS_DELIMITER_OWNERSHIP_MISMATCH"):
        translate_subtitle_file_v3(
            input_path, output_path, source_language="francês",
            transport_call=lambda payload: {"0": 'meu amigo"'},
        )
    assert not output_path.exists()


def test_even_canonical_tag_allocation_must_not_collapse_a_styled_interval():
    from ass_structure import replace_source_payload
    source = r"{\i1}Hello {\i0}world"
    collapsed = replace_source_payload(source, "Sim")
    assert "ASS_INLINE_TAG_ANCHOR_FAILURE" in validate_inline_tags(source, collapsed)


def test_gradient_preserves_inline_italic_scope_and_validates_reflow():
    source = _document(r"{\c&HFF0000&\i1}O{\c&H00FF00&\i0}K", style="Sign")
    target = _document("Sim", style="Sign")
    stats = InMemoryEffectsEngine().process_effects(target, source)
    assert stats["visual_glyphs_applied"] == 1
    assert target[0].text.index(r"\i1") < target[0].text.index("S")
    assert target[0].text.index("S") < target[0].text.index(r"\i0") < target[0].text.index("m")
    assert validate_document_structure(source, target, allow_tag_reflow_indices={0})["valid"]
    corrupt = copy.deepcopy(target)
    corrupt[0].text = r"{\i1}{\i0}{\c&HFF0000&}S{\c&HFF0000&}i{\c&H00FF00&}m"
    assert not validate_document_structure(source, corrupt, allow_tag_reflow_indices={0})["valid"]


def test_gradient_reconstruction_failure_is_not_silently_approved(monkeypatch):
    source = _document(r"{\c&HFF0000&}O{\c&H00FF00&}K", style="Sign")
    target = _document("Sim", style="Sign")
    def broken(*args, **kwargs):
        raise ValueError("synthetic anchor failure")
    monkeypatch.setattr("effects_engine.apply_glyph_color_gradient", broken)
    with pytest.raises(ValueError, match="ASS_INLINE_TAG_ANCHOR_FAILURE"):
        InMemoryEffectsEngine().process_effects(target, source)
    assert target[0].text == "Sim"


@pytest.mark.parametrize("source,target", [
    (r"{\c&HFF0000&\i1}O{\c&H00FF00&\i0}K", "S"),
    (r"{\c&HFF0000&\t(0,100,\c&H0000FF&)}O{\c&H00FF00&}K", "Sim"),
    (r"{\c&HFF0000&\rdefault}O{\c&H00FF00&}K", "Sim"),
])
def test_gradient_with_ambiguous_or_collapsed_scope_blocks_output(tmp_path, source, target):
    input_path, output_path = tmp_path / "source.ass", tmp_path / "candidate.ass"
    _document(source, style="Sign").save(input_path)
    with pytest.raises(PipelineV3Error, match="ASS_INLINE_TAG_ANCHOR_FAILURE"):
        translate_subtitle_file_v3(
            input_path, output_path,
            transport_call=lambda payload: {str(item["id"]): target for item in payload},
        )
    assert not output_path.exists()


def test_valid_gradient_round_trip_preserves_inline_scope(tmp_path):
    input_path, output_path = tmp_path / "source.ass", tmp_path / "candidate.ass"
    _document(r"{\c&HFF0000&\i1}O{\c&H00FF00&\i0}K", style="Sign").save(input_path)
    result = translate_subtitle_file_v3(
        input_path, output_path,
        transport_call=lambda payload: {str(item["id"]): "Sim" for item in payload},
    )
    assert result["validation"]["valid"] and result["serialized_validation"]["valid"]
    text = ASSDocumentAST.from_file(output_path)[0].text
    assert text.index("S") < text.index(r"\i0") < text.index("m")


def test_distinct_sign_intentions_receive_distinct_translations(tmp_path):
    input_path, output_path = tmp_path / "source.ass", tmp_path / "candidate.ass"
    _document("Tu viens ?", "Tu viens !", style="Sign").save(input_path)
    seen = []
    def translate(payload):
        seen.extend(payload)
        return {str(item["id"]): "Você vem?" if "?" in item["source_text"] else "Você vem!" for item in payload}
    translate_subtitle_file_v3(input_path, output_path, transport_call=translate, source_language="francês")
    assert len(seen) == 2
    assert [node.visible for node in ASSDocumentAST.from_file(output_path).events] == ["Você vem?", "Você vem!"]


@pytest.mark.parametrize("source,target,style", [
    (r"{\blur1\c&HCB91E0&\pos(497.252,168.333)}How {\c&HE8A070&}Beetles {\c&HCB91E0&}Transform",
     "Como os Besouros se Transformam", "Sign"),
    (r"{\blur2\t(0,100,1,\fsp0\blur0.4)}R{\fs75}OAD{\fs} TO H{\fs75}ERO",
     "ESTRADA PARA UM HERÓI", "Sign"),
    (r"{\a6\pos(316,379)}{\c&HFFFFFF&\3c&H000000&}The solitude you carry on your\N\N\Nback, does it end with your life ?",
     "A solidão que você carrega nas costas, isso termina com a sua vida?", "Sign"),
    (r"What {\c&HFFFFFF&}now ?", "O quê agora?", "Sign"),
    (r"{\i1}Hello {\i0}world", "Olá mundo", "Default"),
    (r"{*}{\an8}Hello {*}world", "Olá mundo", "Sign"),
    (r"{\k10}Hi{\k20}ka{\k30}ru", "Brilhe", "OP"),
    (r"{\c&HFF0000&}{\i1}O{\c&H00FF00&}{\i0}K", "Sim", "Sign"),
    (r"{\an8}{\c&HFF0000&}O{\bord2}{\c&H00FF00&}K{\bord0}", "Sim", "Sign"),
])
def test_legitimate_styled_corpus_remains_valid_after_effects_and_serialization(tmp_path, source, target, style):
    from ass_structure import replace_source_payload
    original = _document(source, style=style)
    translated = _document(replace_source_payload(source, target), style=style)
    InMemoryEffectsEngine().process_effects(translated, original)
    result = validate_document_structure(original, translated, allow_tag_reflow_indices={0})
    assert result["valid"], result["issues"]
    path = tmp_path / "styled.ass"
    translated.save(path)
    serialized = validate_document_structure(original, ASSDocumentAST.from_file(path), allow_tag_reflow_indices={0})
    assert serialized["valid"], serialized["issues"]


@pytest.mark.parametrize("source", [
    r"{\k106}O{\k94}s {\k79}i{\k36}u{\k132}sti {\k99}me{\k159}di{\k53}ta{\k109}bi{\k256}tur",
    r"{\c&HFF0000&\t(0,100,\c&H0000FF&)}O{\c&H00FF00&}K",
    r"{\c&HFF0000&\rdefault}O{\c&H00FF00&}K",
    r"{\i1}Hello {\i0}world",
    r"{\an8}One  {\fs30}Two",
])
def test_identity_styled_corpus_preserves_source_program_exactly(source):
    original = _document(source, style="Sign")
    translated = _document(source, style="Sign")
    InMemoryEffectsEngine().process_effects(translated, original)
    # Existing whitespace cleanup is allowed, but the override order and
    # timing program must stay under source ownership.
    assert validate_inline_tags(source, translated[0].text) == []
    assert validate_document_structure(original, translated, allow_tag_reflow_indices={0})["valid"]
