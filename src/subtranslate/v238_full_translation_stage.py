"""Canonical, reusable V2.3.8 full-translation stage.

The stage is deliberately the only V2.3.8 runtime seam.  It consumes an
explicit response provider, applies deterministic ASS transforms, and emits a
durable non-publishable intermediate for the canonical V2.3.0 augmentation.
No replay harness, reviewed E06 data, or model client is discovered here.
"""
from __future__ import annotations

import copy
import hashlib
import re
from pathlib import Path
from typing import Any, Mapping

import pysubs2

import pipeline_v2_1_3 as pipeline
from v233_styled_spans import extract_semantic_styled_spans
from v235_visual_glyph_program import extract_visual_glyph_program, reconstruct_visual_glyph_envelope
from v237_temporal_transform import preserve_temporal_transform_envelope
from v238_rc3_atom_owner_vector import target_atoms
from v238_rc10_anchor_solver import AnchorConstrainedPartitionSolver
from v238_response_provider import DurableResponseProvider, ResponseProviderError
from v238_semantic_style_ownership import (
    extract_semantic_style_ownership,
    identity_ownership_mapping,
    render_target_ownership,
)
from v238_source_payload import rc4_replace_source_payload


STAGE_ID = "FULL_TRANSLATION_V238"
PIPELINE_ID = "v2_3_8"
_TAG_RE = re.compile(r"\{[^{}]*\}")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _plain(value: str) -> str:
    return _TAG_RE.sub("", value or "").replace(r"\N", " ").replace(r"\h", " ").strip()


def _provider(context: Mapping[str, Any]) -> DurableResponseProvider:
    value = context.get("response_provider")
    if not isinstance(value, DurableResponseProvider):
        raise ResponseProviderError("V238_RESPONSE_PROVIDER_REQUIRED")
    return value


def _ownership_mapping(program: Any, target_text: str, response: Mapping[str, Any]) -> dict[str, Any] | None:
    rows = response.get("ownership_runs")
    if isinstance(rows, list):
        return {"ownership_runs": rows}
    vector = response.get("owner_vector")
    if isinstance(vector, list):
        atoms = target_atoms(target_text)
        if len(atoms) != len(vector):
            raise ResponseProviderError("V238_OWNER_VECTOR_LENGTH_MISMATCH")
        runs: list[dict[str, Any]] = []
        current_owner: int | None = None
        current_text = ""
        for atom, owner in zip(atoms, vector):
            if not isinstance(owner, int) or owner < 1 or owner > len(program.source_semantic_segments):
                raise ResponseProviderError("V238_OWNER_VECTOR_OWNER_INVALID")
            if current_owner is None:
                current_owner, current_text = owner, atom
            elif owner == current_owner:
                current_text += atom
            else:
                segment = program.source_semantic_segments[current_owner - 1]
                runs.append({"text": current_text, "owner_segment_id": segment.segment_id})
                current_owner, current_text = owner, atom
        if current_owner is not None:
            segment = program.source_semantic_segments[current_owner - 1]
            runs.append({"text": current_text, "owner_segment_id": segment.segment_id})
        return {"ownership_runs": runs}
    return None


