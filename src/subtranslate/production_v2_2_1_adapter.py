"""V2.2.1 candidate adapter for complex ASS structure.

The frozen V2.1.3 engine remains the linguistic/retry/validation baseline.
This module only prepares safe semantic views for that engine and restores
ASS-only structure after a response.  V2.2.0 is deliberately not modified.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any

import pysubs2

from pipeline_v2_1_3 import (
    CleanSegment,
    CRITICAL_FLAGS,
    Event,
    TAG_RE,
    Unit,
    Config,
    build_sign_groups,
    _raw_index_for_visible_offset,
    _safe_inline_boundary,
    _visible_length,
    content_flags,
    is_multi_speaker,
    load_events,
    reconstruct_event,
    validate_inline_tags,
    validate_structure,
    write_ass,
)
from production_v2_1_3_adapter import APPROVED_CONFIG, APPROVED_MODEL, _fsync_file, _merged_glossary
from production_v2_2_0_adapter import MemoryRunner
from translation_memory import TranslationMemory


APPROVED_PIPELINE = "v2_2_1"
DRAWING_TAG_RE = re.compile(r"\\p(?P<mode>\d+)", re.I)
DRAWING_BODY_RE = re.compile(r"^[mnlbspcMNLBSPCqQ0-9eE+\-.,\s]*$")


class _V221MemoryProxy:
    """Keep V2.2.0 untouched while stamping usage with V2.2.1 provenance."""

    def __init__(self, memory: TranslationMemory):
        self._memory = memory

    def retrieve(self, *args, **kwargs):
        return self._memory.retrieve(*args, **kwargs)

    def safe_prompt_context(self, *args, **kwargs):
        return self._memory.safe_prompt_context(*args, **kwargs)

    def record_usage(self, **kwargs):
        kwargs["pipeline_version"] = APPROVED_PIPELINE
        return self._memory.record_usage(**kwargs)


def _structural_shape(event: Event) -> tuple[Any, ...]:
    """Shape used to prove a repeated sign has the same reconstruction contract."""
    tag_shape = tuple(re.sub(r"[-+]?\d+(?:\.\d+)?", "#", tag) for tag in TAG_RE.findall(event.original_text or ""))
    segment_shape = tuple(bool(segment.clean_text) for segment in event.segments)
    return (len(event.line_break_boundaries), segment_shape, tag_shape)


def _tag_end(text: str, start: int) -> int | None:
    if start >= len(text) or text[start] != "{":
        return None
    end = text.find("}", start + 1)
    return end + 1 if end >= 0 else None


def _scan_ass_parts(text: str) -> dict[str, Any]:
    """Split visible ASS content by deterministic drawing mode.

    Tags, breaks and drawing commands remain source data.  Only contiguous
    non-drawing text is exposed as a linguistic slot for the model.
    """
    chunks: list[dict[str, Any]] = []
    mode = 0
    index = 0
    while index < len(text):
        end = _tag_end(text, index)
        if end is not None:
            tag = text[index:end]
            match = DRAWING_TAG_RE.search(tag)
            if match:
                mode = int(match.group("mode"))
            chunks.append({"kind": "tag", "text": tag, "mode": mode})
            index = end
            continue
        if text.startswith(r"\N", index):
            chunks.append({"kind": "break", "text": r"\N", "mode": mode})
            index += 2
            continue
        end = index + 1
        while end < len(text) and text[end] != "{" and not text.startswith(r"\N", end):
            end += 1
        kind = "drawing" if mode > 0 else "linguistic"
        chunks.append({"kind": kind, "text": text[index:end], "mode": mode})
        index = end
    linguistic = [chunk["text"] for chunk in chunks if chunk["kind"] == "linguistic"]
    drawing = [chunk["text"] for chunk in chunks if chunk["kind"] == "drawing"]
    drawing_text = "".join(drawing)
    visible = "".join(chunk["text"] for chunk in chunks if chunk["kind"] in {"drawing", "linguistic"})
    drawing_only = bool(drawing) and not any(chunk["kind"] == "linguistic" and chunk["text"].strip() for chunk in chunks)
    drawing_valid = bool(drawing_text.strip()) and bool(DRAWING_BODY_RE.fullmatch(drawing_text.strip()))
    return {
        "chunks": chunks,
        "linguistic_chunks": linguistic,
        "linguistic_text": " ".join(part.strip() for part in linguistic if part.strip()),
        "drawing_text": drawing_text,
        "drawing_only": drawing_only and drawing_valid,
        "mixed": bool(drawing) and any(part.strip() for part in linguistic),
        "drawing_present": bool(drawing),
        "vector_signature": drawing_text,
        "visible": visible,
    }


def analyze_vector_event(event: Event) -> dict[str, Any]:
    info = _scan_ass_parts(event.original_text or "")
    return {
        **info,
        "event_id": event.id,
        "line_break_count": len(event.line_break_boundaries),
        "empty_visual_segments": bool(event.line_break_boundaries) and any(not segment.clean_text for segment in event.segments),
        "source_segments": [segment.clean_text for segment in event.segments],
    }


def _clone_linguistic_event(event: Event, text: str) -> Event:
    clean = " ".join(text.split())
    return replace(
        event,
        original_text=clean,
        visible_text=clean,
        clean_text=clean,
        segments=[CleanSegment(0, clean, clean, False)],
        tag_anchors=[],
        line_break_boundaries=[],
        has_positioning=False,
    )


def _split_translation(text: str, source_parts: list[str]) -> list[str] | None:
    """Map one model string to source linguistic slots without asking it for breaks."""
    source_parts = [part.strip() for part in source_parts if part.strip()]
    if not source_parts:
        return []
    if len(source_parts) == 1:
        return [text.strip()]
    remaining = text.strip()
    remaining_source = sum(len(part) for part in source_parts)
    result: list[str] = []
    for index, source in enumerate(source_parts[:-1]):
        remaining_source = max(1, remaining_source)
        ratio = len(source) / remaining_source
        # Prefer a lexical boundary.  If no such boundary exists, this is a
        # genuine unsafe reconstruction and must fail closed.
        plain = TAG_RE.sub("", remaining).replace(r"\N", " ")
        candidates = [pos for pos in range(1, len(plain)) if plain[pos - 1].isspace() or plain[pos - 1] in ",.!?;:"]
        if not candidates:
            return None
        ideal = max(1, min(len(plain) - 1, round(ratio * len(plain))))
        cut = min(candidates, key=lambda pos: abs(pos - ideal))
        result.append(plain[:cut].strip())
        remaining = plain[cut:].strip()
        remaining_source -= len(source)
    result.append(remaining)
    return result


def _restore_tags(text: str, original: Event) -> str | None:
    base = text
    source_visible_len = max(1, _visible_length(original.clean_text))
    for anchor in sorted(original.tag_anchors, key=lambda item: item["position"], reverse=True):
        desired = _visible_length(base) if anchor["position"] >= source_visible_len else min(_visible_length(base), max(0, anchor["position"]))
        safe = _safe_inline_boundary(base, desired)
        if safe is None:
            return None
        raw_position = _raw_index_for_visible_offset(base, safe)
        base = base[:raw_position] + anchor["tag"] + base[raw_position:]
    return base


def _restore_tags_by_source_segment(text: str, original: Event) -> str | None:
    r"""Restore tags at the source segment, including runs of empty segments.

    A plain visible-offset anchor is insufficient for ``A\N\N{tag}B``: the
    translated text may have different word lengths and the tag belongs to B,
    not to the nearest lexical boundary in A.  Track the source break ordinal
    and the offset inside that source segment instead.
    """
    parts = text.split(r"\N")
    raw_cursor = 0
    for anchor in original.tag_anchors:
        tag = anchor["tag"]
        raw_position = original.original_text.find(tag, raw_cursor)
        if raw_position < 0:
            return None
        raw_cursor = raw_position + len(tag)
        prefix = original.original_text[:raw_position]
        segment_index = prefix.count(r"\N")
        segment_start = prefix.rfind(r"\N") + 2 if r"\N" in prefix else 0
        source_segment = original.original_text[segment_start:raw_position]
        source_offset = _visible_length(source_segment)
        if segment_index >= len(parts):
            return None
        target = parts[segment_index]
        safe = _safe_inline_boundary(target, min(source_offset, _visible_length(target)))
        if safe is None:
            return None
        raw_target = _raw_index_for_visible_offset(target, safe)
        parts[segment_index] = target[:raw_target] + tag + target[raw_target:]
    return r"\N".join(parts)


def _restore_empty_breaks(original: Event, translated: str) -> str | None:
    if r"\N" in translated or "{" in translated or "}" in translated or any(token in translated for token in ("§T", "§N", "§G")):
        return None
    source_parts = [segment.clean_text for segment in original.segments]
    nonempty = [part for part in source_parts if part]
    translated_parts = _split_translation(translated, nonempty)
    if translated_parts is None:
        return None
    iterator = iter(translated_parts)
    assembled: list[str] = []
    for index, source in enumerate(source_parts):
        assembled.append(next(iterator) if source else "")
        if index < len(source_parts) - 1:
            assembled.append(r"\N")
    restored = _restore_tags_by_source_segment("".join(assembled), original)
    if restored is None or restored.count(r"\N") != len(original.line_break_boundaries):
        return None
    return restored


def _restore_mixed(original: Event, info: dict[str, Any], translated: str) -> str | None:
    source_parts = [part for part in info["linguistic_chunks"] if part.strip()]
    translated_parts = _split_translation(translated, source_parts)
    if translated_parts is None:
        return None
    iterator = iter(translated_parts)
    output: list[str] = []
    for chunk in info["chunks"]:
        if chunk["kind"] == "linguistic":
            source = chunk["text"]
            output.append(next(iterator) if source.strip() else source)
        else:
            output.append(chunk["text"])
    merged = "".join(output)
    original_signature = _scan_ass_parts(original.original_text)["vector_signature"]
    merged_signature = _scan_ass_parts(merged)["vector_signature"]
    if original_signature != merged_signature:
        return None
    if merged.count(r"\N") != original.original_text.count(r"\N"):
        return None
    return merged


def _bound_units(units: list[Unit], config: Config) -> tuple[list[Unit], dict[str, int]]:
    """Bound grouped signs at event and configured prompt-character budgets."""
    max_events = max(1, int(config.batch_target_size))
    max_chars = max(600, min(int(config.context_max_chars), int(config.num_ctx * 3)))
    bounded: list[Unit] = []
    original_largest = max((len(unit.events) for unit in units), default=0)
    for unit in units:
        has_sensitive_sign = unit.grouped_sign and any(
            bool(re.search(r"[()\[\]\"]", event.clean_text or "")) for event in unit.events
        )
        if not unit.grouped_sign or (len(unit.events) <= max_events and not has_sensitive_sign):
            bounded.append(unit)
            continue
        current: list[Event] = []
        chars = 0
        part = 0
        for event in unit.events:
            event_chars = len(event.clean_text)
            # Delimiter-bearing sign text is a protected construct.  Keep it
            # as an event-boundary unit so a batch response cannot make one
            # sign's quote/parenthesis contract depend on neighbouring frames.
            # This is generic and does not change the ASS envelope or the
            # validator; it only chooses a safer LLM batch boundary.
            delimiter_sensitive = event.classification == "SIGN_OR_SCREEN_TEXT" and bool(re.search(r"[()\[\]\"]", event.clean_text or ""))
            if delimiter_sensitive:
                if current:
                    bounded.append(Unit(f"{unit.unit_id}-part-{part}", current, True))
                    part += 1
                    current, chars = [], 0
                bounded.append(Unit(f"{unit.unit_id}-protected-{part}", [event], True))
                part += 1
                continue
            if current and (len(current) >= max_events or chars + event_chars > max_chars):
                bounded.append(Unit(f"{unit.unit_id}-part-{part}", current, True))
                part += 1
                current, chars = [], 0
            current.append(event)
            chars += event_chars
        if current:
            bounded.append(Unit(f"{unit.unit_id}-part-{part}", current, True))
    return bounded, {
        "largest_bounded_input_sign_group": original_largest,
        "largest_bounded_processing_group": max((len(unit.events) for unit in bounded if unit.grouped_sign), default=0),
        "bounded_processing_groups": sum(unit.grouped_sign for unit in bounded),
        "bounded_max_events": max_events,
        "bounded_max_chars": max_chars,
    }


class V221MemoryRunner(MemoryRunner):
    """MemoryRunner with an ASS structural envelope outside the frozen engine."""

    def __init__(self, events, profile, config, glossary, memory, anime_series_id, episode_id, job_id):
        self.v221_original_events = list(events)
        raw_units = build_sign_groups(events, config.enable_sign_grouping)
        raw_sign_largest = max((len(unit.events) for unit in raw_units if unit.grouped_sign), default=0)
        raw_sign_groups = sum(unit.grouped_sign for unit in raw_units)
        self.v221_transformations: dict[int, dict[str, Any]] = {}
        prepared: list[Event] = []
        vector_only = mixed = empty_break = 0
        deduplicated_events = deduplicated_blocks = 0
        last_sign_key: tuple[Any, ...] | None = None
        last_sign_index: int | None = None
        last_sign_representative: int | None = None
        last_dedup_representative: int | None = None
        for event in events:
            info = analyze_vector_event(event)
            sign_key = (event.clean_text, event.style, event.name, event.effect, _structural_shape(event))
            contiguous_sign = (
                event.classification == "SIGN_OR_SCREEN_TEXT"
                and not info["drawing_present"]
                and not info["empty_visual_segments"]
                and bool(event.clean_text.strip())
                and last_sign_key == sign_key
                and last_sign_index is not None
                and event.original_index == last_sign_index + 1
            )
            if contiguous_sign and last_sign_representative is not None:
                self.v221_transformations[event.id] = {
                    "kind": "DEDUPLICATED_SIGN",
                    "representative_id": last_sign_representative,
                    "info": info,
                    "reason": "contiguous identical sign text/style/effect with no intervening semantic event",
                }
                prepared.append(replace(event, classification="TECHNICAL_OR_EMPTY", classification_reason="V221_DEDUPLICATED_SIGN", clean_text="", visible_text="", segments=[CleanSegment(0, "", "", False)], line_break_boundaries=[]))
                deduplicated_events += 1
                if last_dedup_representative != last_sign_representative:
                    deduplicated_blocks += 1
                    last_dedup_representative = last_sign_representative
                last_sign_index = event.original_index
                last_sign_key = sign_key
                continue
            if info["drawing_only"]:
                self.v221_transformations[event.id] = {"kind": "VECTOR_DRAWING", "info": info}
                prepared.append(replace(event, classification="TECHNICAL_OR_EMPTY", classification_reason="V221_VECTOR_DRAWING", clean_text="", visible_text="", segments=[CleanSegment(0, "", "", False)], line_break_boundaries=[]))
                vector_only += 1
            elif info["mixed"]:
                self.v221_transformations[event.id] = {"kind": "MIXED_VECTOR_TEXT", "info": info}
                prepared.append(_clone_linguistic_event(event, info["linguistic_text"]))
                mixed += 1
            elif info["empty_visual_segments"]:
                self.v221_transformations[event.id] = {"kind": "EMPTY_VISUAL_SEGMENTS", "info": info}
                prepared.append(_clone_linguistic_event(event, " ".join(part for part in info["source_segments"] if part)))
                empty_break += 1
            else:
                prepared.append(event)
            if event.classification == "SIGN_OR_SCREEN_TEXT" and not info["drawing_present"] and not info["empty_visual_segments"] and event.clean_text.strip():
                last_sign_key = sign_key
                last_sign_index = event.original_index
                last_sign_representative = event.id
                last_dedup_representative = None
            else:
                last_sign_key = None
                last_sign_index = None
                last_sign_representative = None
                last_dedup_representative = None
        self.v221_prepared_events = prepared
        super().__init__(prepared, profile, config, glossary, _V221MemoryProxy(memory), anime_series_id, episode_id, job_id)
        self.units, bound_metrics = _bound_units(self.units, config)
        delimiter_sensitive_units = sum(
            1 for unit in self.units
            if unit.grouped_sign and len(unit.events) == 1
            and bool(re.search(r"[()\[\]\"]", unit.events[0].clean_text or ""))
        )
        self.v221_metrics = {
            "vector_only_events": vector_only,
            "mixed_vector_text_events": mixed,
            "empty_visual_segment_events": empty_break,
            "linguistic_events": len(events) - vector_only,
            "vector_drawing_memory_ineligible": vector_only,
            "unique_deduplicated_sign_texts": 0,
            "deduplicated_sign_events": deduplicated_events,
            "deduplicated_sign_blocks": deduplicated_blocks,
            "noncritical_sign_retries_suppressed": 0,
            "delimiter_sensitive_sign_units": delimiter_sensitive_units,
            "largest_original_sign_group": raw_sign_largest,
            "largest_untransformed_sign_group": raw_sign_largest,
            "largest_bounded_input_sign_group": bound_metrics.get("largest_bounded_input_sign_group", 0),
            "untransformed_grouped_sign_units": raw_sign_groups,
            **bound_metrics,
        }

    def run(self) -> dict[str, Any]:
        summary = super().run()
        result_by_id = {item["id"]: item for item in summary["results"]}
        custom_failures: list[str] = []
        for original in self.v221_original_events:
            transform = self.v221_transformations.get(original.id)
            item = result_by_id[original.id]
            if not transform:
                continue
            kind = transform["kind"]
            if kind == "VECTOR_DRAWING":
                item["status"] = "resolved"
                item["final_text"] = original.original_text
                item["final_model"] = "vector-preserve"
                item.setdefault("flags", []).append("VECTOR_DRAWING_PRESERVED")
                continue
            if kind == "DEDUPLICATED_SIGN":
                representative = result_by_id.get(transform["representative_id"])
                if not representative or representative.get("status") != "resolved" or not representative.get("final_text"):
                    item["status"] = "failed"
                    item["failure_reason"] = "V221_DEDUPLICATED_REPRESENTATIVE_FAILED"
                    item.setdefault("flags", []).append("V221_DEDUPLICATED_REPRESENTATIVE_FAILED")
                    custom_failures.append("V221_DEDUPLICATED_REPRESENTATIVE_FAILED")
                    continue
                linguistic = TAG_RE.sub("", representative["final_text"]).replace(r"\N", " ").strip()
                rebuilt, rebuild_flags = reconstruct_event(original, {"text": linguistic})
                if rebuild_flags:
                    item["status"] = "failed"
                    item["failure_reason"] = "; ".join(rebuild_flags)
                    item.setdefault("flags", []).extend(rebuild_flags)
                    custom_failures.extend(rebuild_flags)
                else:
                    item["status"] = "resolved"
                    item["final_text"] = rebuilt
                    item["final_model"] = "deduplicated-sign-map"
                    item.setdefault("flags", []).append("DEDUPLICATED_SIGN_MAPPED")
                continue
            if item["status"] != "resolved" or not item.get("final_text"):
                continue
            restored = _restore_empty_breaks(original, item["final_text"]) if kind == "EMPTY_VISUAL_SEGMENTS" else _restore_mixed(original, transform["info"], item["final_text"])
            if restored is None:
                item["status"] = "failed"
                item["failure_reason"] = "V221_STRUCTURAL_RECONSTRUCTION_FAILURE"
                item.setdefault("flags", []).append("V221_STRUCTURAL_RECONSTRUCTION_FAILURE")
                custom_failures.append("V221_STRUCTURAL_RECONSTRUCTION_FAILURE")
                continue
            item["final_text"] = restored
            item.setdefault("flags", []).append("V221_STRUCTURAL_RESTORED")
            if kind == "EMPTY_VISUAL_SEGMENTS" and restored.count(r"\N") != len(original.line_break_boundaries):
                item["status"] = "failed"
                item.setdefault("flags", []).append("LINE_BREAK_COUNT_MISMATCH")
                custom_failures.append("LINE_BREAK_COUNT_MISMATCH")
            if kind == "MIXED_VECTOR_TEXT" and _scan_ass_parts(restored)["vector_signature"] != transform["info"]["vector_signature"]:
                item["status"] = "failed"
                item.setdefault("flags", []).append("V221_VECTOR_SIGNATURE_MISMATCH")
                custom_failures.append("V221_VECTOR_SIGNATURE_MISMATCH")
        flags = Counter(flag.split(":", 1)[0] for item in result_by_id.values() for flag in item.get("flags", []))
        critical = set(summary.get("critical_flags", [])) | set(custom_failures)
        # Custom structural failures are adapter-level critical states, not a
        # change to the frozen engine's CRITICAL_FLAGS set.
        summary["results"] = [result_by_id[event.id] for event in self.v221_original_events]
        summary["events"] = len(self.v221_original_events)
        summary["resolved"] = sum(item["status"] == "resolved" for item in result_by_id.values())
        summary["failed"] = len(result_by_id) - summary["resolved"]
        summary["eligible"] = summary["failed"] == 0
        summary["eligible_experimental"] = summary["eligible"] and not critical
        summary["flags"] = dict(flags)
        summary["critical_flags"] = sorted(critical)
        summary["classifications"] = dict(Counter(event.classification for event in self.v221_original_events))
        self.v221_metrics["unique_deduplicated_sign_texts"] = self.v221_metrics.get("deduplicated_sign_blocks", 0)
        summary.update(self.v221_metrics)
        summary["structural_failures"] = sorted(set(custom_failures))
        return summary

    def _set_model_result(self, event: Event, response: dict[str, Any], model: str) -> bool:
        """Keep non-critical sign diagnostics from consuming structural retry budget.

        The frozen runner deliberately retries dictionary-backed untranslated
        tokens.  That is appropriate for dialogue, but a large animated sign
        block can contain many legitimate English/proper-name tokens.  In a
        complex ASS release those optional sign retries can exhaust the shared
        finite budget before a later delimiter failure is reached.  V2.2.1
        still records the flags and never suppresses a critical structural or
        high-confidence dialogue result; it only treats the two non-critical
        English-audit flags on a sign as diagnostic after the first valid
        structural response.
        """
        valid = super()._set_model_result(event, response, model)
        if not valid:
            return valid
        result = self.results[event.id]
        if event.classification == "SIGN_OR_SCREEN_TEXT" and not (set(result.flags) & CRITICAL_FLAGS):
            if result.retry_recommended:
                result.retry_recommended = False
                self.v221_metrics["noncritical_sign_retries_suppressed"] = self.v221_metrics.get("noncritical_sign_retries_suppressed", 0) + 1
        return valid


def _config(subtitle_path: Path, folder_glossary: dict[str, str] | None) -> tuple[Config, dict[str, str]]:
    ollama_url = os.environ.get("TRANSLATOR_OLLAMA_URL", "").strip()
    model = os.environ.get("TRANSLATOR_OLLAMA_MODEL", APPROVED_MODEL).strip()
    if not ollama_url:
        raise RuntimeError("TRANSLATOR_OLLAMA_URL não configurada para V2.2.1")
    if model != APPROVED_MODEL:
        raise RuntimeError(f"V2.2.1 exige {APPROVED_MODEL}; modelo configurado: {model or '<vazio>'}")
    values = dict(APPROVED_CONFIG)
    values.update({
        "ollama_url": ollama_url,
        "model": model,
        "series_title": subtitle_path.parent.name,
        "episode_title": subtitle_path.stem,
        "glossary_path": str(Path(__file__).with_name("glossaries") / "v2_1_2_glossary.json"),
        "english_dictionary_path": "/nonexistent/american-english",
    })
    if os.environ.get("TRANSLATOR_OLLAMA_TIMEOUT"):
        values["timeout_seconds"] = float(os.environ["TRANSLATOR_OLLAMA_TIMEOUT"])
    if os.environ.get("V213_RETRY_BUDGET_CALLS"):
        values["retry_budget_calls"] = int(os.environ["V213_RETRY_BUDGET_CALLS"])
    if os.environ.get("V213_DIAGNOSTIC_CAPTURE"):
        values["diagnostic_capture"] = os.environ["V213_DIAGNOSTIC_CAPTURE"].lower() in {"1", "true", "yes"}
    if os.environ.get("V213_HARD_STOP_CALLS"):
        values["diagnostic_hard_stop_calls"] = int(os.environ["V213_HARD_STOP_CALLS"])
    return Config(**values), _merged_glossary(folder_glossary)


def translate_subtitle_file_v2_2_1(
    subtitle_path: Path,
    output_path: Path,
    glossary: dict[str, str] | None = None,
    *,
    memory_db_root: str | Path | None = None,
    anime_series_id: int | None = None,
    episode_id: int | None = None,
    job_id: str | None = None,
) -> dict[str, Any]:
    if subtitle_path.suffix.lower() not in {".ass", ".ssa"}:
        raise RuntimeError("V2.2.1 aceita somente ASS/SSA; saída não publicada")
    if output_path.exists():
        raise FileExistsError(f"a saída final já existe: {output_path.name}")
    started = time.perf_counter()
    config, merged_glossary = _config(subtitle_path, glossary)
    memory_root = Path(memory_db_root or os.environ.get("ANIME_SUBTITLE_LIBRARY_ROOT", "/app/state/anime-subtitle-library"))
    memory = TranslationMemory(memory_root)
    build = memory.sync_approved()
    original, events, profile = load_events(subtitle_path, merged_glossary)
    effective_job = job_id or f"v221-{output_path.name}-{int(time.time() * 1000)}"
    runner = V221MemoryRunner(events, profile, config, merged_glossary, memory, anime_series_id, episode_id, effective_job)
    summary = runner.run()
    summary["memory_build"] = build
    summary["pipeline"] = APPROVED_PIPELINE
    summary["model"] = config.model
    summary["profile_fingerprint"] = profile.get("profile_fingerprint")
    if not summary.get("eligible_experimental"):
        failure_samples = [
            {"id": item.get("id"), "status": item.get("status"), "flags": item.get("flags", []), "failure_reason": item.get("failure_reason", ""), "retry_count": item.get("retry_count", 0), "final_model": item.get("final_model", "")}
            for item in summary.get("results", []) if item.get("status") != "resolved"
        ][:120]
        raise RuntimeError(json.dumps({
            "reason": "v2_2_1_not_eligible",
            "resolved": summary.get("resolved"),
            "events": summary.get("events"),
            "critical_flags": summary.get("critical_flags", []),
            "flags": summary.get("flags", {}),
            "structural_failures": summary.get("structural_failures", []),
            "retry_budget": summary.get("retry_budget", {}),
            "failure_samples": failure_samples,
        }, ensure_ascii=False))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_tmp = tempfile.mkstemp(prefix=f".{output_path.name}.v2_2_1-", suffix=output_path.suffix, dir=str(output_path.parent)); os.close(fd)
    tmp_path = Path(raw_tmp)
    try:
        write_ass(original, events, summary, tmp_path)
        candidate = pysubs2.load(str(tmp_path))
        validation = validate_structure(original, candidate, {event.original_index for event in events}, {event.original_index for event in events if is_multi_speaker(event)})
        if not validation.get("valid"):
            raise RuntimeError(json.dumps({"reason": "v2_2_1_structure_invalid", "issues": validation.get("issues", [])[:30]}, ensure_ascii=False))
        if output_path.exists():
            raise FileExistsError(f"a saída final apareceu durante o job: {output_path.name}")
        _fsync_file(tmp_path); os.replace(tmp_path, output_path)
        try:
            directory_fd = os.open(output_path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    result = {key: summary.get(key) for key in (
        "events", "linguistic_events", "vector_only_events", "mixed_vector_text_events", "empty_visual_segment_events",
        "resolved", "failed", "units", "grouped_sign_units", "largest_original_sign_group", "largest_untransformed_sign_group", "untransformed_grouped_sign_units", "largest_bounded_input_sign_group", "largest_bounded_processing_group", "deduplicated_sign_events", "deduplicated_sign_blocks",
        "bounded_processing_groups", "unique_deduplicated_sign_texts", "total_ollama_calls", "initial_ollama_calls",
        "actual_retry_ollama_calls", "events_retried", "flags", "critical_flags", "profile_fingerprint", "structural_failures",
        "memory_candidates_found", "memory_items_used", "memory_exact_hits", "memory_fuzzy_hits", "memory_conflicts",
        "memory_misses", "memory_scope", "memory_lookup_ms", "memory_build", "memory_event_ids", "retry_budget",
    )}
    result.update({"pipeline": APPROVED_PIPELINE, "model": config.model, "calls": summary.get("total_ollama_calls", 0), "retry_calls": summary.get("actual_retry_ollama_calls", 0), "elapsed_client_seconds": round(time.perf_counter() - started, 3), "output": output_path.name})
    print("V2_2_1_SUMMARY " + json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result


translate_subtitle_file = translate_subtitle_file_v2_2_1
