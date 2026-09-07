import os
from pathlib import Path
from unittest import mock
import pytest

from web_retranslation_runner import _provider_for
from web_durable_provider import WebDurableResponseProvider
from pipeline_v2_1_3 import validate_inline_tags
from web_audit_retranslation import source_relative_delimiter_audit
from v238_full_translation_stage import _render_event, _repair_ownership_whitespace
from v238_source_payload import rc4_replace_source_payload
from transport_providers import TransportBlocked


def test_retranslation_ollama_uses_container_reachable_url():
    with mock.patch.dict(
        os.environ,
        {"TRANSLATOR_OLLAMA_URL": "http://192.168.1.5:11434/api/chat"},
        clear=False,
    ):
        transport = _provider_for(
            {"provider": "ollama", "model": "qwen3.5:9b", "base_url": None},
            {},
        )

    assert transport is not None
    assert transport.endpoint() == "http://192.168.1.5:11434/api/chat"


def test_retranslation_invalid_provider_fails_at_configuration_boundary():
    with pytest.raises(TransportBlocked, match="TRANSPORT_PROVIDER_UNKNOWN"):
        _provider_for({"provider": "not-a-provider", "model": "model"}, {})


def test_retranslation_cloud_provider_without_key_fails_before_network():
    with pytest.raises(RuntimeError, match="GROQ_API_KEY_MISSING"):
        _provider_for({"provider": "groq", "model": "openai/gpt-oss-20b"}, {})


def test_retranslation_deepseek_without_key_fails_before_network():
    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY_MISSING"):
        _provider_for({"provider": "deepseek", "model": "deepseek-chat"}, {})


def test_ownership_request_is_strict_and_preserves_segment_ids(tmp_path: Path):
    provider = WebDurableResponseProvider(
        {"primary": {"provider": "ollama", "model": "qwen3.5:9b"}},
        mode="TEST_FAKE",
        capture_root=tmp_path,
    )
    request = provider._project_request({
        "operation": "v238_ownership",
        "event_id": 115,
        "text": "Como você conseguiu?",
        "source_segments": [
            {"segment_id": "event-115:segment-1", "source_text": "Comment vous avez fait ?"},
            {"segment_id": "event-115:segment-2", "source_text": "Hein ?"},
        ],
    })

    assert request["format"]["required"] == ["ownership_runs"]
    assert "somente um objeto JSON válido" in request["messages"][0]["content"]
    assert "byte a byte" in request["messages"][0]["content"]
    assert "event-115:segment-1" in request["messages"][1]["content"]
    assert "candidate_linguistic_text" in request["messages"][1]["content"]


def test_ownership_whitespace_is_reallocated_without_changing_visible_text():
    target = "- Como você  conseguiu? - Hein?"
    rows = [
        {"text": "- Como você  conseguiu?", "owner_segment_id": "segment-1"},
        {"text": "- Hein?", "owner_segment_id": "segment-2"},
    ]

    repaired = _repair_ownership_whitespace(target, rows)

    assert repaired is not None
    assert "".join(row["text"] for row in repaired) == target
    assert repaired[0]["text"].endswith("? ")
    assert [row["owner_segment_id"] for row in repaired] == ["segment-1", "segment-2"]


def test_source_payload_discards_model_ass_tags_and_preserves_source_breaks():
    source = r"{\i1}Bonjour.{\i0}\N{\i1}À bientôt.{\i0}"
    target = r"{\i1}{\i1}Olá.{\i0}{\i0}\N{\i1}{\i1}Até breve.{\i0}{\i0}"

    rendered = rc4_replace_source_payload(source, target)

    assert rendered == r"{\i1}Olá.{\i0}\N{\i1}Até breve.{\i0}"
    assert validate_inline_tags(source, rendered) == []


def test_source_payload_restores_source_break_when_model_omits_it():
    source = r"{\i1}Bonjour.{\i0}\N{\i1}À bientôt.{\i0}"

    rendered = rc4_replace_source_payload(source, r"{\i1}Olá. Até breve.{\i0}")

    assert rendered.count(r"\N") == 1
    assert r"{\i1}{\i1}" not in rendered
    assert rendered.replace(r"\N", " ").replace(r"{\i1}", "").replace(r"{\i0}", "").strip() == "Olá. Até breve."


def test_ordinary_v238_event_uses_source_owned_reenvelope():
    class OrdinaryProvider:
        def v238_group_key(self, event_id: int):
            return None

    source = r"{\i1}Bonjour.{\i0}\N{\i1}À bientôt.{\i0}"
    model_output = r"{\i1}{\i1}Olá. Até breve.{\i0}{\i0}"

    rendered, details = _render_event(
        source,
        model_output,
        event_id=8,
        provider=OrdinaryProvider(),
        model=None,
        counters={"source_payload": 0},
    )

    assert details["path"] == "BASE_V226_PAYLOAD_REENVELOPED"
    assert validate_inline_tags(source, rendered) == []
    assert rendered.count(r"\N") == source.count(r"\N")


