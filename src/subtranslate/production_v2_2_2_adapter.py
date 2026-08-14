"""V2.2.2 candidate: safe ASS line-break anchors over the V2.2.1 adapter.

This module is deliberately separate from V2.2.1.  It composes the existing
vector/sign/memory handling and changes only the external reconstruction of
visual breaks.  The frozen V2.1.3 validator remains strict and unchanged.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

import pysubs2

from pipeline_v2_1_3 import (
    CRITICAL_FLAGS,
    TAG_RE,
    Config,
    Event,
    _raw_index_for_visible_offset,
    _safe_inline_boundary,
    _visible_length,
    content_flags,
    is_multi_speaker,
    load_events,
    normalize_delimiter_style,
    normalize_idiomatic_output,
    reconstruct_event,
    validate_inline_tags,
    validate_structure,
    write_ass,
    _word_char,
)
from production_v2_1_3_adapter import APPROVED_CONFIG, APPROVED_MODEL, _fsync_file, _merged_glossary
from production_v2_2_0_adapter import MemoryRunner
from production_v2_2_1_adapter import (
    APPROVED_PIPELINE as V221_PIPELINE,
    V221MemoryRunner,
    _restore_tags_by_source_segment,
    _restore_mixed,
    _scan_ass_parts,
    _structural_shape,
    analyze_vector_event,
    _bound_units,
    _clone_linguistic_event,
    _tag_end,
    DRAWING_TAG_RE,
    DRAWING_BODY_RE,
    _V221MemoryProxy,
)
from translation_memory import TranslationMemory


APPROVED_PIPELINE = "v2_2_2"


def _lexical_boundary(text: str, index: int) -> bool:
    """Return whether ``index`` is a safe boundary in Unicode lexical text."""
    if index <= 0 or index >= len(text):
        return True
    left, right = text[index - 1], text[index]
    if _word_char(left) and _word_char(right):
        return False
    # Apostrophes and hyphens inside a lexical token are not line anchors.
    if index >= 2 and index < len(text) - 1 and text[index - 1] in "'’ʼ-‐‑‒–—" and _word_char(text[index - 2]) and _word_char(right):
        return False
    if index > 1 and index + 1 < len(text) and text[index] in "'’ʼ-‐‑‒–—" and _word_char(left) and _word_char(text[index + 1]):
        return False
    return True


def _safe_break_candidates(text: str) -> list[int]:
    """Positions that cannot cut a Unicode lexical word or its connector."""
    return [index for index in range(1, len(text)) if _lexical_boundary(text, index)]


def _split_translation_safe(text: str, source_parts: list[str]) -> list[str] | None:
    """Map one model response to visual slots using lexical-safe anchors.

    Source offsets are used only to rank candidate boundaries.  They never
    become raw offsets in the translated string.  If the target has fewer safe
    boundaries than the source envelope, text is kept intact in the first slot
    and remaining slots stay empty; this preserves content and break count
    without inventing an intra-word split.
    """
    if not isinstance(text, str):
        return None
    plain = text.strip()
    if not plain or r"\N" in plain or "{" in plain or "}" in plain:
        return [""] * len(source_parts) if not plain and source_parts else None
    parts = [part.strip() for part in source_parts]
    count = len(parts)
    if count <= 1:
        return [plain]
    candidates = _safe_break_candidates(plain)
    if not candidates:
        return [plain] + [""] * (count - 1)
    source_lengths = [max(1, len(part)) for part in parts]
    total = sum(source_lengths)
    desired: list[int] = []
    running = 0
    for length in source_lengths[:-1]:
        running += length
        desired.append(round(running / total * len(plain)))
    selected: list[int] = []
    remaining = len(desired)
    for target in desired:
        available = [candidate for candidate in candidates if (not selected or candidate > selected[-1]) and candidate <= len(plain) - (remaining - 1)]
        if not available:
            return [plain] + [""] * (count - 1)
        def cut_score(candidate: int) -> tuple[int, int, int]:
            # Prefer keeping a complete word on the left of a visual break.
            # A candidate immediately after whitespace would otherwise strip
            # the preceding space and move the first character of that word to
            # the next visual line (``Episódio\N7``).
            left, right = plain[candidate - 1], plain[candidate]
            orientation_penalty = 0 if _word_char(left) and right.isspace() else (1 if left.isspace() and _word_char(right) else 2)
            return orientation_penalty, abs(candidate - target), -candidate
        cut = min(available, key=cut_score)
        selected.append(cut)
        remaining -= 1
    result: list[str] = []
    start = 0
    for cut in selected + [len(plain)]:
        result.append(plain[start:cut].strip())
        start = cut
    return result


def _assemble_slots(slots: list[str]) -> str:
    """Reapply exactly one ASS break between every source visual slot."""
    output: list[str] = []
    for index, slot in enumerate(slots):
        output.append(slot)
        if index >= len(slots) - 1:
            continue
        output.append(r"\N")
        # The frozen validator intentionally rejects short lexical fragments
        # adjacent to a break.  A post-break separator is deterministic visual
        # whitespace; it is not delegated to the model and does not alter the
        # number of ASS breaks.
        next_slot = slots[index + 1].lstrip()
        if slot.rstrip() and next_slot and _word_char(slot.rstrip()[-1]) and _word_char(next_slot[0]):
            output.append(" ")
    return "".join(output)


def _has_unsafe_break(text: str) -> bool:
    """Strict lexical check used by the candidate before frozen validation."""
    for match in re.finditer(r"\\N", text):
        before = TAG_RE.sub("", text[:match.start()])
        after = TAG_RE.sub("", text[match.end():])
        if before.endswith("\\N") or after.startswith("\\N"):
            continue
        if before and after and _word_char(before[-1]) and _word_char(after[0]):
            return True
    return False


def _restore_empty_breaks_v222(original: Event, translated: str) -> str | None:
    if any(token in translated for token in (r"\N", "{", "}", "§T", "§N", "§G")):
        return None
    source_parts = [segment.clean_text for segment in original.segments]
    nonempty = [part for part in source_parts if part]
    translated_parts = _split_translation_safe(translated, nonempty)
    if translated_parts is None:
        return None
    iterator = iter(translated_parts)
    slots = [next(iterator) if source else "" for source in source_parts]
    restored = _restore_tags_by_source_segment(_assemble_slots(slots), original)
    if restored is None or restored.count(r"\N") != len(original.line_break_boundaries) or _has_unsafe_break(restored):
        return None
    return restored


def _restore_mixed_v222(original: Event, info: dict[str, Any], translated: str) -> str | None:
    source_parts = [part for part in info["linguistic_chunks"] if part.strip()]
    translated_parts = _split_translation_safe(translated, source_parts)
    if translated_parts is None:
        return None
    iterator = iter(translated_parts)
    output: list[str] = []
    for chunk in info["chunks"]:
        if chunk["kind"] == "linguistic":
            output.append(next(iterator) if chunk["text"].strip() else chunk["text"])
        else:
            output.append(chunk["text"])
    merged = "".join(output)
    if _scan_ass_parts(merged)["vector_signature"] != info["vector_signature"]:
        return None
    if merged.count(r"\N") != original.original_text.count(r"\N") or _has_unsafe_break(merged):
        return None
    return merged


def _reconstruct_line_break_event(event: Event, response: dict[str, Any]) -> tuple[str, list[str]]:
    """Reconstruct a non-empty-break event without source character offsets."""
    if "segments" in response:
        return reconstruct_event(event, response)
    translated = response.get("text", "")
    if not isinstance(translated, str):
        return event.original_text, ["TEXT_NOT_STRING"]
    if any(token in translated for token in (r"\N", "{", "}", "§T", "§N", "§G")):
        return event.original_text, ["MODEL_EMITTED_STRUCTURAL_TOKEN"]
    normalized, delimiter_changed = normalize_delimiter_style(event.clean_text, translated.strip())
    flags: list[str] = ["DELIMITER_STYLE_NORMALIZED"] if delimiter_changed else []
    slots = _split_translation_safe(normalized, [segment.clean_text for segment in event.segments])
    if slots is None:
        return event.original_text, ["LINE_BREAK_INSIDE_WORD"]
    rebuilt = _assemble_slots(slots)
    rebuilt = _restore_tags_by_source_segment(rebuilt, event)
    if rebuilt is None:
        return event.original_text, ["ASS_INLINE_TAG_ANCHOR_FAILURE"]
    if rebuilt.count(r"\N") != len(event.line_break_boundaries):
        flags.append("LINE_BREAK_COUNT_MISMATCH")
    if _has_unsafe_break(rebuilt):
        flags.append("LINE_BREAK_INSIDE_WORD")
    if sorted(TAG_RE.findall(rebuilt)) != sorted(TAG_RE.findall(event.original_text)):
        flags.append("ASS_TAG_MISMATCH")
    flags.extend(flag for flag in validate_inline_tags(event.original_text, rebuilt) if flag not in flags)
    return rebuilt, flags


class V222MemoryRunner(V221MemoryRunner):
    """V2.2.1 runner with only the line-break reconstruction contract replaced."""

    def _set_model_result(self, event: Event, response: dict[str, Any], model: str) -> bool:
        if event.line_break_boundaries and not is_multi_speaker(event):
            text, flags = _reconstruct_line_break_event(event, response)
        else:
            text, flags = reconstruct_event(event, response)
        text, idiomatic_normalized = normalize_idiomatic_output(event.clean_text, text, self.contexts[event.id])
        if idiomatic_normalized:
            flags.append("IDIOMATIC_LITERAL_NORMALIZED")
        result = self.results[event.id]
        result.flags = list(dict.fromkeys(flags))
        result.final_text = text
        result.final_segments = response.get("segments")
        result.final_model = model
        if not any(flag in flags for flag in CRITICAL_FLAGS):
            for flag in content_flags(event, text, self.contexts[event.id], self.english_dictionary, self.protected_terms):
                if flag not in result.flags:
                    result.flags.append(flag)
        critical = set(flags) & CRITICAL_FLAGS
        critical.update(set(result.flags) & CRITICAL_FLAGS)
        if critical:
            result.status = "pending"
            result.failure_reason = "; ".join(sorted(critical))
            return False
        retry_flags = {"UNTRANSLATED_TRANSLATABLE_TOKEN", "CONTEXT_LEAK", "UNSUPPORTED_ADDITION"}
        if event.classification == "ROMANIZATION_GLOSS":
            retry_flags.add("POSSIBLE_UNTRANSLATED_OUTPUT")
        result.retry_recommended = any(flag in result.flags for flag in retry_flags)
        result.status = "resolved"
        result.failure_reason = ""
        if event.classification == "SIGN_OR_SCREEN_TEXT" and result.retry_recommended:
            result.retry_recommended = False
            self.v221_metrics["noncritical_sign_retries_suppressed"] = self.v221_metrics.get("noncritical_sign_retries_suppressed", 0) + 1
        return True

    def run(self) -> dict[str, Any]:
        summary = super().run()
        result_by_id = {item["id"]: item for item in summary["results"]}
        structural_failures: list[str] = []
        for original in self.v221_original_events:
            transform = self.v221_transformations.get(original.id)
            if not transform:
                continue
            item = result_by_id[original.id]
            if item.get("final_text") is None:
                continue
            kind = transform["kind"]
            if kind == "EMPTY_VISUAL_SEGMENTS":
                # V2.2.1's post-processing may already have put its unsafe
                # breaks into ``final_text``.  Recover the semantic response
                # before applying the V2.2.2 anchor contract; never feed the
                # previous structural candidate back into the new reflow.
                semantic = TAG_RE.sub("", item["final_text"]).replace(r"\N", " ")
                restored = _restore_empty_breaks_v222(original, semantic)
            elif kind == "MIXED_VECTOR_TEXT":
                semantic = _scan_ass_parts(item["final_text"])["linguistic_text"]
                restored = _restore_mixed_v222(original, transform["info"], semantic)
            else:
                continue
            if restored is None:
                item["status"] = "failed"
                item["failure_reason"] = "V222_STRUCTURAL_RECONSTRUCTION_FAILURE"
                item.setdefault("flags", []).append("V222_STRUCTURAL_RECONSTRUCTION_FAILURE")
                structural_failures.append("V222_STRUCTURAL_RECONSTRUCTION_FAILURE")
                continue
            item["status"] = "resolved"
            item["failure_reason"] = ""
            item["final_text"] = restored
            item["flags"] = [flag for flag in item.get("flags", []) if flag not in {"V221_STRUCTURAL_RECONSTRUCTION_FAILURE", "LINE_BREAK_INSIDE_WORD", "LINE_BREAK_COUNT_MISMATCH"}]
            item.setdefault("flags", []).append("V222_STRUCTURAL_RESTORED")
        summary["results"] = [result_by_id[event.id] for event in self.v221_original_events]
        summary["resolved"] = sum(item["status"] == "resolved" for item in result_by_id.values())
        summary["failed"] = len(result_by_id) - summary["resolved"]
        summary["eligible"] = summary["failed"] == 0
        flags = Counter(flag.split(":", 1)[0] for item in result_by_id.values() for flag in item.get("flags", []))
        critical = sorted({flag.split(":", 1)[0] for item in result_by_id.values() for flag in item.get("flags", []) if flag.split(":", 1)[0] in CRITICAL_FLAGS} | set(structural_failures))
        summary["eligible_experimental"] = summary["eligible"] and not critical
        summary["flags"] = dict(flags)
        summary["critical_flags"] = critical
        summary["structural_failures"] = sorted(set(structural_failures))
        summary["line_break_inside_word_count"] = flags.get("LINE_BREAK_INSIDE_WORD", 0)
        summary["line_break_count_mismatch_count"] = flags.get("LINE_BREAK_COUNT_MISMATCH", 0)
        return summary


def _config(subtitle_path: Path, folder_glossary: dict[str, str] | None) -> tuple[Config, dict[str, str]]:
    from production_v2_2_1_adapter import _config as v221_config
    return v221_config(subtitle_path, folder_glossary)


def translate_subtitle_file_v2_2_2(
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
        raise RuntimeError("V2.2.2 aceita somente ASS/SSA; saída não publicada")
    if output_path.exists():
        raise FileExistsError(f"a saída final já existe: {output_path.name}")
    started = time.perf_counter()
    config, merged_glossary = _config(subtitle_path, glossary)
    memory_root = Path(memory_db_root or os.environ.get("ANIME_SUBTITLE_LIBRARY_ROOT", "/app/state/anime-subtitle-library"))
    memory = TranslationMemory(memory_root)
    build = memory.sync_approved()
    original, events, profile = load_events(subtitle_path, merged_glossary)
    effective_job = job_id or f"v222-{output_path.name}-{int(time.time() * 1000)}"
    runner = V222MemoryRunner(events, profile, config, merged_glossary, memory, anime_series_id, episode_id, effective_job)
    summary = runner.run()
    summary["memory_build"] = build
    summary["pipeline"] = APPROVED_PIPELINE
    summary["model"] = config.model
    if not summary.get("eligible_experimental"):
        raise RuntimeError(json.dumps({"reason": "v2_2_2_not_eligible", "resolved": summary.get("resolved"), "events": summary.get("events"), "critical_flags": summary.get("critical_flags", []), "flags": summary.get("flags", [])}, ensure_ascii=False))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_tmp = tempfile.mkstemp(prefix=f".{output_path.name}.v2_2_2-", suffix=output_path.suffix, dir=str(output_path.parent)); os.close(fd)
    tmp_path = Path(raw_tmp)
    try:
        write_ass(original, events, summary, tmp_path)
        candidate = pysubs2.load(str(tmp_path))
        validation = validate_structure(original, candidate, {event.original_index for event in events}, {event.original_index for event in events if is_multi_speaker(event)})
        if not validation.get("valid"):
            raise RuntimeError(json.dumps({"reason": "v2_2_2_structure_invalid", "issues": validation.get("issues", [])[:30]}, ensure_ascii=False))
        if output_path.exists():
            raise FileExistsError(f"a saída final apareceu durante o job: {output_path.name}")
        _fsync_file(tmp_path); os.replace(tmp_path, output_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    result = {key: summary.get(key) for key in ("events", "linguistic_events", "vector_only_events", "mixed_vector_text_events", "empty_visual_segment_events", "resolved", "failed", "total_ollama_calls", "initial_ollama_calls", "actual_retry_ollama_calls", "events_retried", "flags", "critical_flags", "structural_failures", "line_break_inside_word_count", "line_break_count_mismatch_count", "memory_candidates_found", "memory_items_used", "memory_conflicts", "memory_misses", "retry_budget")}
    result.update({"pipeline": APPROVED_PIPELINE, "model": config.model, "calls": summary.get("total_ollama_calls", 0), "retry_calls": summary.get("actual_retry_ollama_calls", 0), "elapsed_client_seconds": round(time.perf_counter() - started, 3), "output": output_path.name})
    print("V2_2_2_SUMMARY " + json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result


translate_subtitle_file = translate_subtitle_file_v2_2_2
