"""V2.2.3 candidate: localized recovery for short English dialogue.

V2.2.2 remains the immediate rollback.  This adapter composes its complete
ASS-complexity and line-break behavior and adds one narrowly-scoped semantic
decision: short, interrupted or punctuation-heavy English dialogue that is
still present after a valid model response can consume a localized retry from
the existing hierarchical RetryBudget.
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
    ENGLISH_COMMON,
    TAG_RE,
    Config,
    Event,
    Unit,
    is_multi_speaker,
    load_events,
    source_residue_evidence,
    source_residue_strong,
    validate_structure,
    write_ass,
)
from production_v2_1_3_adapter import APPROVED_MODEL, _fsync_file
from production_v2_2_0_adapter import MemoryClient
from production_v2_2_2_adapter import V222MemoryRunner, _config
from translation_memory import TranslationMemory


APPROVED_PIPELINE = "v2_2_3"

SHORT_ENGLISH_HIGH_CONFIDENCE = "SHORT_ENGLISH_HIGH_CONFIDENCE"
SHORT_ENGLISH_POSSIBLE = "SHORT_ENGLISH_POSSIBLE"
NOT_SHORT_ENGLISH = "NOT_SHORT_ENGLISH"

# Only linguistic categories can enter automatic short-fragment recovery.
# Signs, songs, romanisation, effects, drawings and deterministic preservation
# classes must remain outside this path even when their bytes look English.
SHORT_FRAGMENT_LINGUISTIC_CLASSES = {
    "MAIN_DIALOGUE",
    "NARRATION_OR_THOUGHT",
    "SDH",
}
SHORT_FRAGMENT_PROTECTED_CLASSES = {
    "MUSIC_OR_KARAOKE",
    "ROMAJI_PRESERVED",
    "ROMANIZATION_GLOSS",
    "SONG_LYRICS_PRESERVED",
    "SONG_AMBIGUOUS",
    "SIGN_OR_SCREEN_TEXT",
    "TECHNICAL_OR_EMPTY",
    "VECTOR_ONLY",
}

# This is a generic, deliberately small evidence vocabulary rather than a
# translation table.  It contains sentence/function words, commands,
# operational labels and measurement words that make a short subtitle
# linguistically identifiable.  The text itself is always sent to Qwen.
SHORT_ENGLISH_LEXICON = set(ENGLISH_COMMON) | {
    "abort", "again", "away", "back", "careful", "come", "down", "enemy",
    "can't", "couldn't", "didn't", "doesn't", "fire", "get", "go", "help", "hold", "incoming", "leave", "left",
    "look", "miles", "move", "no", "now", "range", "ready", "right", "run",
    "sector", "stay", "stop", "take", "target", "turn", "up", "wait", "won't", "wouldn't", "yes",
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "hundred", "thousand",
}
SHORT_COMMAND_WORDS = {
    "abort", "come", "fire", "go", "help", "hold", "leave", "look", "move",
    "run", "stay", "stop", "take", "turn", "wait",
}
SHORT_OPERATIONAL_WORDS = {"enemy", "incoming", "miles", "range", "sector", "target"}
SHORT_NUMBER_WORDS = {
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "hundred", "thousand",
}

_WORD_RE = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)?", re.UNICODE)
_STUTTER_RE = re.compile(r"\b([A-Za-z])[-‐‑‒–—](?=[A-Za-z])")
_INTERRUPTION_RE = re.compile(r"(?:\.{2,}|…|[-‐‑‒–—])\s*$")
_ONLY_CODE_TOKEN_RE = re.compile(r"^(?:[A-Z]{2,}|[A-Z]+\d+(?:-\d+)?|[A-Z]+-\d+|\d+[A-Z]+)$")
_KNOWN_SHORT_CODES = {"ai", "arx-7", "as", "e4", "fmp", "m9", "nasa", "ok", "sos", "tdd-1"}

INTERRUPTED_DIALOGUE_RETRY_INSTRUCTION = (
    "\n\nSHORT_INTERRUPTED_DIALOGUE: este TARGET é fala linguística de diálogo, "
    "curta, gaguejada ou interrompida. Não é nome, sigla, código, romanização, "
    "canção, sinal, efeito sonoro, desenho vetorial ou conteúdo protegido. "
    "Traduza o fragmento para português brasileiro sem copiar o inglês e sem "
    "inventar a parte que foi interrompida. Preserve a gagueira, a interrupção "
    "e as reticências quando forem semanticamente relevantes. Responda somente "
    "no JSON solicitado."
)


def normalize_short_fragment_for_detection(text: str) -> dict[str, Any]:
    """Build an analysis-only view while preserving the original bytes."""
    visible = TAG_RE.sub("", text or "").replace(r"\N", " ")
    visible = unicodedata.normalize("NFKC", visible)
    interrupted = bool(_INTERRUPTION_RE.search(visible))
    stuttered = bool(_STUTTER_RE.search(visible))
    lexical = _STUTTER_RE.sub("", visible)
    lexical = re.sub(r"(?:\.{2,}|…|[-‐‑‒–—])+\s*$", "", lexical).strip()
    words = [word.casefold().replace("’", "'") for word in _WORD_RE.findall(lexical)]
    return {
        "original": text,
        "visible": visible,
        "normalized": lexical,
        "words": words,
        "interrupted": interrupted,
        "stuttered": stuttered,
        "has_number": bool(re.search(r"\d", visible)),
        "sentence_punctuation": bool(re.search(r"[!?.,:—…]", visible)),
    }


def _code_only(analysis: dict[str, Any]) -> bool:
    compact = re.sub(r"[\s!?.,:…—]+", " ", analysis["visible"]).strip()
    tokens = compact.split()
    if not tokens:
        return False
    return all(
        token.casefold() in _KNOWN_SHORT_CODES
        or bool(_ONLY_CODE_TOKEN_RE.fullmatch(token))
        or token.isdigit()
        for token in tokens
    )


def _lexical_evidence(analysis: dict[str, Any]) -> dict[str, Any]:
    hits: list[str] = []
    prefix_hits: list[str] = []
    for word in analysis["words"]:
        if word in SHORT_ENGLISH_LEXICON:
            hits.append(word)
            continue
        if analysis["stuttered"] and len(word) >= 3:
            roots = sorted(root for root in SHORT_COMMAND_WORDS if root.startswith(word))
            if roots:
                prefix_hits.append(f"{word}->{roots[0]}")
    return {
        "hits": hits,
        "prefix_hits": prefix_hits,
        "hit_count": len(hits) + len(prefix_hits),
        "command_hits": [word for word in hits if word in SHORT_COMMAND_WORDS],
        "operational_hits": [word for word in hits if word in SHORT_OPERATIONAL_WORDS],
        "number_word_hits": [word for word in hits if word in SHORT_NUMBER_WORDS],
    }


def _context_english_signal(context: dict[str, Any] | None) -> int:
    score = 0
    for side in ("previous", "next"):
        for item in (context or {}).get(side, []):
            if not isinstance(item, dict):
                continue
            analysis = normalize_short_fragment_for_detection(str(item.get("text", "")))
            score += min(2, _lexical_evidence(analysis)["hit_count"])
    return score


def classify_short_english_fragment(
    event: Event,
    output: str,
    context: dict[str, Any] | None = None,
    protected_terms: set[str] | None = None,
    source_language: str = "inglês",
) -> dict[str, Any]:
    """Conservatively classify a short residual without mutating its text."""
    source = normalize_short_fragment_for_detection(event.clean_text)
    candidate = normalize_short_fragment_for_detection(output)
    result: dict[str, Any] = {
        "status": NOT_SHORT_ENGLISH,
        "source": source,
        "output": candidate,
        "evidence": [],
        "retry_eligible": False,
    }
    if event.classification in SHORT_FRAGMENT_PROTECTED_CLASSES or event.classification not in SHORT_FRAGMENT_LINGUISTIC_CLASSES:
        result["evidence"].append(f"protected_class:{event.classification}")
        return result
    source_folded = " ".join(source["words"])
    output_folded = " ".join(candidate["words"])
    protected = {unicodedata.normalize("NFKC", term).casefold().strip() for term in (protected_terms or set()) if term}
    if source["visible"].casefold().strip() in protected or candidate["visible"].casefold().strip() in protected:
        result["evidence"].append("protected_term")
        return result
    if _code_only(candidate):
        result["evidence"].append("code_or_acronym_only")
        return result
    evidence = _lexical_evidence(candidate)
    source_evidence = _lexical_evidence(source)
    context_score = _context_english_signal(context)
    same_lexical = bool(source_folded and source_folded == output_folded)
    overlap = len(set(source["words"]) & set(candidate["words"])) / max(1, len(set(source["words"])))
    result.update({
        "lexical_evidence": evidence,
        "source_lexical_evidence": source_evidence,
        "context_english_score": context_score,
        "same_lexical": same_lexical,
        "source_output_overlap": round(overlap, 4),
    })
    # Non-English configured source: strong residue in the candidate is the
    # same failure class as an English residual fragment and feeds the same
    # bounded retry path (HIGH_CONFIDENCE).
    residue = source_residue_evidence(candidate["visible"] or output, source_language)
    if residue["count"]:
        result["source_residue"] = residue
        if source_residue_strong(residue, overlap):
            result["status"] = SHORT_ENGLISH_HIGH_CONFIDENCE
            result["retry_eligible"] = True
            result["evidence"].extend([
                f"source_residue:{residue['language']}:{','.join(residue['word_hits']) or '-'}",
                f"residue_patterns:{residue['pattern_hits']}",
            ])
            return result
    # A single title-cased unknown token is a name/term candidate, never an
    # automatic retry.  It remains visible to ordinary review via POSSIBLE.
    single_unknown_title = (
        len(candidate["words"]) == 1
        and not evidence["hit_count"]
        and bool(re.fullmatch(r"[A-Z][A-Za-z'\u2019-]+[!?]?", candidate["visible"].strip()))
    )
    if single_unknown_title:
        result["status"] = SHORT_ENGLISH_POSSIBLE
        result["evidence"].append("single_titlecase_name_or_term_candidate")
        return result
    high = False
    if same_lexical or overlap >= 0.60:
        high = any((
            evidence["hit_count"] >= 2,
            bool(evidence["command_hits"]),
            bool(evidence["prefix_hits"]),
            bool(evidence["operational_hits"] and (candidate["has_number"] or evidence["number_word_hits"])),
            bool(evidence["hit_count"] and candidate["interrupted"] and context_score >= 2),
        ))
    if high:
        result["status"] = SHORT_ENGLISH_HIGH_CONFIDENCE
        result["retry_eligible"] = True
        result["evidence"].extend([
            f"lexical_hits:{','.join(evidence['hits']) or '-'}",
            f"prefix_hits:{','.join(evidence['prefix_hits']) or '-'}",
            f"context_score:{context_score}",
            f"interrupted:{str(candidate['interrupted']).lower()}",
        ])
        return result
    if evidence["hit_count"] or (
        (same_lexical or overlap >= 0.60)
        and candidate["words"]
        and candidate["sentence_punctuation"]
    ):
        result["status"] = SHORT_ENGLISH_POSSIBLE
        result["evidence"].append("insufficient_for_automatic_retry")
    return result


class V223MemoryRunner(V222MemoryRunner):
    """V2.2.2 runner with bounded short-residual localized retry."""

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        # Keep the V2.2.0 memory client and all retrieval semantics, replacing
        # only its request envelope hook at this candidate boundary.
        previous_client = self.client
        self.client = V223MemoryClient(
            previous_client.config,
            self.calls,
            previous_client.glossary,
            previous_client.memory,
            previous_client.anime_series_id,
            previous_client.episode_id,
            previous_client.job_id,
        )
        self.v223_high_candidates: set[int] = set()
        self.v223_possible_candidates: set[int] = set()
        self.v223_short_retry_events: set[int] = set()
        self.v223_classifier_evidence: dict[int, dict[str, Any]] = {}

    def _set_model_result(self, event: Event, response: dict[str, Any], model: str) -> bool:
        valid = super()._set_model_result(event, response, model)
        if not valid:
            return False
        result = self.results[event.id]
        assessment = classify_short_english_fragment(
            event,
            result.final_text or "",
            self.contexts.get(event.id, {}),
            self.protected_terms,
            source_language=getattr(self.config, "source_language", "inglês"),
        )
        self.v223_classifier_evidence[event.id] = assessment
        if assessment["status"] == SHORT_ENGLISH_HIGH_CONFIDENCE:
            self.v223_high_candidates.add(event.id)
            for flag in (SHORT_ENGLISH_HIGH_CONFIDENCE, "SHORT_ENGLISH_RESIDUAL"):
                if flag not in result.flags:
                    result.flags.append(flag)
            result.retry_recommended = True
        elif assessment["status"] == SHORT_ENGLISH_POSSIBLE:
            self.v223_possible_candidates.add(event.id)
            if SHORT_ENGLISH_POSSIBLE not in result.flags:
                result.flags.append(SHORT_ENGLISH_POSSIBLE)
        return True

    def _attempt(
        self,
        units: list[Unit],
        simplified: bool = False,
        phase: str = "main",
        parent_call_id: str | None = None,
        *,
        attempt_type: str | None = None,
        logical_batch_id: str | None = None,
        batch_index: int | None = None,
    ) -> tuple[set[int], list[str]]:
        short_retry_ids = {
            event.id
            for unit in units
            for event in unit.events
            if SHORT_ENGLISH_HIGH_CONFIDENCE in self.results[event.id].flags
        }
        interrupted = (
            phase.startswith("retry")
            and len(units) == 1
            and len(units[0].events) == 1
            and units[0].events[0].id in self.v223_high_candidates
            and (
                normalize_short_fragment_for_detection(units[0].events[0].clean_text)["stuttered"]
                or normalize_short_fragment_for_detection(units[0].events[0].clean_text)["interrupted"]
            )
        )
        calls_before = len(self.calls)
        self.client.interrupted_dialogue_retry = interrupted
        try:
            outcome = super()._attempt(
                units, simplified, phase, parent_call_id,
                attempt_type=attempt_type, logical_batch_id=logical_batch_id, batch_index=batch_index,
            )
        finally:
            self.client.interrupted_dialogue_retry = False
        if short_retry_ids and phase != "initial":
            self.v223_short_retry_events.update(short_retry_ids)
            if len(self.calls) > calls_before:
                observation = self.calls[-1]
                observation.update({
                    "retry_reason": "SHORT_ENGLISH_RESIDUAL",
                    "reason": "SHORT_ENGLISH_RESIDUAL",
                    "residual_english_trigger": True,
                    "short_english_event_ids": sorted(short_retry_ids),
                })
        return outcome

    def run(self) -> dict[str, Any]:
        summary = super().run()
        original_by_id = {event.id: event for event in self.v221_original_events}
        final_residuals: list[int] = []
        for item in summary["results"]:
            event = original_by_id[item["id"]]
            assessment = classify_short_english_fragment(
                event,
                item.get("final_text") or "",
                self.contexts.get(event.id, {}),
                self.protected_terms,
                source_language=getattr(self.config, "source_language", "inglês"),
            )
            if assessment["status"] != SHORT_ENGLISH_HIGH_CONFIDENCE:
                item["flags"] = [
                    flag for flag in item.get("flags", [])
                    if flag not in {SHORT_ENGLISH_HIGH_CONFIDENCE, "SHORT_ENGLISH_RESIDUAL"}
                ]
                continue
            final_residuals.append(event.id)
            item["status"] = "failed"
            item["failure_reason"] = "SHORT_ENGLISH_RESIDUAL_EXHAUSTED"
            if "SHORT_ENGLISH_RESIDUAL" not in item.setdefault("flags", []):
                item["flags"].append("SHORT_ENGLISH_RESIDUAL")
        flags = Counter(
            flag.split(":", 1)[0]
            for item in summary["results"]
            for flag in item.get("flags", [])
        )
        adapter_critical = set(summary.get("critical_flags", []))
        if final_residuals:
            adapter_critical.add("SHORT_ENGLISH_RESIDUAL")
        summary.update({
            "resolved": sum(item["status"] == "resolved" for item in summary["results"]),
            "failed": sum(item["status"] != "resolved" for item in summary["results"]),
            "flags": dict(flags),
            "critical_flags": sorted(adapter_critical),
            "short_english_candidates": len(self.v223_high_candidates | self.v223_possible_candidates),
            "short_english_high_confidence": len(self.v223_high_candidates),
            "short_english_possible": len(self.v223_possible_candidates),
            "short_english_retries": len(self.v223_short_retry_events),
            "short_english_retry_event_ids": sorted(self.v223_short_retry_events),
            "short_english_residual_after_retry": len(final_residuals),
            "short_english_residual_event_ids": sorted(final_residuals),
            "short_english_classifier": {
                str(event_id): self.v223_classifier_evidence[event_id]
                for event_id in sorted(self.v223_high_candidates | self.v223_possible_candidates)
                if event_id in self.v223_classifier_evidence
            },
        })
        summary["eligible"] = summary["failed"] == 0
        summary["eligible_experimental"] = summary["eligible"] and not adapter_critical
        return summary


class V223MemoryClient(MemoryClient):
    """MemoryClient with an adapter-only instruction for interrupted speech.

    The base Client remains untouched.  The short instruction is appended to
    the existing retry request by a scoped request hook, while all model
    parameters, schema, context, and RetryBudget behavior remain inherited.
    """

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.interrupted_dialogue_retry = False

    def finalize_request_payload(self, payload, units, phase):
        payload = super().finalize_request_payload(payload, units, phase)
        if not self.interrupted_dialogue_retry:
            return payload
        payload = dict(payload)
        messages = list(payload.get("messages") or [])
        if messages:
            first = dict(messages[0])
            first["content"] = str(first.get("content") or "") + INTERRUPTED_DIALOGUE_RETRY_INSTRUCTION
            messages[0] = first
            payload["messages"] = messages
        return payload

    def call(self, units, events, contexts, simplified=False, phase="main"):
        found, issues, observation = super().call(
            units, events, contexts, simplified=simplified, phase=phase
        )
        if self.interrupted_dialogue_retry:
            observation["retry_specialization"] = "SHORT_INTERRUPTED_DIALOGUE_RETRY"
            observation["retry_instruction_chars"] = len(INTERRUPTED_DIALOGUE_RETRY_INSTRUCTION)
        return found, issues, observation


def translate_subtitle_file_v2_2_3(
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
        raise RuntimeError("V2.2.3 aceita somente ASS/SSA; saída não publicada")
    if output_path.exists():
        raise FileExistsError(f"a saída final já existe: {output_path.name}")
    started = time.perf_counter()
    config, merged_glossary = _config(subtitle_path, glossary)
    memory_root = Path(memory_db_root or os.environ.get("ANIME_SUBTITLE_LIBRARY_ROOT", "/app/state/anime-subtitle-library"))
    memory = TranslationMemory(memory_root)
    build = memory.sync_approved()
    original, events, profile = load_events(subtitle_path, merged_glossary)
    effective_job = job_id or f"v223-{output_path.name}-{int(time.time() * 1000)}"
    runner = V223MemoryRunner(events, profile, config, merged_glossary, memory, anime_series_id, episode_id, effective_job)
    summary = runner.run()
    summary["memory_build"] = build
    summary["pipeline"] = APPROVED_PIPELINE
    summary["model"] = config.model
    if not summary.get("eligible_experimental"):
        raise RuntimeError(json.dumps({
            "reason": "v2_2_3_not_eligible",
            "resolved": summary.get("resolved"),
            "events": summary.get("events"),
            "critical_flags": summary.get("critical_flags", []),
            "flags": summary.get("flags", {}),
            "short_english_residual_event_ids": summary.get("short_english_residual_event_ids", []),
            "retry_budget": summary.get("retry_budget", {}),
        }, ensure_ascii=False))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_tmp = tempfile.mkstemp(
        prefix=f".{output_path.name}.v2_2_3-",
        suffix=output_path.suffix,
        dir=str(output_path.parent),
    )
    os.close(fd)
    tmp_path = Path(raw_tmp)
    try:
        write_ass(original, events, summary, tmp_path)
        candidate = pysubs2.load(str(tmp_path))
        validation = validate_structure(
            original,
            candidate,
            {event.original_index for event in events},
            {event.original_index for event in events if is_multi_speaker(event)},
        )
        if not validation.get("valid"):
            raise RuntimeError(json.dumps({
                "reason": "v2_2_3_structure_invalid",
                "issues": validation.get("issues", [])[:30],
            }, ensure_ascii=False))
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
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    keys = (
        "events", "linguistic_events", "vector_only_events", "mixed_vector_text_events",
        "empty_visual_segment_events", "resolved", "failed", "total_ollama_calls",
        "initial_ollama_calls", "actual_retry_ollama_calls", "events_retried", "flags",
        "critical_flags", "structural_failures", "line_break_inside_word_count",
        "line_break_count_mismatch_count", "short_english_candidates",
        "short_english_high_confidence", "short_english_possible", "short_english_retries",
        "short_english_retry_event_ids", "short_english_residual_after_retry",
        "short_english_residual_event_ids", "memory_candidates_found", "memory_items_used",
        "memory_conflicts", "memory_misses", "retry_budget",
    )
    result = {key: summary.get(key) for key in keys}
    result.update({
        "pipeline": APPROVED_PIPELINE,
        "model": config.model,
        "calls": summary.get("total_ollama_calls", 0),
        "retry_calls": summary.get("actual_retry_ollama_calls", 0),
        "elapsed_client_seconds": round(time.perf_counter() - started, 3),
        "output": output_path.name,
    })
    print("V2_2_3_SUMMARY " + json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result


translate_subtitle_file = translate_subtitle_file_v2_2_3
