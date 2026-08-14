"""V2.2.4 candidate for multiline ASS sign cards.

V2.2.3 remains the production/rollback implementation.  This adapter composes
it and changes only the restoration of events that contain empty visual ASS
segments.  Empty runs are visual break blocks, never linguistic slots; the
model therefore receives only non-empty source segments and reconstruction
puts the original break blocks back between their stable segment IDs.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any

import pysubs2

from pipeline_v2_1_3 import (
    CRITICAL_FLAGS,
    TAG_RE,
    Config,
    Event,
    content_flags,
    is_multi_speaker,
    load_events,
    validate_inline_tags,
    validate_structure,
    write_ass,
)
from production_v2_1_3_adapter import _fsync_file
from production_v2_2_2_adapter import (
    _assemble_slots,
    _has_unsafe_break,
    _restore_tags_by_source_segment,
    _safe_break_candidates,
    _word_char,
    _config,
)
from production_v2_2_3_adapter import V223MemoryRunner
from translation_memory import TranslationMemory
from failure_ledger import FailureLedger


APPROVED_PIPELINE = "v2_2_4"
APPROVED_MODEL = "qwen3.5:9b"

_STRUCTURAL_FAILURE_FLAGS = {
    "V221_STRUCTURAL_RECONSTRUCTION_FAILURE",
    "V222_STRUCTURAL_RECONSTRUCTION_FAILURE",
    "V224_STRUCTURAL_RECONSTRUCTION_FAILURE",
    "LINE_BREAK_INSIDE_WORD",
    "LINE_BREAK_COUNT_MISMATCH",
    "ASS_INLINE_TAG_ANCHOR_FAILURE",
}


def _nonempty_source_segments(event: Event) -> list[str]:
    return [segment.clean_text.strip() for segment in event.segments if segment.clean_text.strip()]


def _visual_break_blocks(event: Event) -> dict[str, Any]:
    """Return stable linguistic indices and explicit visual break blocks."""
    source_parts = [segment.clean_text for segment in event.segments]
    nonempty_indices = [index for index, part in enumerate(source_parts) if part.strip()]
    blocks: list[dict[str, Any]] = []
    if not nonempty_indices:
        return {
            "source_segments": source_parts,
            "linguistic_segment_indices": [],
            "visual_break_blocks": [],
            "leading_breaks": max(0, len(source_parts) - 1),
            "trailing_breaks": 0,
        }
    leading = nonempty_indices[0]
    trailing = max(0, len(source_parts) - 1 - nonempty_indices[-1])
    for left, right in zip(nonempty_indices, nonempty_indices[1:]):
        # There is one ASS break for every slot transition.  Empty slots are
        # visual content, so A + five empty slots + B has six breaks.
        count = right - left
        if count:
            blocks.append({"after_linguistic_index": nonempty_indices.index(left), "count": count})
    return {
        "source_segments": source_parts,
        "linguistic_segment_indices": nonempty_indices,
        "visual_break_blocks": blocks,
        "leading_breaks": leading,
        "trailing_breaks": trailing,
    }


def classify_multiline_sign_card(event: Event) -> dict[str, Any]:
    shape = _visual_break_blocks(event)
    eligible = bool(
        len(shape["linguistic_segment_indices"]) >= 1
        and (
            shape["visual_break_blocks"]
            or shape["leading_breaks"]
            or shape["trailing_breaks"]
        )
    )
    return {
        "class": "MULTILINE_SIGN_CARD" if eligible else "EMPTY_VISUAL_SEGMENT_EVENT",
        "eligible": eligible,
        "event_id": event.id,
        "style": event.style,
        "classification": event.classification,
        **shape,
        "break_count": len(event.line_break_boundaries),
    }


def _split_linguistic_segments_v224(text: str, source_parts: list[str]) -> list[str] | None:
    """Split a model string only at lexical-safe boundaries.

    Source lengths rank boundaries but are never copied as target offsets.  A
    tie deliberately prefers the earliest boundary, which keeps a title/card
    segment intact (e.g. ``Episódio 4``) instead of moving the first word of
    the next segment into it.  Empty visual segments are not represented here.
    """
    if not isinstance(text, str):
        return None
    plain = TAG_RE.sub("", text.strip()).replace(r"\N", " ").strip()
    parts = [part.strip() for part in source_parts if part.strip()]
    if not parts:
        return []
    if len(parts) == 1:
        return [plain]
    if not plain:
        return None
    candidates = _safe_break_candidates(plain)
    if len(candidates) < len(parts) - 1:
        return None
    lengths = [max(1, len(part)) for part in parts]
    source_word_counts = [max(1, len(re.findall(r"[\wÀ-ÿ]+", part, re.UNICODE))) for part in parts]
    output_word_count = max(1, len(re.findall(r"[\wÀ-ÿ]+", plain, re.UNICODE)))
    total = sum(lengths)
    selected: list[int] = []
    running = 0
    for index, length in enumerate(lengths[:-1]):
        running += length
        target = round(running / total * len(plain))
        running_words = sum(source_word_counts[:index + 1])
        target_words = round(running_words / max(1, sum(source_word_counts)) * output_word_count)
        available = [
            candidate for candidate in candidates
            if (not selected or candidate > selected[-1])
            and candidate <= len(plain) - (len(parts) - index - 2)
        ]
        if not available:
            return None

        def score(candidate: int) -> tuple[int, int, int, int]:
            left, right = plain[candidate - 1], plain[candidate]
            # Prefer a boundary after a complete word; then closest target;
            # finally earliest boundary to avoid swallowing the next word.
            orientation = 0 if _word_char(left) and right.isspace() else 1
            left_words = len(re.findall(r"[\wÀ-ÿ]+", plain[:candidate], re.UNICODE))
            return abs(left_words - target_words), orientation, abs(candidate - target), candidate

        selected.append(min(available, key=score))
    result: list[str] = []
    start = 0
    for end in selected + [len(plain)]:
        part = plain[start:end].strip()
        if not part:
            return None
        result.append(part)
        start = end
    return result


def _restore_multiline_sign_card_v224(original: Event, translated: str) -> tuple[str | None, dict[str, Any]]:
    """Restore a card using linguistic IDs plus explicit visual break blocks."""
    shape = _visual_break_blocks(original)
    source_parts = shape["source_segments"]
    nonempty = [part for part in source_parts if part.strip()]
    evidence: dict[str, Any] = {
        "class": "MULTILINE_SIGN_CARD",
        "source_segments": source_parts,
        "linguistic_source_segments": nonempty,
        "break_count": len(original.line_break_boundaries),
        "break_blocks": shape["visual_break_blocks"],
    }
    if not nonempty:
        # A purely visual/empty event must never create a model request.  Keep
        # the canonical ASS bytes exactly as supplied.
        evidence["preservation"] = "source_bytes"
        return original.original_text, evidence
    translated_parts = _split_linguistic_segments_v224(translated, nonempty)
    if translated_parts is None:
        evidence["failure"] = "NO_SAFE_LINGUISTIC_BOUNDARY"
        return None, evidence
    iterator = iter(translated_parts)
    slots = [next(iterator) if part.strip() else "" for part in source_parts]
    # Do not synthesize whitespace around a visual block.  The original ASS
    # slot boundaries (including empty slots) are the entire structural
    # contract; linguistic whitespace belongs to the translated segment.
    restored = r"\N".join(slots)
    restored = _restore_tags_by_source_segment(restored, original)
    if restored is None:
        evidence["failure"] = "ASS_INLINE_TAG_ANCHOR_FAILURE"
        return None, evidence
    evidence["translated_segments"] = translated_parts
    evidence["reconstructed"] = restored
    evidence["reconstructed_break_count"] = restored.count(r"\N")
    if restored.count(r"\N") != len(original.line_break_boundaries):
        evidence["failure"] = "LINE_BREAK_COUNT_MISMATCH"
        return None, evidence
    if _has_unsafe_break(restored):
        evidence["failure"] = "LINE_BREAK_INSIDE_WORD"
        return None, evidence
    inline_flags = validate_inline_tags(original.original_text, restored)
    if inline_flags:
        evidence["failure"] = ";".join(inline_flags)
        return None, evidence
    return restored, evidence


class V224MemoryRunner(V223MemoryRunner):
    """V2.2.3 runner with explicit visual break blocks."""

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.failure_ledger: FailureLedger | None = None
        self.v224_multiline_cards: dict[int, dict[str, Any]] = {}
        self.v224_metrics = {
            "multiline_sign_card_events": 0,
            "visual_break_block_count": 0,
            "visual_break_block_max_breaks": 0,
            "visual_break_linguistic_segments": 0,
        }
        for event in self.v221_original_events:
            if event.id not in self.v221_transformations:
                continue
            info = classify_multiline_sign_card(event)
            if info["eligible"] and self.v221_transformations[event.id]["kind"] == "EMPTY_VISUAL_SEGMENTS":
                self.v224_multiline_cards[event.id] = info
                self.v224_metrics["multiline_sign_card_events"] += 1
                self.v224_metrics["visual_break_block_count"] += len(info["visual_break_blocks"])
                self.v224_metrics["visual_break_block_max_breaks"] = max(
                    self.v224_metrics["visual_break_block_max_breaks"],
                    max((block["count"] for block in info["visual_break_blocks"]), default=0),
                )
                self.v224_metrics["visual_break_linguistic_segments"] += len(info["linguistic_segment_indices"])

    @staticmethod
    def _budget_snapshot(runner: "V224MemoryRunner") -> dict[str, Any]:
        budget = runner.retry_budget
        return {
            "configured": int(budget.consumed + budget.remaining),
            "consumed": int(budget.consumed),
            "remaining": int(budget.remaining),
            "max_depth": int(budget.max_depth),
            "last_reason": budget.last_reason,
        }

    def _set_model_result(self, event: Event, response: dict[str, Any], model: str) -> bool:
        valid = super()._set_model_result(event, response, model)
        if self.failure_ledger is not None:
            self.failure_ledger.record_unit_update(self, event)
        return valid

    def _attempt(
        self,
        units: list[Any],
        simplified: bool = False,
        phase: str = "main",
        parent_call_id: str | None = None,
    ) -> tuple[set[int], list[str]]:
        before = self._budget_snapshot(self)
        calls_before = len(self.calls)
        try:
            return super()._attempt(units, simplified, phase, parent_call_id)
        finally:
            after = self._budget_snapshot(self)
            if self.failure_ledger is not None:
                for call in self.calls[calls_before:]:
                    call.setdefault("ledger_stage", "model_call")
                    call.setdefault("ledger_event_ids", [event.id for unit in units for event in unit.events])
                    self.failure_ledger.record_call(call, budget_before=before, budget_after=after)

    def run(self) -> dict[str, Any]:
        summary = super().run()
        result_by_id = {item["id"]: item for item in summary["results"]}
        recovered: list[int] = []
        new_failures: list[str] = []
        for event_id, card in self.v224_multiline_cards.items():
            event = next(event for event in self.v221_original_events if event.id == event_id)
            item = result_by_id[event_id]
            # V2.2.2 may leave its semantic response in final_text even when
            # its old envelope restoration failed.  Do not feed old ASS breaks
            # back into the new reflow.
            semantic = TAG_RE.sub("", str(item.get("final_text") or "")).replace(r"\N", " ").strip()
            restored, evidence = _restore_multiline_sign_card_v224(event, semantic)
            item["v224_visual_break_evidence"] = evidence
            if restored is None:
                item["status"] = "failed"
                item["failure_reason"] = "V224_STRUCTURAL_RECONSTRUCTION_FAILURE"
                item.setdefault("flags", []).append("V224_STRUCTURAL_RECONSTRUCTION_FAILURE")
                new_failures.append("V224_STRUCTURAL_RECONSTRUCTION_FAILURE")
                continue
            item["status"] = "resolved"
            item["failure_reason"] = ""
            item["final_text"] = restored
            item["final_model"] = item.get("final_model") or "v224-visual-break-block"
            item["flags"] = [flag for flag in item.get("flags", []) if flag not in _STRUCTURAL_FAILURE_FLAGS]
            item.setdefault("flags", []).append("V224_VISUAL_BREAK_BLOCK_RESTORED")
            recovered.append(event_id)
        result_list = [result_by_id[event.id] for event in self.v221_original_events]
        flags = Counter(flag.split(":", 1)[0] for item in result_list for flag in item.get("flags", []))
        critical = sorted({
            flag.split(":", 1)[0]
            for item in result_list
            for flag in item.get("flags", [])
            if flag.split(":", 1)[0] in CRITICAL_FLAGS or flag.split(":", 1)[0] in new_failures
        })
        summary["results"] = result_list
        summary["resolved"] = sum(item.get("status") == "resolved" for item in result_list)
        summary["failed"] = len(result_list) - summary["resolved"]
        summary["eligible"] = summary["failed"] == 0
        summary["eligible_experimental"] = summary["eligible"] and not critical
        summary["flags"] = dict(flags)
        summary["critical_flags"] = critical
        summary["structural_failures"] = sorted(set(summary.get("structural_failures", [])) - {
            "V221_STRUCTURAL_RECONSTRUCTION_FAILURE",
            "V222_STRUCTURAL_RECONSTRUCTION_FAILURE",
        } | set(new_failures))
        summary.update(self.v224_metrics)
        summary["multiline_sign_card_recovered_event_ids"] = sorted(recovered)
        summary["multiline_sign_card_failed_event_ids"] = sorted(
            event_id for event_id in self.v224_multiline_cards if event_id not in recovered
        )
        summary["line_break_inside_word_count"] = flags.get("LINE_BREAK_INSIDE_WORD", 0)
        summary["line_break_count_mismatch_count"] = flags.get("LINE_BREAK_COUNT_MISMATCH", 0)
        return summary


def translate_subtitle_file_v2_2_4(
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
        raise RuntimeError("V2.2.4 aceita somente ASS/SSA; saída não publicada")
    if output_path.exists():
        raise FileExistsError(f"a saída final já existe: {output_path.name}")
    started = time.perf_counter()
    config, merged_glossary = _config(subtitle_path, glossary)
    memory_root = Path(memory_db_root or os.environ.get("ANIME_SUBTITLE_LIBRARY_ROOT", "/app/state/anime-subtitle-library"))
    memory = TranslationMemory(memory_root)
    build = memory.sync_approved()
    original, events, profile = load_events(subtitle_path, merged_glossary)
    effective_job = job_id or f"v224-{output_path.name}-{int(time.time() * 1000)}"
    ledger = FailureLedger(effective_job, {
        "episode_id": episode_id,
        "anime_series_id": anime_series_id,
        "pipeline": APPROVED_PIPELINE,
        "model": config.model,
        "source_path": str(subtitle_path),
        "output_path": str(output_path),
    })
    ledger.set_source(subtitle_path)
    # This flag only asks the frozen client to retain the already returned
    # response content in its call observation.  It does not alter the
    # prompt, model parameters, retry policy, parser, or validators.
    config.diagnostic_capture = True
    runner = V224MemoryRunner(events, profile, config, merged_glossary, memory, anime_series_id, episode_id, effective_job)
    runner.failure_ledger = ledger
    ledger.register_runner(runner)
    summary: dict[str, Any] | None = None
    try:
        summary = runner.run()
    except Exception as exc:
        snapshot = ledger.snapshot(runner, None, stage="runner.run", error=f"{type(exc).__name__}: {exc}")
        raise RuntimeError(json.dumps({
            "reason": "v2_2_4_runner_exception",
            "failure_snapshot": snapshot,
            "ledger_dir": ledger.path,
            "stage_of_failure": "runner.run",
            "error": str(exc)[:500],
        }, ensure_ascii=False)) from exc
    summary["memory_build"] = build
    summary["pipeline"] = APPROVED_PIPELINE
    summary["model"] = config.model
    if not summary.get("eligible_experimental"):
        snapshot = ledger.snapshot(runner, summary, stage="eligibility_gate", error="v2_2_4_not_eligible")
        failure_summary = {
            "reason": "v2_2_4_not_eligible",
            "resolved": summary.get("resolved"),
            "events": summary.get("events"),
            "critical_flags": summary.get("critical_flags", []),
            "flags": summary.get("flags", {}),
            "structural_failures": summary.get("structural_failures", []),
            "failure_snapshot": snapshot,
            "ledger_dir": ledger.path,
            "stage_of_failure": "eligibility_gate",
        }
        print("V2_2_4_FAILURE_SUMMARY " + json.dumps(failure_summary, ensure_ascii=False, sort_keys=True))
        raise RuntimeError(json.dumps(failure_summary, ensure_ascii=False))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_tmp = tempfile.mkstemp(prefix=f".{output_path.name}.v2_2_4-", suffix=output_path.suffix, dir=str(output_path.parent))
    os.close(fd)
    tmp_path = Path(raw_tmp)
    try:
        write_ass(original, events, summary, tmp_path)
        candidate = pysubs2.load(str(tmp_path))
        validation = validate_structure(
            original,
            candidate,
            {event.original_index for event in events},
            {
                event.original_index
                for event in events
                if is_multi_speaker(event) or event.id in runner.v224_multiline_cards
            },
        )
        if not validation.get("valid"):
            raise RuntimeError(json.dumps({"reason": "v2_2_4_structure_invalid", "issues": validation.get("issues", [])[:30]}, ensure_ascii=False))
        if output_path.exists():
            raise FileExistsError(f"a saída final apareceu durante o job: {output_path.name}")
        _fsync_file(tmp_path)
        os.replace(tmp_path, output_path)
        try:
            directory_fd = os.open(output_path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        snapshot = ledger.snapshot(runner, summary, stage="output_validation_or_write", error=f"{type(exc).__name__}: {exc}")
        # Preserve the snapshot path in the exception for the web worker; the
        # linguistic result itself remains failed closed.
        if isinstance(exc, RuntimeError):
            raise RuntimeError(json.dumps({
                "reason": "v2_2_4_output_failure",
                "failure_snapshot": snapshot,
                "ledger_dir": ledger.path,
                "stage_of_failure": "output_validation_or_write",
                "error": str(exc)[:500],
            }, ensure_ascii=False)) from exc
        raise
    result = {
        key: summary.get(key)
        for key in (
            "events", "linguistic_events", "vector_only_events", "mixed_vector_text_events",
            "empty_visual_segment_events", "multiline_sign_card_events", "visual_break_block_count",
            "visual_break_block_max_breaks", "resolved", "failed", "total_ollama_calls",
            "initial_ollama_calls", "actual_retry_ollama_calls", "events_retried", "flags",
            "critical_flags", "structural_failures", "line_break_inside_word_count",
            "line_break_count_mismatch_count", "short_english_candidates", "short_english_retries",
            "short_english_residual_after_retry", "memory_candidates_found", "memory_items_used",
            "memory_conflicts", "memory_misses", "retry_budget",
        )
    }
    result.update({
        "pipeline": APPROVED_PIPELINE,
        "model": config.model,
        "calls": summary.get("total_ollama_calls", 0),
        "retry_calls": summary.get("actual_retry_ollama_calls", 0),
        "elapsed_client_seconds": round(time.perf_counter() - started, 3),
        "output": output_path.name,
    })
    ledger.complete(runner, summary)
    print("V2_2_4_SUMMARY " + json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result


translate_subtitle_file = translate_subtitle_file_v2_2_4
