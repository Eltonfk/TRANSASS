"""Candidate V2.2.0 adapter: V2.1.3 plus bounded approved memory context."""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

import pysubs2

from pipeline_v2_1_3 import (
    Client,
    Config,
    Runner,
    is_multi_speaker,
    load_events,
    validate_structure,
    write_ass,
)
from production_v2_1_3_adapter import APPROVED_CONFIG, APPROVED_MODEL, _fsync_file, _merged_glossary
from translation_memory import TranslationMemory


APPROVED_PIPELINE = "v2_2_0"


class MemoryClient(Client):
    """Inject only quoted, relevant memory into the existing context field."""

    def __init__(self, config: Config, calls: list[dict[str, Any]], glossary: dict[str, str], memory: TranslationMemory, anime_series_id: int | None, episode_id: int | None, job_id: str):
        super().__init__(config, calls, glossary=glossary)
        self.memory = memory
        self.anime_series_id = anime_series_id
        self.episode_id = episode_id
        self.job_id = job_id
        self.cache: dict[int, dict[str, Any]] = {}
        self.metrics = {
            "memory_candidates_found": 0,
            "memory_items_used": 0,
            "memory_exact_hits": 0,
            "memory_fuzzy_hits": 0,
            "memory_conflicts": 0,
            "memory_misses": 0,
            "memory_scope": "ANIME" if anime_series_id else "NONE",
            "memory_lookup_ms": 0.0,
            "memory_event_ids": {},
        }

    def _retrieve(self, event, context: dict[str, Any]) -> dict[str, Any]:
        if event.id in self.cache:
            return self.cache[event.id]
        result = self.memory.retrieve(
            self.anime_series_id,
            event.clean_text,
            context_before=[item.get("text", "") for item in context.get("previous", [])],
            context_after=[item.get("text", "") for item in context.get("next", [])],
            limit=3,
            max_chars=1800,
        )
        self.cache[event.id] = result
        self.metrics["memory_lookup_ms"] += result.get("lookup_ms", 0.0)
        items = result.get("items", [])
        self.metrics["memory_candidates_found"] += len(items)
        self.metrics["memory_conflicts"] += int(result.get("conflicts", 0))
        if not items:
            self.metrics["memory_misses"] += 1
        for item in items:
            self.metrics["memory_items_used"] += 1
            if item["match_type"] in {"EXACT", "NORMALIZED_EXACT"}:
                self.metrics["memory_exact_hits"] += 1
            else:
                self.metrics["memory_fuzzy_hits"] += 1
            self.metrics["memory_event_ids"].setdefault(str(event.id), []).append({"memory_item_id": item["memory_item_id"], "match_type": item["match_type"], "score": item["score"]})
            if self.memory.record_usage(memory_item_id=item["memory_item_id"], job_id=self.job_id, episode_id=self.episode_id, event_id=event.id, match_type=item["match_type"], score=item["score"]):
                # The count above is the number of effective examples in the
                # job, not a count of recursive retry calls.
                pass
        return result

    def call(self, units, events, contexts, simplified=False, phase="main"):
        enriched: dict[int, dict[str, Any]] = {}
        selected: dict[int, dict[str, Any]] = {}
        for unit in units:
            for event in unit.events:
                context = dict(contexts[event.id])
                retrieval = self._retrieve(event, context)
                context.update(self.memory.safe_prompt_context(retrieval))
                enriched[event.id] = context
                selected[event.id] = retrieval
        found, issues, observation = super().call(units, events, enriched, simplified=simplified, phase=phase)
        observation["memory_item_ids"] = sorted({item["memory_item_id"] for retrieval in selected.values() for item in retrieval.get("items", [])})
        observation["memory_scope"] = self.metrics["memory_scope"]
        return found, issues, observation


class MemoryRunner(Runner):
    def __init__(self, events, profile, config, glossary, memory: TranslationMemory, anime_series_id: int | None, episode_id: int | None, job_id: str):
        super().__init__(events, profile, config, glossary)
        self.client = MemoryClient(config, self.calls, glossary, memory, anime_series_id, episode_id, job_id)

    def run(self) -> dict[str, Any]:
        summary = super().run()
        summary.update(self.client.metrics)
        summary["memory_lookup_ms"] = round(float(summary.get("memory_lookup_ms", 0.0)), 3)
        return summary


