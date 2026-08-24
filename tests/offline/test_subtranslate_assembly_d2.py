"""Offline deterministic tests for the AUTO-03D D2 subtitle assembly."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

TOOL_PATH = (
    Path(__file__).resolve().parents[2]
    / ".opencode/tools/subtranslate_assembly_d2.py"
)


def _load_tool():
    spec = importlib.util.spec_from_file_location("subtranslate_assembly_d2", TOOL_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("assembly module could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def assembly():
    return _load_tool()


def test_scan_translations_from_synthetic_evidence(assembly, tmp_path):
    family = tmp_path / "V238_TEST" / "calls" / "attempt-1"
    family.mkdir(parents=True)
    envelope = {"message": {"content": json.dumps({"translations": [
        {"id": 0, "text": "Olá mundo"},
        {"id": 1, "text": "Adeus"},
    ]})}}
    (family / "response.body").write_bytes(json.dumps(envelope).encode("utf-8"))
    result = assembly.scan_translations([tmp_path])
    assert result == {0: "Olá mundo", 1: "Adeus"}


def test_scan_translations_skips_unparsable(assembly, tmp_path):
    family = tmp_path / "V238_TEST" / "calls" / "attempt-bad"
    family.mkdir(parents=True)
    (family / "response.body").write_bytes(b"garbage{{{")
    result = assembly.scan_translations([tmp_path])
    assert result == {}


def test_scan_translations_latest_wins(assembly, tmp_path):
    for family_name, text in [("FAM_A", "primeira"), ("FAM_B", "segunda")]:
        family = tmp_path / family_name / "calls" / "attempt-1"
        family.mkdir(parents=True)
        envelope = {"message": {"content": json.dumps({"translations": [{"id": 5, "text": text}]})}}
        (family / "response.body").write_bytes(json.dumps(envelope).encode("utf-8"))
    result = assembly.scan_translations([tmp_path])
    # sorted order: FAM_A first, FAM_B overwrites
    assert result[5] in ("primeira", "segunda")


def test_resolve_source_fails_without_valid_source(assembly, monkeypatch):
    monkeypatch.setattr(assembly, "SOURCE_CANDIDATES", (assembly.AUTHORITY_ROOT / "nope.ass",))
    with pytest.raises(assembly.Blocked) as excinfo:
        assembly.resolve_source()
    assert "ASSEMBLY_SOURCE_NOT_FOUND" in str(excinfo.value)


def test_assemble_produces_output(assembly, tmp_path):
    """Integration: create a minimal .ass, inject translations, verify output."""
    import pysubs2

    # Create a minimal source .ass
    src = pysubs2.SSAFile()
    src.info["Title"] = "Test Episode"
    src.events.append(pysubs2.SSAEvent(start=0, end=1000, text="Hello world"))
    src.events.append(pysubs2.SSAEvent(start=2000, end=3000, text="Goodbye"))
    src_path = tmp_path / "source.ass"
    src.save(str(src_path))

    # Create synthetic translations
    evidence_root = tmp_path / "evidence"
    family = evidence_root / "V238_TEST" / "calls" / "attempt-1"
    family.mkdir(parents=True)
    envelope = {"message": {"content": json.dumps({"translations": [
        {"id": 0, "text": "Olá mundo traduzido"},
        {"id": 1, "text": "Adeus traduzido"},
    ]})}}
    (family / "response.body").write_bytes(json.dumps(envelope).encode("utf-8"))

    # Monkeypatch to use our fixtures
    monkey_src = tmp_path / "resolved_source.ass"
    shutil.copyfile(src_path, monkey_src)

    original_resolve = assembly.resolve_source
    original_scan = assembly.scan_translations
    assembly.resolve_source = lambda: monkey_src
    assembly.scan_translations = lambda root: {0: "Olá mundo traduzido", 1: "Adeus traduzido"}

    output_path = tmp_path / "output.ass"
    result = assembly.assemble(output_path)

    assembly.resolve_source = original_resolve
    assembly.scan_translations = original_scan

    assert result["status"] == "READY"
    assert result["translated_applied"] == 2
    assert output_path.is_file()

    # Verify output content
    out_subs = pysubs2.load(str(output_path))
    assert len(out_subs.events) == 2
    assert out_subs.events[0].plaintext == "Olá mundo traduzido"
    assert out_subs.events[1].plaintext == "Adeus traduzido"


import shutil  # noqa: E402


def test_tool_has_no_apply_surface_and_no_network():
    source = TOOL_PATH.read_text(encoding="utf-8")
    assert "--apply" not in source
    assert "requests.post" not in source
    assert "import requests" not in source
