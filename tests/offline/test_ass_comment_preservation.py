import copy

import pysubs2
import pytest

import pipeline_v2_1_3 as pipeline
from production_v2_3_8_adapter import _validate_base_presentation_envelope
from v238_full_translation_stage import validate_v238_candidate
from v238_full_translation_stage import execute_v238_stage
from v238_response_provider import DurableResponseProvider
from web_audit_retranslation import audit_record


def source_file(tmp_path, duration=0):
    subs = pysubs2.SSAFile()
    subs.events = [pysubs2.SSAEvent(
        type="Comment", start=13850, end=13850 + duration,
        style="def2", name="Comment", text=", I'd assume}",
    )]
    path = tmp_path / "source.ass"
    subs.save(str(path))
    return path


@pytest.mark.parametrize("duration", [0, 1000])
def test_actual_comments_preserved_without_translation(tmp_path, duration):
    path = source_file(tmp_path, duration)
    subs, events, profile = pipeline.load_events(path, {})
    assert events[0].is_comment
    assert events[0].classification == "TECHNICAL_OR_EMPTY"
    runner = pipeline.Runner(events, profile, pipeline.Config(ollama_url="http://unused.invalid"), {})
    assert runner.plan_initial_batches() == []
    assert runner.results[0].final_text == ", I'd assume}"
    assert pipeline.validate_structure(subs, subs)["valid"]
    _validate_base_presentation_envelope(path, path)
    validate_v238_candidate(path, path)
    assert audit_record(path, path)["blocking_flags"] == []


def test_dialogue_named_comment_is_not_a_comment(tmp_path):
    path = source_file(tmp_path)
    subs = pysubs2.load(str(path))
    subs[0].type = "Dialogue"
    subs.save(str(path))
    _, events, _ = pipeline.load_events(path, {})
    assert not events[0].is_comment
    assert events[0].classification != "TECHNICAL_OR_EMPTY"


def test_v238_does_not_call_provider_for_comment(tmp_path):
    source = source_file(tmp_path)
    output = tmp_path / "output.ass"
    def unexpected_call(request):
        pytest.fail("Comment must never be sent to provider")
    provider = DurableResponseProvider("TEST_FAKE", fake=unexpected_call)
    result = execute_v238_stage(source, output, context={"response_provider": provider})
    assert result["provider_calls"] == 0
    assert pysubs2.load(str(source))[0].as_dict() == pysubs2.load(str(output))[0].as_dict()


def test_structural_preparation_leaves_comment_untouched(tmp_path, monkeypatch):
    import production_v2_2_1_adapter as adapter
    path = source_file(tmp_path)
    subs = pysubs2.load(str(path))
    subs[0].text = r"{\i1}\N, I'd assume}\N"
    subs.save(str(path))
    _, events, profile = pipeline.load_events(path, {})
    def offline_init(self, prepared, *args):
        self.units = pipeline.build_sign_groups(prepared)
    monkeypatch.setattr(adapter.MemoryRunner, "__init__", offline_init)
    runner = adapter.V221MemoryRunner(events, profile, pipeline.Config(ollama_url="http://unused.invalid"), {}, None, 1, 1, "offline")
    assert runner.v221_prepared_events[0] is events[0]
    assert runner.v221_transformations == {}


def test_comment_is_not_dialogue_context(tmp_path):
    path = source_file(tmp_path)
    subs = pysubs2.load(str(path))
    subs.events.insert(0, pysubs2.SSAEvent(start=12000, end=13000, text="I will return tomorrow."))
    subs.events.append(pysubs2.SSAEvent(start=14000, end=15000, text="You should wait for me."))
    subs.save(str(path))
    _, events, _ = pipeline.load_events(path, {})
    context = pipeline.choose_context(events, events[2], pipeline.Config(ollama_url="http://unused.invalid"))
    assert [item["id"] for item in context["previous"]] == [0]
    assert [event.id for event in events] == [0, 1, 2]


def test_persistent_gemini_503_does_not_split_into_more_requests(monkeypatch):
    event = pipeline.Event(
        id=0, original_index=0, layer=0, start=0, end=1000, style="Default",
        name="", marginl=0, marginr=0, marginv=0, effect="",
        original_text="Hello", visible_text="Hello", clean_text="Hello",
        segments=[], tag_anchors=[], line_break_boundaries=[], has_positioning=False,
    )
    config = pipeline.Config(ollama_url="http://unused.invalid", model="gemini-3.5-flash-lite")
    config.transport = type("Transport", (), {"name": "gemini"})()
    runner = pipeline.Runner([event], {}, config, {})
    calls = []
    def always_unavailable(*args, **kwargs):
        calls.append(1)
        return set(), ["TRANSIENT_HTTP_503"]
    monkeypatch.setattr(runner, "_attempt", always_unavailable)
    monkeypatch.setattr(pipeline.time, "sleep", lambda _seconds: None)
    runner._process_units([pipeline.Unit("event-0", [event])], attempt_type="INITIAL", logical_batch_id="batch-0")
    assert len(calls) == 4
    assert runner.results[0].status == "failed"
    assert runner.results[0].failure_reason == "TRANSIENT_HTTP_503_EXHAUSTED"


@pytest.mark.parametrize("field,value", [("type", "Dialogue"), ("text", "changed"), ("end", 15000)])
def test_comment_mutation_fails_closed(tmp_path, field, value):
    path = source_file(tmp_path)
    source = pysubs2.load(str(path))
    changed = copy.deepcopy(source)
    setattr(changed[0], field, value)
    output = tmp_path / "changed.ass"
    changed.save(str(output))
    assert not pipeline.validate_structure(source, changed)["valid"]
    with pytest.raises(Exception):
        _validate_base_presentation_envelope(path, output)
    with pytest.raises(ValueError):
        validate_v238_candidate(path, output)
    assert "ASS_COMMENT_CHANGED" in audit_record(path, output)["blocking_flags"]