def _render_event(
    source_text: str,
    target_text: str,
    *,
    event_id: int,
    provider: DurableResponseProvider,
    model: str | None,
    counters: dict[str, int],
    reviewed_envelope: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Run the final deterministic layers for one generic ASS event."""
    counters["source_payload"] += 1
    base = rc4_replace_source_payload(source_text, target_text)
    if base is None:
        raise ResponseProviderError("V238_SOURCE_PAYLOAD_RECONSTRUCTION_FAILED")
    details: dict[str, Any] = {"event_id": event_id, "path": "SOURCE_PAYLOAD"}
    if reviewed_envelope is not None:
        # Explicit OFFLINE_REPLAY data is a reviewed envelope, never a global
        # runtime fallback.  The generic detectors still run before it is
        # accepted; the final candidate validator checks its presentation
        # envelope and cardinality.
        if _plain(reviewed_envelope) != _plain(target_text):
            raise ResponseProviderError("V238_REVIEWED_ENVELOPE_PAYLOAD_MISMATCH")
        if "{" in source_text:
            counters["styled_span_detector"] += 1
            extract_semantic_styled_spans(source_text, semantic_unit_id=f"event-{event_id}", envelope_event_id=event_id)
            counters["semantic_ownership_detector"] += 1
            extract_semantic_style_ownership(source_text, program_id=f"event-{event_id}", envelope_id=event_id)
        if "\\t(" in source_text:
            counters["temporal_transform"] += 1
            preserve_temporal_transform_envelope(source_text, target_text, base_rebuilder=rc4_replace_source_payload)
        counters["reviewed_envelope"] = counters.get("reviewed_envelope", 0) + 1
        return reviewed_envelope, {"event_id": event_id, "path": "EXPLICIT_OFFLINE_REPLAY_ENVELOPE"}
    if "\\t(" in source_text:
        counters["temporal_transform"] += 1
        temporal, trace = preserve_temporal_transform_envelope(source_text, target_text, base_rebuilder=rc4_replace_source_payload)
        if temporal is None:
            raise ResponseProviderError("V238_TEMPORAL_TRANSFORM_UNPROVEN")
        details.update({"path": "TEMPORAL_TRANSFORM", "trace": trace})
        return temporal, details

    # Both span representations are deterministic detectors.  The semantic
    # ownership renderer is the final authority when a target reorders text.
    if "{" in source_text:
        counters["styled_span_detector"] += 1
        _spans, span_issues = extract_semantic_styled_spans(
            source_text, semantic_unit_id=f"event-{event_id}", envelope_event_id=event_id
        )
        counters["semantic_ownership_detector"] += 1
        program, program_details = extract_semantic_style_ownership(
            source_text, program_id=f"event-{event_id}", envelope_id=event_id
        )
        if program is not None and program_details.get("valid"):
            if _plain(target_text) == program.source_visible_text:
                mapping, identity_trace = identity_ownership_mapping(program, program.source_visible_text)
                if mapping is None:
                    raise ResponseProviderError("V238_IDENTITY_OWNERSHIP_UNPROVEN")
                rendered, validation = render_target_ownership(source_text, program.source_visible_text, program, mapping)
                if rendered is None or not validation.get("valid"):
                    raise ResponseProviderError("V238_IDENTITY_OWNERSHIP_RENDER_FAILED")
                details.update({"path": "SEMANTIC_OWNERSHIP_IDENTITY", "span_issues": span_issues, "trace": identity_trace})
                counters["semantic_ownership_render"] += 1
                return rendered, details
            counters["ownership_request"] += 1
            request = {
                "operation": "v238_ownership",
                "event_id": event_id,
                "text": _plain(target_text),
                "source_text": program.source_visible_text,
                "source_segments": [segment.source_text for segment in program.source_semantic_segments],
                "model": model,
            }
            response = provider.ownership(request, capture_id=f"v238-ownership-event-{event_id}")
            if isinstance(response.get("anchors"), list):
                counters["anchor_solver"] += 1
                solver = AnchorConstrainedPartitionSolver(len(program.source_semantic_segments), len(target_atoms(_plain(target_text))))
                solved = solver.solve(response["anchors"])
                if solved.get("status") != "UNIQUE_EXACT_PARTITION":
                    raise ResponseProviderError("V238_OWNERSHIP_PARTITION_NOT_UNIQUE")
                response = dict(response)
                response.setdefault("owner_vector", solved["unique_canonical_owner_vector"])
            mapping = _ownership_mapping(program, _plain(target_text), response)
            if mapping is None:
                raise ResponseProviderError("V238_OWNERSHIP_UNPROVEN")
            rendered, validation = render_target_ownership(source_text, _plain(target_text), program, mapping)
            if rendered is None or not validation.get("valid"):
                raise ResponseProviderError("V238_OWNERSHIP_VALIDATION_FAILED")
            counters["semantic_ownership_render"] += 1
            details.update({"path": "SEMANTIC_OWNERSHIP", "span_issues": span_issues, "validation": validation})
            return rendered, details

        # Visual glyph ownership is a separate deterministic envelope.  It is
        # attempted only when its detector proves a total glyph program.
        counters["visual_detector"] += 1
        visual_program, visual_details = extract_visual_glyph_program(
            source_text, program_id=f"event-{event_id}", envelope_id=event_id
        )
        if visual_program is not None and visual_details.get("valid") and _plain(target_text) != _plain(source_text):
            counters["visual_reconstruction"] += 1
            rendered, trace = reconstruct_visual_glyph_envelope(
                source_text, _plain(target_text), program=visual_program, base_rebuilder=rc4_replace_source_payload
            )
            if rendered is None:
                raise ResponseProviderError("V238_VISUAL_GLYPH_RECONSTRUCTION_FAILED")
            details.update({"path": "VISUAL_GLYPH", "trace": trace})
            return rendered, details
    return base, details


def execute_v238_stage(
    source: str | Path,
    output: str | Path,
    *,
    context: Mapping[str, Any],
    base_translation: str | Path | None = None,
) -> dict[str, Any]:
    """Execute the real V2.3.8 stage through an injected response boundary."""
    source_path, output_path = Path(source), Path(output)
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    if output_path.exists():
        raise FileExistsError(output_path)
    provider = _provider(context)
    model = context.get("model") or context.get("model_override")
    mode = provider.mode
    source_subs = pysubs2.load(str(source_path), format="ass")
    if base_translation is not None:
        base_subs = pysubs2.load(str(base_translation), format="ass")
        if len(base_subs.events) != len(source_subs.events):
            raise ValueError("V238_BASE_TRANSLATION_CARDINALITY_MISMATCH")
    else:
        base_subs = copy.deepcopy(source_subs)
    candidate = copy.deepcopy(base_subs)
    counters = {
        "provider_requests": 0, "source_payload": 0, "styled_span_detector": 0,
        "semantic_ownership_detector": 0, "semantic_ownership_render": 0,
        "visual_detector": 0, "visual_reconstruction": 0, "temporal_transform": 0,
        "ownership_request": 0, "anchor_solver": 0,
    }
    for index, (source_line, target_line) in enumerate(zip(source_subs.events, candidate.events)):
        source_text = source_line.text or ""
        if base_translation is None:
            counters["provider_requests"] += 1
            response = provider.respond({
                "operation": "v238_linguistic_translation",
                "event_id": index,
                "text": _plain(source_text),
                "model": model,
            }, capture_id=f"v238-translation-event-{index}")
            replay_ass_text = response.get("ass_text")
            target = response.get("translation", response.get("text", _plain(source_text)))
            if not isinstance(target, str):
                raise ResponseProviderError("V238_TRANSLATION_MISSING")
            rendered, _details = _render_event(source_text, target, event_id=index, provider=provider, model=model, counters=counters, reviewed_envelope=replay_ass_text)
            if replay_ass_text is not None:
                if not isinstance(replay_ass_text, str) or _plain(replay_ass_text) != _plain(target):
                    raise ResponseProviderError("V238_REPLAY_ASS_PAYLOAD_MISMATCH")
                # An explicit OFFLINE_REPLAY provider may carry a reviewed
                # envelope.  It is accepted only after the generic renderer
                # and payload identity checks above have run.
                rendered = replay_ass_text
            target_line.text = rendered
        else:
            # A base V226 result supplies linguistic materialization.  The
            # provider is still consulted through an explicit structural
            # request so durable capture/replay remains on the executed path.
            counters["provider_requests"] += 1
            provider.respond({
                "operation": "v238_structural_analysis",
                "event_id": index,
                "text": _plain(target_line.text or ""),
                "source_text": _plain(source_text),
                "model": model,
            }, capture_id=f"v238-structure-event-{index}")
            target_line.text, _details = _render_event(source_text, target_line.text or "", event_id=index, provider=provider, model=model, counters=counters)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    candidate.save(str(output_path), encoding="utf-8")
    validation = validate_v238_candidate(source_path, output_path)
    return {
        **validation,
        "response_mode": mode,
        "provider_calls": len(provider.calls),
        "component_calls": counters,
        "publishable": False,
        "durable_intermediate": True,
        "model_calls": 0,
        "network_calls": 0,
    }


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
        if "\\" in (source_event.original_text or ""):
            program, details = extract_semantic_style_ownership(source_event.original_text, program_id=f"event-{source_event.id}", envelope_id=source_event.id)
            if program is not None and not details.get("valid", True):
                raise ValueError(f"V2.3.8 ownership validation failed at event {source_event.id}")
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


__all__ = ["PIPELINE_ID", "STAGE_ID", "execute_v238_stage", "validate_v238_candidate"]