def test_ordinary_v238_event_keeps_materialized_break_spacing():
    class OrdinaryProvider:
        def v238_group_key(self, event_id: int):
            return None

    source = r"Put your hands up,\Nand turn slowly toward me!"
    materialized = r"Levante as mãos e\N gire devagar para mim!"
    rendered, details = _render_event(
        source,
        materialized,
        event_id=1,
        provider=OrdinaryProvider(),
        model=None,
        counters={"source_payload": 0},
    )

    assert details["path"] == "BASE_V226_PAYLOAD_REENVELOPED"
    assert rendered == materialized


def test_karaoke_identity_preserves_source_spacing_and_timing_tags():
    class OrdinaryProvider:
        pass

    source = r"{\k106}O{\k94}s {\k79}i{\k36}u{\k132}sti {\k99}me{\k159}di{\k53}ta{\k109}bi{\k256}tur"
    rendered, details = _render_event(
        source,
        source,
        event_id=3658,
        provider=OrdinaryProvider(),
        model=None,
        counters={"source_payload": 0},
    )

    assert rendered == source
    assert details["path"] == "KARAOKE_IDENTITY_PRESERVED"


def test_semantic_ownership_emits_explicit_toggle_reset_and_restores_break():
    class OwnershipProvider:
        def v238_group_key(self, event_id: int):
            return "event-139"

        def ownership(self, request, *, capture_id=None):
            return {"ownership_runs": [
                {"text": "Não precisa ser mais gentil com", "owner_segment_id": "event-139:segment-1"},
                {"text": "ela do que", "owner_segment_id": "event-139:segment-2"},
                {"text": "precisa ser, está bem?", "owner_segment_id": "event-139:segment-3"},
            ]}

    source = r"No need to be nicer to her\Nthan you {\i1}need{\i0} to be, okay?"
    target = r"Não precisa ser mais gentil com\N ela do {\i1}que {\i0}precisa ser, está bem?"
    counters = {key: 0 for key in (
        "source_payload", "styled_span_detector", "semantic_ownership_detector",
        "semantic_ownership_render", "visual_detector", "visual_reconstruction",
        "temporal_transform", "ownership_request", "anchor_solver",
    )}

    rendered, details = _render_event(
        source, target, event_id=139, provider=OwnershipProvider(), model=None,
        counters=counters, ownership_cache={},
    )

    assert details["path"] == "SEMANTIC_OWNERSHIP"
    assert rendered.count(r"\N") == 1
    assert r"{\i}" not in rendered
    assert r"{\i0}" in rendered
    assert validate_inline_tags(source, rendered) == []


def test_unproven_ownership_keeps_a_safe_v226_envelope():
    class InvalidOwnershipProvider:
        def v238_group_key(self, event_id: int):
            return "event-135"

        def ownership(self, request, *, capture_id=None):
            # A small model can echo source-oriented runs.  The V226 output
            # is already a structurally valid envelope and must be retained.
            return {"ownership_runs": [
                {"text": "Ah, Haraguchi-", "owner_segment_id": "event-135:segment-1"},
                {"text": "san", "owner_segment_id": "event-135:segment-2"},
                {"text": "!", "owner_segment_id": "event-135:segment-3"},
            ]}

    source = r"Ah, Haraguchi-{\i1}san{\i0}!"
    target = r"Ah, Sr Haraguchi{\i1}!{\i0}"
    counters = {key: 0 for key in (
        "source_payload", "styled_span_detector", "semantic_ownership_detector",
        "semantic_ownership_render", "visual_detector", "visual_reconstruction",
        "temporal_transform", "ownership_request", "anchor_solver",
    )}
    rendered, details = _render_event(
        source, target, event_id=135, provider=InvalidOwnershipProvider(), model=None,
        counters=counters, ownership_cache={},
    )

    assert details["path"] == "SEMANTIC_OWNERSHIP_FALLBACK_VALIDATION"
    assert rendered == target
    assert validate_inline_tags(source, rendered) == []


def test_cross_event_delimiter_balance_is_not_reported_as_corruption():
    source = '"Police Headquarters -'
    output = '"Quartel-General da Polícia -'
    result = source_relative_delimiter_audit(source, output, sequence_preserved=True)
    assert result["state"] == "SOURCE_PREEXISTING_UNBALANCED_PRESERVED"
    assert result["fatal"] is False


def test_changed_delimiter_still_fails_closed():
    result = source_relative_delimiter_audit('"Police Headquarters -', 'Quartel-General da Polícia -', sequence_preserved=True)
    assert result["fatal"] is True
