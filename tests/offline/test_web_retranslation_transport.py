import os
from pathlib import Path
from unittest import mock

from web_retranslation_runner import _provider_for
from web_durable_provider import WebDurableResponseProvider
from pipeline_v2_1_3 import validate_inline_tags
from v238_full_translation_stage import _render_event, _repair_ownership_whitespace
from v238_source_payload import rc4_replace_source_payload


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
