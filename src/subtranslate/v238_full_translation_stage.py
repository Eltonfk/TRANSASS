"""Reusable V2.3.8 full-translation stage contract.

The stage owns only generic source/candidate structural checks and deterministic
presentation helpers.  Dispatch, queue, summary, archive, publication and
lineage remain owned by the canonical P2C modules.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pysubs2

import pipeline_v2_1_3 as pipeline
from v238_semantic_style_ownership import extract_semantic_style_ownership
from v238_source_payload import rc4_replace_source_payload


STAGE_ID = "FULL_TRANSLATION_V238"
PIPELINE_ID = "v2_3_8"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_v238_candidate(source: str | Path, candidate: str | Path) -> dict[str, Any]:
    """Validate generic source/candidate structure without model or network."""
    source_path, candidate_path = Path(source), Path(candidate)
    if not source_path.is_file() or not candidate_path.is_file():
        raise FileNotFoundError("V2.3.8 source and candidate files are required")
    _source_subs_loaded, source_events, _source_profile = pipeline.load_events(source_path, {})
    _candidate_subs_loaded, candidate_events, _candidate_profile = pipeline.load_events(candidate_path, {})
    source_subs = pysubs2.load(str(source_path), format="ass")
    candidate_subs = pysubs2.load(str(candidate_path), format="ass")
    if len(source_events) != len(candidate_events) or len(source_subs.events) != len(candidate_subs.events):
        raise ValueError("V2.3.8 candidate event cardinality mismatch")
    for source_event, target_event in zip(source_events, candidate_events):
        if (source_event.start, source_event.end, source_event.layer, source_event.style) != (target_event.start, target_event.end, target_event.layer, target_event.style):
            raise ValueError(f"V2.3.8 presentation envelope mismatch at event {source_event.id}")
        # A semantic ownership program is optional for plain events, but if an
        # inline transition exists it must parse and validate before archive.
        if "\\" in (source_event.original_text or ""):
            program, details = extract_semantic_style_ownership(source_event.original_text, program_id=f"event-{source_event.id}", envelope_id=source_event.id)
            if program is not None and not details.get("valid", True):
                raise ValueError(f"V2.3.8 ownership validation failed at event {source_event.id}")
    # Exercise the canonical deterministic whitespace seam without using a
    # historical episode or review capture as an oracle.
    probe = rc4_replace_source_payload("{\\b1} alpha beta", "alfa beta")
    if "alfa" not in probe or "beta" not in probe:
        raise ValueError("V2.3.8 source payload seam failed")
    return {
        "pipeline": PIPELINE_ID,
        "stage_id": STAGE_ID,
        "event_count": len(source_events),
        "source_sha256": _sha256(source_path),
        "candidate_sha256": _sha256(candidate_path),
        "publishable": False,
        "durable_intermediate": True,
        "model_calls": 0,
        "network_calls": 0,
    }


__all__ = ["PIPELINE_ID", "STAGE_ID", "validate_v238_candidate"]