def _config(subtitle_path: Path, folder_glossary: dict[str, str] | None) -> tuple[Config, dict[str, str]]:
    ollama_url = os.environ.get("TRANSLATOR_OLLAMA_URL", "").strip()
    model = os.environ.get("TRANSLATOR_OLLAMA_MODEL", APPROVED_MODEL).strip()
    if not ollama_url:
        raise RuntimeError("TRANSLATOR_OLLAMA_URL não configurada para V2.2.0")
    if model != APPROVED_MODEL:
        raise RuntimeError(f"V2.2.0 exige {APPROVED_MODEL}; modelo configurado: {model or '<vazio>'}")
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


def translate_subtitle_file_v2_2_0(
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
        raise RuntimeError("V2.2.0 aceita somente ASS/SSA; saída não publicada")
    if output_path.exists():
        raise FileExistsError(f"a saída final já existe: {output_path.name}")
    started = time.perf_counter()
    config, merged_glossary = _config(subtitle_path, glossary)
    memory_root = Path(memory_db_root or os.environ.get("ANIME_SUBTITLE_LIBRARY_ROOT", "/app/state/anime-subtitle-library"))
    memory = TranslationMemory(memory_root)
    build = memory.sync_approved()
    original, events, profile = load_events(subtitle_path, merged_glossary)
    effective_job = job_id or f"v220-{output_path.name}-{int(time.time() * 1000)}"
    runner = MemoryRunner(events, profile, config, merged_glossary, memory, anime_series_id, episode_id, effective_job)
    summary = runner.run()
    summary["memory_build"] = build
    summary["pipeline"] = APPROVED_PIPELINE
    summary["model"] = config.model
    if not summary.get("eligible_experimental"):
        raise RuntimeError(json.dumps({"reason": "v2_2_0_not_eligible", "resolved": summary.get("resolved"), "events": summary.get("events"), "critical_flags": summary.get("critical_flags", []), "flags": summary.get("flags", []), "memory": {key: summary.get(key) for key in ("memory_candidates_found", "memory_items_used", "memory_conflicts", "memory_misses")}}, ensure_ascii=False))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_tmp = tempfile.mkstemp(prefix=f".{output_path.name}.v2_2_0-", suffix=output_path.suffix, dir=str(output_path.parent)); os.close(fd)
    tmp_path = Path(raw_tmp)
    try:
        write_ass(original, events, summary, tmp_path)
        candidate = pysubs2.load(str(tmp_path))
        validation = validate_structure(original, candidate, {event.original_index for event in events}, {event.original_index for event in events if is_multi_speaker(event)})
        if not validation.get("valid"):
            raise RuntimeError(json.dumps({"reason": "v2_2_0_structure_invalid", "issues": validation.get("issues", [])[:30]}, ensure_ascii=False))
        if output_path.exists():
            raise FileExistsError(f"a saída final apareceu durante o job: {output_path.name}")
        _fsync_file(tmp_path); os.replace(tmp_path, output_path)
        try:
            directory_fd = os.open(output_path.parent, os.O_RDONLY)
            try: os.fsync(directory_fd)
            finally: os.close(directory_fd)
        except OSError:
            pass
    except Exception:
        tmp_path.unlink(missing_ok=True); raise
    result = {key: summary.get(key) for key in ("events", "resolved", "total_ollama_calls", "initial_ollama_calls", "actual_retry_ollama_calls", "events_retried", "flags", "critical_flags", "profile_fingerprint", "memory_candidates_found", "memory_items_used", "memory_exact_hits", "memory_fuzzy_hits", "memory_conflicts", "memory_misses", "memory_scope", "memory_lookup_ms", "memory_build", "memory_event_ids")}
    result.update({"pipeline": APPROVED_PIPELINE, "model": config.model, "calls": summary.get("total_ollama_calls", 0), "retry_calls": summary.get("actual_retry_ollama_calls", 0), "elapsed_client_seconds": round(time.perf_counter() - started, 3), "output": output_path.name})
    print("V2_2_0_SUMMARY " + json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result


translate_subtitle_file = translate_subtitle_file_v2_2_0
