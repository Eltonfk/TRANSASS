"""Canonical, reusable V2.3.8 full-translation stage.

The stage is deliberately the only V2.3.8 runtime seam.  It consumes an
explicit response provider, applies deterministic ASS transforms, and emits a
durable non-publishable intermediate for the canonical V2.3.0 augmentation.
No replay harness, reviewed E06 data, or model client is discovered here.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
import time
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
from pipeline_v2_1_3 import preserve_source_punctuation_profile


STAGE_ID = "FULL_TRANSLATION_V238"
PIPELINE_ID = "v2_3_8"
_TAG_RE = re.compile(r"\{[^{}]*\}")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _metric_delta(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    """Return an auditable before/after delta without mutating provider state."""
    delta: dict[str, Any] = {}
    keys = set(before) | set(after)
    for key in sorted(keys):
        old, new = before.get(key), after.get(key)
        if isinstance(old, Mapping) or isinstance(new, Mapping):
            old_map = old if isinstance(old, Mapping) else {}
            new_map = new if isinstance(new, Mapping) else {}
            delta[key] = {
                child: int(new_map.get(child, 0)) - int(old_map.get(child, 0))
                for child in sorted(set(old_map) | set(new_map))
                if isinstance(new_map.get(child, old_map.get(child, 0)), (int, float))
            }
        elif isinstance(old, (int, float)) or isinstance(new, (int, float)):
            delta[key] = int(new or 0) - int(old or 0)
        elif old != new:
            delta[key] = {"before": old, "after": new}
    return delta


def _sync_file(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def _sync_dir(path: Path) -> None:
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    """Persist an authoritative marker using sibling+fsync+rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    os.close(fd)
    temporary = Path(raw)
    try:
        temporary.write_bytes((json.dumps(dict(value), ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
        _sync_file(temporary)
        os.replace(temporary, path)
        _sync_dir(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_stage_write(candidate: pysubs2.SSAFile, output_path: Path, source_path: Path, context: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a sibling file before making it authoritative."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=f".{output_path.name}.", suffix=output_path.suffix, dir=str(output_path.parent))
    os.close(fd)
    temporary = Path(raw)
    try:
        candidate.save(str(temporary), encoding="utf-8")
        _sync_file(temporary)
        validation = validate_v238_candidate(source_path, temporary)
        os.replace(temporary, output_path)
        _sync_dir(output_path.parent)
        if context.get("fault_injection") == "after_output_rename":
            raise RuntimeError("V238_FAULT_AFTER_OUTPUT_RENAME")
        marker_root = context.get("stage_completion_root")
        if marker_root:
            marker_dir = Path(marker_root)
            marker_dir.mkdir(parents=True, exist_ok=True)
            marker = marker_dir / f"{output_path.name}.complete.json"
            _atomic_json(marker, {"state": "COMPLETE", "sha256": _sha256(output_path), "validation": validation})
        return validation
    finally:
        temporary.unlink(missing_ok=True)


_FULL_METRIC_KEYS = (
    "v226_primary_requests", "v226_physical_attempts", "v226_model_generation_attempts",
    "v226_successful_generations", "v226_retries", "v226_transport_failures",
    "v226_schema_failures", "v226_validation_failures", "v226_prompt_tokens",
    "v226_completion_tokens", "v226_elapsed_seconds", "v238_semantic_requests",
    "v238_model_attempts", "v238_replay_reads", "v238_deterministic_counters",
    "checkpoint_created", "checkpoint_reused", "total_elapsed_seconds",
)


def _full_metric_map(base: Mapping[str, Any], provider_delta: Mapping[str, Any], counters: Mapping[str, Any], elapsed: float) -> dict[str, int | float]:
    base = base if isinstance(base, Mapping) else {}
    provider_delta = provider_delta if isinstance(provider_delta, Mapping) else {}
    result: dict[str, int | float] = {key: 0 for key in _FULL_METRIC_KEYS}
    result.update({
        "v226_primary_requests": int(base.get("primary_requests", base.get("provider_requests", base.get("calls", 0))) or 0),
        "v226_physical_attempts": int(base.get("physical_attempts", base.get("physical_client_calls", base.get("calls", 0))) or 0),
        "v226_model_generation_attempts": int(base.get("model_generation_attempts", base.get("model_generation_calls", base.get("ollama_calls", 0))) or 0),
        "v226_successful_generations": int(base.get("successful_generations", base.get("resolved", 0)) or 0),
        "v226_retries": int(base.get("retries", base.get("retry_calls", 0)) or 0),
        "v226_transport_failures": int(base.get("transport_failures", 0) or 0),
        "v226_schema_failures": int(base.get("schema_failures", 0) or 0),
        "v226_validation_failures": int(base.get("validation_failures", base.get("failed", 0)) or 0),
        "v226_prompt_tokens": int(base.get("prompt_tokens", 0) or 0),
        "v226_completion_tokens": int(base.get("completion_tokens", 0) or 0),
        "v226_elapsed_seconds": float(base.get("elapsed_seconds", base.get("elapsed_client_seconds", 0.0)) or 0.0),
        "v238_semantic_requests": int(provider_delta.get("provider_requests", 0) or 0),
        "v238_model_attempts": int(provider_delta.get("model_generation_calls", 0) or 0),
        "v238_replay_reads": int(provider_delta.get("offline_replay_reads", 0) or 0),
        "v238_deterministic_counters": int(sum(int(value) for value in counters.values() if isinstance(value, (int, float)))),
        "total_elapsed_seconds": float(elapsed),
    })
    return result


def _base_metric_map(summary: Mapping[str, Any] | None) -> dict[str, int | float]:
    """Normalize both the live V226 summary and grouped replay summary."""
    summary = summary if isinstance(summary, Mapping) else {}
    def number(*keys: str) -> int | float:
        for key in keys:
            if isinstance(summary.get(key), (int, float)):
                return summary[key]
        return 0
    return {
        "primary_requests": int(number("primary_requests", "provider_requests", "calls", "valid_attempts")),
        "physical_attempts": int(number("physical_attempts", "physical_client_calls", "calls", "valid_attempts")),
        "model_generation_attempts": int(number("model_generation_attempts", "model_generation_calls", "ollama_calls", "calls")),
        "successful_generations": int(number("successful_generations", "resolved", "valid_attempts")),
        "retries": int(number("retries", "retry_calls", "actual_retry_ollama_calls")),
        "transport_failures": int(number("transport_failures")),
        "schema_failures": int(number("schema_failures")),
        "validation_failures": int(number("validation_failures", "failed")),
        "prompt_tokens": int(number("prompt_tokens")),
        "completion_tokens": int(number("completion_tokens")),
        "elapsed_seconds": float(number("elapsed_seconds", "elapsed_client_seconds")),
    }


def reconcile_atomic_stage_output(source: str | Path, output: str | Path, *, context: Mapping[str, Any]) -> dict[str, Any]:
    """Reconcile an output renamed before its completion marker was written."""
    source_path, output_path = Path(source), Path(output)
    if not output_path.is_file():
        raise ResponseProviderError("V238_STAGE_OUTPUT_MISSING_FOR_RECONCILIATION")
    validation = validate_v238_candidate(source_path, output_path)
    marker_root = context.get("stage_completion_root")
    if not marker_root:
        raise ResponseProviderError("V238_STAGE_COMPLETION_ROOT_REQUIRED")
    marker = Path(marker_root) / f"{output_path.name}.complete.json"
    _atomic_json(marker, {"state": "COMPLETE", "sha256": _sha256(output_path), "validation": validation, "reconciled": True})
    return {"state": "COMPLETE", "reconciled": True, "output_sha256": _sha256(output_path), "validation": validation}


def _plain(value: str) -> str:
    return _TAG_RE.sub("", value or "").replace(r"\N", " ").replace(r"\h", " ").strip()


def _provider(context: Mapping[str, Any]) -> DurableResponseProvider:
    value = context.get("response_provider")
    if not isinstance(value, DurableResponseProvider):
        raise ResponseProviderError("V238_RESPONSE_PROVIDER_REQUIRED")
    return value


def _repair_ownership_whitespace(
    target_text: str, rows: list[Mapping[str, Any]],
) -> list[dict[str, Any]] | None:
    """Reallocate whitespace only when the model preserved all visible atoms.

    Ownership is still validated against the exact target afterwards.  This
    helper only assigns target whitespace to the current run (boundary spaces
    stay with the preceding run), so a model cannot invent, remove, or alter a
    visible non-whitespace character.
    """
    if not rows or any(set(row) != {"text", "owner_segment_id"} for row in rows):
        return None
    texts = [row.get("text") for row in rows]
    owners = [row.get("owner_segment_id") for row in rows]
    if any(not isinstance(text, str) or not text or not isinstance(owner, str) for text, owner in zip(texts, owners)):
        return None
    visible_model = "".join(char for text in texts for char in text if not char.isspace())
    visible_target = "".join(char for char in target_text if not char.isspace())
    if visible_model != visible_target:
        return None
    counts = [sum(not char.isspace() for char in text) for text in texts]
    if any(count <= 0 for count in counts):
        return None
    repaired = [[] for _ in rows]
    run_index = 0
    remaining = counts[0]
    for char in target_text:
        if not char.isspace():
            while remaining == 0 and run_index + 1 < len(counts):
                run_index += 1
                remaining = counts[run_index]
            if remaining == 0:
                return None
            remaining -= 1
        repaired[run_index].append(char)
    if remaining or run_index != len(counts) - 1:
        return None
    return [
        {"text": "".join(text), "owner_segment_id": owner}
        for text, owner in zip(repaired, owners)
    ]


def _ownership_mapping(program: Any, target_text: str, response: Mapping[str, Any]) -> dict[str, Any] | None:
    rows = response.get("ownership_runs")
    if isinstance(rows, list):
        repaired = _repair_ownership_whitespace(target_text, rows)
        return {"ownership_runs": repaired or rows}
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
    ownership_cache: dict[str, dict[str, Any]] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Run the final deterministic layers for one generic ASS event."""
    counters["source_payload"] += 1
    normalized_target, punctuation_changed = preserve_source_punctuation_profile(source_text, target_text)
    # All later envelope/temporal reconstruction paths must consume the same
    # normalized payload; otherwise a bypass branch could reintroduce model-
    # invented punctuation after the base materializer ran.
    target_text = normalized_target
    base = rc4_replace_source_payload(source_text, target_text)
    if base is None:
        raise ResponseProviderError("V238_SOURCE_PAYLOAD_RECONSTRUCTION_FAILED")
    details: dict[str, Any] = {"event_id": event_id, "path": "SOURCE_PAYLOAD"}
    if punctuation_changed:
        details["punctuation_profile"] = "SOURCE_PRESERVED"
    group_probe = getattr(provider, "v238_group_key", None)
    if callable(group_probe) and group_probe(event_id) is None:
        # The canonical base materializer has already produced the V226
        # linguistic payload for ordinary units.  Rebuild its source-owned
        # presentation envelope once so model-returned ASS tags and line
        # breaks cannot leak into the candidate.
        return base, {"event_id": event_id, "path": "BASE_V226_PAYLOAD_REENVELOPED"}
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
    temporal_probe = getattr(provider, "is_temporal_group", None)
    if "\\t(" in source_text and (not callable(temporal_probe) or temporal_probe(event_id)):
        if callable(group_probe) and group_probe(event_id) is None:
            return base, {"event_id": event_id, "path": "DETERMINISTIC_TEMPORAL_PRESERVATION"}
        counters["temporal_transform"] += 1
        temporal, trace = preserve_temporal_transform_envelope(source_text, target_text, base_rebuilder=rc4_replace_source_payload)
        if temporal is None:
            # Modelo não preservou \t( — injeta blocos temporais do source no target
            from v237_temporal_transform import inject_source_temporal_transforms
            injected = inject_source_temporal_transforms(source_text, target_text, base_rebuilder=rc4_replace_source_payload)
            if injected is not None:
                temporal = rc4_replace_source_payload(source_text, injected)
                if temporal is not None:
                    return temporal, {"event_id": event_id, "path": "TEMPORAL_INJECTED_FROM_SOURCE", "trace": trace}
            # Se injeção não foi possível, fallback para base V226
            return base, {"event_id": event_id, "path": "UNPROVEN_TEMPORAL_BASE_FALLBACK", "trace": trace}
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
        visual_group_probe = getattr(provider, "is_visual_group", None)
        if callable(visual_group_probe) and visual_group_probe(event_id):
            reviewed_visual = getattr(provider, "reviewed_visual_envelope", None)
            if callable(reviewed_visual):
                envelope = reviewed_visual(event_id)
                if isinstance(envelope, str):
                    return envelope, {"event_id": event_id, "path": "REVIEWED_VISUAL_ENVELOPE"}
            counters["visual_detector"] += 1
            visual_program, visual_details = extract_visual_glyph_program(
                source_text, program_id=f"event-{event_id}", envelope_id=event_id
            )
            if visual_program is not None and visual_details.get("valid"):
                counters["visual_reconstruction"] += 1
                rendered, trace = reconstruct_visual_glyph_envelope(
                    source_text, _plain(target_text), program=visual_program, base_rebuilder=rc4_replace_source_payload
                )
                if rendered is None:
                    return base, {"event_id": event_id, "path": "REVIEWED_VISUAL_BASE_PRESERVATION"}
                return rendered, {"event_id": event_id, "path": "VISUAL_GLYPH", "trace": trace}
            return base, {"event_id": event_id, "path": "REVIEWED_VISUAL_BASE_PRESERVATION"}
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
                "source_segments": [
                    {"segment_id": segment.segment_id, "source_text": segment.source_text}
                    for segment in program.source_semantic_segments
                ],
                "model": model,
            }
            group_key_fn = getattr(provider, "ownership_group_key", None)
            resolved_group = group_key_fn(request) if callable(group_key_fn) else f"event-{event_id}"
            # An injected canonical unit/group resolver may explicitly state
            # that this styled event has no V238 semantic ambiguity. In that
            # case the source-payload seam is the deterministic authority and
            # no provider request is permitted.
            if resolved_group is None:
                details.update({"path": "DETERMINISTIC_STYLE_PRESERVATION"})
                return base, details
            group_key = str(resolved_group)
            if ownership_cache is not None and group_key in ownership_cache:
                response = ownership_cache[group_key]
            else:
                response = provider.ownership(request, capture_id=f"v238-ownership-{group_key}")
                if ownership_cache is not None:
                    ownership_cache[group_key] = response
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
                # Fallback determinístico em vez de falhar o episódio:
                # modelos pequenos podem não provar ownership completo;
                # base (rc4) preserva texto traduzido com estilo base.
                counters["semantic_ownership_fallback"] = counters.get("semantic_ownership_fallback", 0) + 1
                details.update({"path": "SEMANTIC_OWNERSHIP_FALLBACK_UNPROVEN", "span_issues": span_issues})
                return base, details
            rendered, validation = render_target_ownership(source_text, _plain(target_text), program, mapping)
            if rendered is None or not validation.get("valid"):
                counters["semantic_ownership_fallback"] = counters.get("semantic_ownership_fallback", 0) + 1
                details.update({"path": "SEMANTIC_OWNERSHIP_FALLBACK_VALIDATION", "span_issues": span_issues, "validation": validation})
                return base, details
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
    started = time.perf_counter()
    source_path, output_path = Path(source), Path(output)
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    if output_path.exists():
        raise FileExistsError(output_path)
    provider = _provider(context)
    operation_budget = context.get("operation_budget")
    if operation_budget is not None and callable(getattr(provider, "attach_operation_budget", None)):
        provider.attach_operation_budget(operation_budget, phase="V238_SEMANTIC")
    metrics_before = copy.deepcopy(getattr(provider, "metrics", {}))
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
    # A no-base invocation must not invent units from visible text/style. An
    # explicit caller may provide canonical V226 unit identities; otherwise
    # each event remains its own linguistic unit.
    explicit_units = context.get("linguistic_unit_ids")
    if explicit_units is not None and not isinstance(explicit_units, Mapping):
        raise ResponseProviderError("V238_LINGUISTIC_UNIT_IDENTITIES_INVALID")
    member_ids: dict[str, list[int]] = {}
    for event_index in range(len(source_subs.events)):
        unit_id = str(explicit_units.get(event_index, f"event-{event_index}") if isinstance(explicit_units, Mapping) else f"event-{event_index}")
        member_ids.setdefault(unit_id, []).append(event_index)
    translated_units: dict[str, dict[str, Any]] = {}
    ownership_cache: dict[str, dict[str, Any]] = {}
    for index, (source_line, target_line) in enumerate(zip(source_subs.events, candidate.events)):
        source_text = source_line.text or ""
        if base_translation is None:
            unit_id = str(explicit_units.get(index, f"event-{index}") if isinstance(explicit_units, Mapping) else f"event-{index}")
            response = translated_units.get(unit_id)
            if response is None:
                counters["provider_requests"] += 1
                response = provider.respond({
                    "operation": "v238_linguistic_translation",
                    "unit_id": unit_id,
                    "member_event_ids": member_ids[unit_id],
                    "reason_code": "LINGUISTIC_UNIT_TRANSLATION",
                    "expected_response_schema": "translation|text|ass_text",
                    "text": _plain(source_text),
                    "model": model,
                }, capture_id=f"v238-translation-unit-{unit_id}")
                translated_units[unit_id] = response
            replay_ass_text = response.get("ass_text")
            target = response.get("translation", response.get("text", _plain(source_text)))
            if not isinstance(target, str):
                raise ResponseProviderError("V238_TRANSLATION_MISSING")
            rendered, _details = _render_event(source_text, target, event_id=index, provider=provider, model=model, counters=counters, reviewed_envelope=replay_ass_text, ownership_cache=ownership_cache)
            if replay_ass_text is not None:
                if not isinstance(replay_ass_text, str) or _plain(replay_ass_text) != _plain(target):
                    raise ResponseProviderError("V238_REPLAY_ASS_PAYLOAD_MISMATCH")
                # An explicit OFFLINE_REPLAY provider may carry a reviewed
                # envelope.  It is accepted only after the generic renderer
                # and payload identity checks above have run.
                rendered = replay_ass_text
            target_line.text = rendered
        else:
            # Base V226 already supplies linguistic materialization. V238
            # transforms are deterministic; no artificial provider call.
            target_line.text, _details = _render_event(source_text, target_line.text or "", event_id=index, provider=provider, model=model, counters=counters, ownership_cache=ownership_cache)
    validation = _atomic_stage_write(candidate, output_path, source_path, context)
    provider_metrics = copy.deepcopy(getattr(provider, "metrics", {}))
    metrics_delta = _metric_delta(metrics_before, provider_metrics)
    base_summary = context.get("base_materializer_summary")
    base_metrics: dict[str, Any] = _base_metric_map(base_summary)
    if isinstance(base_summary, Mapping) and isinstance(base_summary.get("metrics"), Mapping):
        base_metrics.update(_base_metric_map(base_summary["metrics"]))
    v238_metrics = {key: value for key, value in metrics_delta.items() if isinstance(value, (int, float))}
    v238_elapsed = time.perf_counter() - started
    aggregated_metrics = _full_metric_map(base_metrics, metrics_delta, counters, v238_elapsed)
    if isinstance(base_summary, Mapping):
        aggregated_metrics["checkpoint_created"] = int(base_summary.get("checkpoint_created", 0) or 0)
        aggregated_metrics["checkpoint_reused"] = int(base_summary.get("checkpoint_reused", 0) or 0)
    return {
        **validation,
        "response_mode": mode,
        "provider_calls": len(provider.calls),
        "component_calls": counters,
        "publishable": False,
        "durable_intermediate": True,
        "metrics": provider_metrics,
        "metrics_before": metrics_before,
        "metrics_after": provider_metrics,
        "metrics_delta": metrics_delta,
        "base_materializer_metrics": base_metrics,
        "v238_metrics": v238_metrics,
        "aggregated_metrics": aggregated_metrics,
        "model_calls": int(aggregated_metrics.get("v226_model_generation_attempts", 0)) + int(aggregated_metrics.get("v238_model_attempts", 0)),
        "network_calls": int(base_metrics.get("network_calls", 0) or 0) + int(metrics_delta.get("application_network_calls", 0) or 0),
        "v238_wall_seconds": v238_elapsed,
        "metrics_measurements": {
            "v238_wall_seconds": {"value": v238_elapsed, "measurement_status": "MEASURED", "measurement_source": "v238_full_translation_stage"},
            "provider_metrics": {"value": provider_metrics, "measurement_status": "MEASURED", "measurement_source": "DurableResponseProvider"},
        },
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


__all__ = ["PIPELINE_ID", "STAGE_ID", "execute_v238_stage", "reconcile_atomic_stage_output", "validate_v238_candidate"]
