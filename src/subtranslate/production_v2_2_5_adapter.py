"""V2.2.5: bounded recovery for persistent source-copy residuals.

V2.2.4 remains available as rollback.  This adapter composes the complete
multiline-sign, break, delimiter and short-fragment behavior and adds one
last semantic retry only when a high-confidence linguistic target remains
effectively identical to its English source after the existing retries.
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

import pipeline_v2_1_3 as frozen_pipeline
from failure_ledger import FailureLedger
from pipeline_v2_1_3 import (
    CRITICAL_FLAGS,
    TAG_RE,
    Config,
    Event,
    Unit,
    is_multi_speaker,
    load_events,
    validate_structure,
    write_ass,
)
from production_v2_1_3_adapter import _fsync_file
from production_v2_2_3_adapter import (
    INTERRUPTED_DIALOGUE_RETRY_INSTRUCTION,
    SHORT_ENGLISH_HIGH_CONFIDENCE,
    V223MemoryClient,
    classify_short_english_fragment,
    normalize_short_fragment_for_detection,
)
from production_v2_2_4_adapter import V224MemoryRunner, _config
from translation_memory import TranslationMemory


APPROVED_PIPELINE = "v2_2_5"
APPROVED_MODEL = "qwen3.5:9b"
PERSISTENT_SOURCE_COPY_RETRY = "PERSISTENT_SOURCE_COPY_RETRY"
PERSISTENT_SOURCE_COPY = "PERSISTENT_SOURCE_COPY"
_RESIDUAL_FLAGS = {SHORT_ENGLISH_HIGH_CONFIDENCE, "SHORT_ENGLISH_RESIDUAL"}

PERSISTENT_SOURCE_COPY_RETRY_INSTRUCTION = (
    "\n\nPERSISTENT_SOURCE_COPY: o TARGET é diálogo linguístico em inglês e uma "
    "tentativa anterior devolveu o próprio inglês. Isso não é aceitável. "
    "Traduza o significado para português brasileiro usando o contexto; não "
    "copie o source nem devolva uma variante apenas com pontuação, caixa, "
    "travessão ou reticências diferentes. Preserve stutter, hesitação e "
    "interrupção quando forem semanticamente aplicáveis. Não invente a parte "
    "truncada e não receba tradução pronta. Nomes, callsigns, códigos, siglas, "
    "romaji, songs, SFX, signs e labels técnicos protegidos permanecem somente "
    "quando a classificação indicar conteúdo protegido. Responda apenas no "
    "JSON solicitado."
)


def effective_source_copy_key(text: str) -> tuple[str, ...]:
    """Return analysis-only lexical identity; never mutate canonical ASS text."""
    analysis = normalize_short_fragment_for_detection(unicodedata.normalize("NFKC", text or ""))
    words = [word.casefold().replace("’", "'") for word in analysis.get("words", [])]
    # A stutter may be serialized as ``H-Hold`` or ``H - Hold``.  Treat only
    # an isolated one-letter prefix that repeats the next word's first letter
    # as presentation noise; all other lexical words remain significant.
    if len(words) >= 2 and len(words[0]) == 1 and words[1].startswith(words[0]):
        words = words[1:]
    return tuple(words)


def is_effective_source_copy(source: str, output: str) -> bool:
    """Detect source-copy despite punctuation/case/stutter presentation noise."""
    source_key = effective_source_copy_key(source)
    output_key = effective_source_copy_key(output)
    return bool(source_key) and source_key == output_key


def preserve_interrupted_speech_features(source: str, output: str) -> str:
    """Restore only source-proven stutter/terminal interruption markers.

    This is presentation preservation, not a translation or lexical
    substitution.  It is applied only to the specialized retry result.
    """
    if not output:
        return output
    view = normalize_short_fragment_for_detection(source)
    restored = output.strip()
    if view.get("stuttered") and not re.search(r"\b[A-Za-zÀ-ÿ]-[A-Za-zÀ-ÿ]", restored):
        match = re.search(r"([A-Za-zÀ-ÿ])([A-Za-zÀ-ÿ]+)", restored)
        if match:
            first = match.group(1)
            restored = restored[:match.start()] + first + "-" + restored[match.start():]
    if view.get("interrupted") and not re.search(r"(?:\.\.\.|…|[-‐‑‒–—])\s*$", restored):
        source_visible = view.get("visible", "")
        if re.search(r"(?:\.\.\.|…)$", source_visible.strip()):
            restored += "..."
        elif re.search(r"[-‐‑‒–—]$", source_visible.strip()):
            restored += "—"
    return restored


class V225MemoryClient(V223MemoryClient):
    """Adapter-only request envelope for the final source-copy escalation."""

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.persistent_source_copy_retry = False

    def call(self, units, events, contexts, simplified=False, phase="main"):
        if not self.persistent_source_copy_retry:
            return super().call(units, events, contexts, simplified=simplified, phase=phase)

        original_post = frozen_pipeline.requests.post

        def patched_post(url, **kwargs):
            payload = dict(kwargs.get("json") or {})
            messages = list(payload.get("messages") or [])
            if messages:
                first = dict(messages[0])
                analysis = []
                for unit in units:
                    for event in unit.events:
                        view = normalize_short_fragment_for_detection(event.clean_text)
                        analysis.append({
                            "id": event.id,
                            "lexical_core": " ".join(view.get("words", [])),
                            "leading_stutter": bool(view.get("stuttered")),
                            "interrupted": bool(view.get("interrupted")),
                        })
                content = str(first.get("content") or "")
                # For this last escalation only, hide presentation noise from
                # the model while retaining it as explicit metadata.  The
                # canonical ASS source remains untouched and reconstruction
                # still receives the original Event object.
                target_match = re.search(r"TARGET: (\[.*?\])\nGLOSSARY:", content, flags=re.DOTALL)
                if target_match:
                    try:
                        targets = json.loads(target_match.group(1))
                    except (TypeError, ValueError, json.JSONDecodeError):
                        targets = None
                    if isinstance(targets, list):
                        by_id = {item["id"]: item for item in analysis}
                        for target in targets:
                            info = by_id.get(target.get("id"))
                            if info:
                                target["text"] = info["lexical_core"]
                                target["speech_features"] = {
                                    "leading_stutter": info["leading_stutter"],
                                    "interrupted": info["interrupted"],
                                }
                        content = content[:target_match.start(1)] + json.dumps(targets, ensure_ascii=False) + content[target_match.end(1):]
                auxiliary = (
                    "\n\nAUXILIARY SPEECH ANALYSIS (not a translation): "
                    + json.dumps(analysis, ensure_ascii=False)
                    + ". Translate the lexical_core's meaning; the leading stutter "
                    "and interrupted punctuation are presentation features to preserve "
                    "in Portuguese. Translate every lexical_core word; no English "
                    "lexical_core token may remain in the returned Portuguese text. "
                    "Treat a multiword lexical_core as one idiomatic expression, not "
                    "as a word-by-word label. The result is invalid if any source "
                    "lexical word is repeated unchanged. Do not return the English lexical_core."
                )
                first["content"] = content + PERSISTENT_SOURCE_COPY_RETRY_INSTRUCTION + auxiliary
                messages[0] = first
                payload["messages"] = messages
            kwargs["json"] = payload
            return original_post(url, **kwargs)

        frozen_pipeline.requests.post = patched_post
        try:
            found, issues, observation = super().call(
                units, events, contexts, simplified=simplified, phase=phase
            )
            observation["retry_specialization"] = PERSISTENT_SOURCE_COPY_RETRY
            observation["retry_instruction_chars"] = len(
                INTERRUPTED_DIALOGUE_RETRY_INSTRUCTION + PERSISTENT_SOURCE_COPY_RETRY_INSTRUCTION
            )
            return found, issues, observation
        finally:
            frozen_pipeline.requests.post = original_post


class V225MemoryRunner(V224MemoryRunner):
    """V2.2.4 runner plus one bounded persistent-source-copy retry."""

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        previous = self.client
        self.client = V225MemoryClient(
            previous.config,
            self.calls,
            previous.glossary,
            previous.memory,
            previous.anime_series_id,
            previous.episode_id,
            previous.job_id,
        )
        self.v225_source_copy_candidates: set[int] = set()
        self.v225_source_copy_retries: set[int] = set()
        self.v225_source_copy_residuals: set[int] = set()

    @staticmethod
    def _result_update(result: Any) -> dict[str, Any]:
        return {
            "status": getattr(result, "status", "failed"),
            "final_text": getattr(result, "final_text", "") or "",
            "final_model": getattr(result, "final_model", "") or "",
            "flags": list(getattr(result, "flags", []) or []),
            "failure_reason": getattr(result, "failure_reason", "") or "",
            "retry_count": int(getattr(result, "retry_count", 0) or 0),
            "retry_recommended": bool(getattr(result, "retry_recommended", False)),
        }

    @staticmethod
    def _merge_v224_rendered_envelope(
        item: dict[str, Any],
        result_update: dict[str, Any],
        structural_event_ids: set[int],
    ) -> dict[str, Any]:
        """Merge semantic retry state without replacing V2.2.4 ASS structure.

        V2.2.4's ``summary`` already contains the authoritative reconstructed
        ASS envelope for multiline cards.  V2.2.5 may update only the
        linguistic result; copying the base Result wholesale would discard
        tags, break blocks and their ordering.  The structural envelope is
        therefore captured before the semantic merge and restored only for
        events that V2.2.4 classified as multiline cards.
        """
        rendered_final_text = item.get("final_text")
        rendered_flags = list(item.get("flags", []) or [])
        item.update(result_update)
        if item.get("id") in structural_event_ids and item.get("status") == "resolved":
            if rendered_final_text is not None:
                item["final_text"] = rendered_final_text
            item["flags"] = rendered_flags
        return item

    def _eligible_source_copy(self, event: Event, result: Any) -> bool:
        assessment = classify_short_english_fragment(
            event,
            getattr(result, "final_text", "") or "",
            self.contexts.get(event.id, {}),
            self.protected_terms,
        )
        protected = assessment.get("status") != SHORT_ENGLISH_HIGH_CONFIDENCE
        return bool(
            not protected
            and event.classification in {"MAIN_DIALOGUE", "NARRATION_OR_THOUGHT", "SDH"}
            and SHORT_ENGLISH_HIGH_CONFIDENCE in (getattr(result, "flags", []) or [])
            and is_effective_source_copy(event.clean_text, getattr(result, "final_text", "") or "")
        )

    def _last_parent_call(self, event_id: int) -> str | None:
        for item in reversed(self.calls):
            if event_id in (item.get("event_ids") or []):
                return item.get("call_id")
        return None

    def _persistent_retry(self, event: Event) -> None:
        result = self.results[event.id]
        if not self._eligible_source_copy(event, result):
            return
        self.v225_source_copy_candidates.add(event.id)
        if self.retry_budget.remaining <= 0:
            self.v225_source_copy_residuals.add(event.id)
            return
        calls_before = len(self.calls)
        self.client.persistent_source_copy_retry = True
        try:
            self._attempt(
                [Unit(f"event-{event.id}", [event])],
                simplified=False,
                phase="retry_persistent_source_copy",
                parent_call_id=self._last_parent_call(event.id),
            )
        finally:
            self.client.persistent_source_copy_retry = False
        result.final_text = preserve_interrupted_speech_features(event.clean_text, result.final_text or "")
        for call in self.calls[calls_before:]:
            call.update({
                "retry_reason": PERSISTENT_SOURCE_COPY_RETRY,
                "reason": PERSISTENT_SOURCE_COPY_RETRY,
                "persistent_source_copy_event_ids": [event.id],
            })
        self.v225_source_copy_retries.add(event.id)
        if is_effective_source_copy(event.clean_text, getattr(result, "final_text", "") or ""):
            self.v225_source_copy_residuals.add(event.id)
            result.status = "failed"
            result.retry_recommended = False
            result.failure_reason = "SHORT_ENGLISH_RESIDUAL"
            if PERSISTENT_SOURCE_COPY not in result.flags:
                result.flags.append(PERSISTENT_SOURCE_COPY)
            if "SHORT_ENGLISH_RESIDUAL" not in result.flags:
                result.flags.append("SHORT_ENGLISH_RESIDUAL")
        else:
            result.status = "resolved"
            result.failure_reason = ""
            result.retry_recommended = False
            result.flags = [flag for flag in result.flags if flag not in _RESIDUAL_FLAGS and flag != PERSISTENT_SOURCE_COPY]

    def run(self) -> dict[str, Any]:
        summary = super().run()
        original_by_id = {event.id: event for event in self.v221_original_events}
        for item in summary.get("results", []):
            event = original_by_id[item["id"]]
            result = self.results[event.id]
            if self._eligible_source_copy(event, result):
                self._persistent_retry(event)
                item.update(self._result_update(result))

        final_results = []
        for event in self.v221_original_events:
            result = self.results[event.id]
            item = next((value for value in summary["results"] if value["id"] == event.id), {"id": event.id})
            item["id"] = event.id
            self._merge_v224_rendered_envelope(
                item,
                self._result_update(result),
                set(self.v224_multiline_cards),
            )
            assessment = classify_short_english_fragment(
                event,
                item.get("final_text") or "",
                self.contexts.get(event.id, {}),
                self.protected_terms,
            )
            if assessment.get("status") == SHORT_ENGLISH_HIGH_CONFIDENCE:
                item["status"] = "failed"
                item["failure_reason"] = "SHORT_ENGLISH_RESIDUAL"
                if "SHORT_ENGLISH_RESIDUAL" not in item.setdefault("flags", []):
                    item["flags"].append("SHORT_ENGLISH_RESIDUAL")
                self.v225_source_copy_residuals.add(event.id) if is_effective_source_copy(event.clean_text, item.get("final_text") or "") else None
            final_results.append(item)

        flags = Counter(flag.split(":", 1)[0] for item in final_results for flag in item.get("flags", []))
        # V2.2.3 marks a residual as critical before this final escalation.
        # Recompute the effective critical set from the post-escalation event
        # results so a successful bounded retry cannot inherit stale failure
        # state from the previous candidate.
        critical = {
            flag.split(":", 1)[0]
            for item in final_results
            for flag in item.get("flags", [])
            if flag.split(":", 1)[0] in CRITICAL_FLAGS
        }
        critical.discard("SHORT_ENGLISH_RESIDUAL")
        if self.v225_source_copy_residuals:
            critical.add("SHORT_ENGLISH_RESIDUAL")
        summary["results"] = final_results
        summary["resolved"] = sum(item.get("status") == "resolved" for item in final_results)
        summary["failed"] = len(final_results) - summary["resolved"]
        summary["eligible"] = summary["failed"] == 0
        summary["eligible_experimental"] = summary["eligible"] and not critical
        final_residuals = sorted(
            item["id"]
            for item in final_results
            if item.get("status") != "resolved"
            and "SHORT_ENGLISH_RESIDUAL" in {flag.split(":", 1)[0] for flag in item.get("flags", [])}
        )
        # V2.2.3's inherited counter describes the pre-escalation pass.  The
        # V2.2.5 gate must report the post-escalation final result instead.
        summary["short_english_residual_after_retry"] = len(final_residuals)
        summary["short_english_residual_event_ids"] = final_residuals
        summary["flags"] = dict(flags)
        summary["critical_flags"] = sorted(critical)
        summary["persistent_source_copy_candidates"] = len(self.v225_source_copy_candidates)
        summary["persistent_source_copy_retries"] = len(self.v225_source_copy_retries)
        summary["persistent_source_copy_retry_event_ids"] = sorted(self.v225_source_copy_retries)
        summary["persistent_source_copy_residual_after_retry"] = len(self.v225_source_copy_residuals)
        summary["persistent_source_copy_residual_event_ids"] = sorted(self.v225_source_copy_residuals)
        return summary


def translate_subtitle_file_v2_2_5(
    subtitle_path: Path,
    output_path: Path,
    glossary: dict[str, str] | None = None,
    *,
    memory_db_root: str | Path | None = None,
    anime_series_id: int | None = None,
    episode_id: int | None = None,
    job_id: str | None = None,
    execution_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if subtitle_path.suffix.lower() not in {".ass", ".ssa"}:
        raise RuntimeError("V2.2.5 aceita somente ASS/SSA; saída não publicada")
    if output_path.exists():
        raise FileExistsError(f"a saída final já existe: {output_path.name}")
    started = time.perf_counter()
    config, merged_glossary = _config(subtitle_path, glossary)
    if execution_context:
        config.operation_budget = execution_context.get("operation_budget")
        config.model_digest = execution_context.get("primary_model_digest") or execution_context.get("model_digest")
    memory_root = Path(memory_db_root or os.environ.get("ANIME_SUBTITLE_LIBRARY_ROOT", "/app/state/anime-subtitle-library"))
    memory = TranslationMemory(memory_root)
    build = memory.sync_approved()
    original, events, profile = load_events(subtitle_path, merged_glossary)
    effective_job = job_id or f"v225-{output_path.name}-{int(time.time() * 1000)}"
    ledger = FailureLedger(effective_job, {
        "episode_id": episode_id,
        "anime_series_id": anime_series_id,
        "pipeline": APPROVED_PIPELINE,
        "model": config.model,
        "source_path": str(subtitle_path),
        "output_path": str(output_path),
    })
    ledger.set_source(subtitle_path)
    config.diagnostic_capture = True
    runner = V225MemoryRunner(events, profile, config, merged_glossary, memory, anime_series_id, episode_id, effective_job)
    runner.failure_ledger = ledger
    ledger.register_runner(runner)
    summary: dict[str, Any] | None = None
    try:
        summary = runner.run()
    except Exception as exc:
        snapshot = ledger.snapshot(runner, None, stage="runner.run", error=f"{type(exc).__name__}: {exc}")
        raise RuntimeError(json.dumps({"reason": "v2_2_5_runner_exception", "failure_snapshot": snapshot, "ledger_dir": ledger.path, "stage_of_failure": "runner.run", "error": str(exc)[:500]}, ensure_ascii=False)) from exc
    summary["memory_build"] = build
    summary["pipeline"] = APPROVED_PIPELINE
    summary["model"] = config.model
    if not summary.get("eligible_experimental") and not (execution_context or {}).get("v238_allow_primary_ledger_failures"):
        snapshot = ledger.snapshot(runner, summary, stage="eligibility_gate", error="v2_2_5_not_eligible")
        failure_summary = {
            "reason": "v2_2_5_not_eligible",
            "resolved": summary.get("resolved"), "events": summary.get("events"),
            "critical_flags": summary.get("critical_flags", []), "flags": summary.get("flags", {}),
            "structural_failures": summary.get("structural_failures", []),
            "short_english_residual_event_ids": summary.get("short_english_residual_event_ids", []),
            "persistent_source_copy_residual_event_ids": summary.get("persistent_source_copy_residual_event_ids", []),
            "failure_snapshot": snapshot, "ledger_dir": ledger.path, "stage_of_failure": "eligibility_gate",
        }
        print("V2_2_5_FAILURE_SUMMARY " + json.dumps(failure_summary, ensure_ascii=False, sort_keys=True))
        raise RuntimeError(json.dumps(failure_summary, ensure_ascii=False))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_tmp = tempfile.mkstemp(prefix=f".{output_path.name}.v2_2_5-", suffix=output_path.suffix, dir=str(output_path.parent))
    os.close(fd)
    tmp_path = Path(raw_tmp)
    try:
        write_ass(original, events, summary, tmp_path)
        candidate = pysubs2.load(str(tmp_path))
        validation = validate_structure(
            original, candidate, {event.original_index for event in events},
            {event.original_index for event in events if is_multi_speaker(event) or event.id in runner.v224_multiline_cards},
        )
        if not validation.get("valid"):
            raise RuntimeError(json.dumps({"reason": "v2_2_5_structure_invalid", "issues": validation.get("issues", [])[:30]}, ensure_ascii=False))
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
        raise RuntimeError(json.dumps({"reason": "v2_2_5_output_failure", "failure_snapshot": snapshot, "ledger_dir": ledger.path, "stage_of_failure": "output_validation_or_write", "error": str(exc)[:500]}, ensure_ascii=False)) from exc
    result_keys = (
        "events", "linguistic_events", "vector_only_events", "mixed_vector_text_events", "empty_visual_segment_events",
        "multiline_sign_card_events", "visual_break_block_count", "visual_break_block_max_breaks", "resolved", "failed",
        "total_ollama_calls", "initial_ollama_calls", "actual_retry_ollama_calls", "events_retried", "flags", "critical_flags",
        "structural_failures", "line_break_inside_word_count", "line_break_count_mismatch_count", "short_english_candidates",
        "short_english_retries", "short_english_residual_after_retry", "persistent_source_copy_candidates",
        "persistent_source_copy_retries", "persistent_source_copy_residual_after_retry", "persistent_source_copy_residual_event_ids",
        "memory_candidates_found", "memory_items_used", "memory_conflicts", "memory_misses", "retry_budget",
    )
    result = {key: summary.get(key) for key in result_keys}
    # Keep the factual V226 ledger/call observations available to the
    # canonical V2.3.8 materializer.  These are runner-produced records, not
    # a reconstruction from rendered ASS text.  The legacy scalar counters
    # remain unchanged for existing consumers.
    result.update({
        "pipeline": APPROVED_PIPELINE,
        "model": config.model,
        "calls": summary.get("total_ollama_calls", 0),
        "retry_calls": summary.get("actual_retry_ollama_calls", 0),
        "primary_results": summary.get("results", []),
        "primary_calls": summary.get("calls", []),
        "elapsed_client_seconds": round(time.perf_counter() - started, 3),
        "output": output_path.name,
    })
    ledger.complete(runner, summary)
    print("V2_2_5_SUMMARY " + json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result


translate_subtitle_file = translate_subtitle_file_v2_2_5
