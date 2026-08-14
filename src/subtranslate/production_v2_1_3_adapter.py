"""Production adapter for the approved V2.1.3 engine.

This module deliberately owns only the boundary between the existing media
workflow and the laboratory-approved engine.  The legacy translator remains
in ``anime_subtitle_translator.py`` and is selected unless the job-start
feature flag explicitly requests ``v2_1_3``.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

import pysubs2

from pipeline_v2_1_3 import (
    Config,
    Runner,
    is_multi_speaker,
    load_events,
    load_glossary,
    validate_structure,
    write_ass,
)


APPROVED_MODEL = "qwen3.5:9b"
APPROVED_PIPELINE = "v2_1_3"
APPROVED_CONFIG = {
    "think": False,
    "temperature": 0.0,
    "num_ctx": 4096,
    "num_predict": 384,
    "keep_alive": "30m",
    "timeout_seconds": 240.0,
    "batch_target_size": 8,
    "context_before": 3,
    "context_after": 3,
    "context_budget_tokens": 1100,
    "context_max_chars": 2600,
    "scene_gap_ms": 6000,
    "max_retries": 2,
}


def _merged_glossary(folder_glossary: dict[str, str] | None) -> dict[str, str]:
    """Merge approved terms with the existing per-folder glossary.

    The approved file is intentionally small.  Folder-specific terms win so
    existing user decisions are not silently replaced.
    """
    approved_path = Path(__file__).with_name("glossaries") / "v2_1_2_glossary.json"
    approved = load_glossary(approved_path) if approved_path.is_file() else {}
    approved.update(folder_glossary or {})
    return approved


def _config(subtitle_path: Path, folder_glossary: dict[str, str] | None) -> tuple[Config, dict[str, str]]:
    ollama_url = os.environ.get("TRANSLATOR_OLLAMA_URL", "").strip()
    model = os.environ.get("TRANSLATOR_OLLAMA_MODEL", APPROVED_MODEL).strip()
    if not ollama_url:
        raise RuntimeError("TRANSLATOR_OLLAMA_URL não configurada para V2.1.3")
    if model != APPROVED_MODEL:
        raise RuntimeError(f"V2.1.3 exige {APPROVED_MODEL}; modelo configurado: {model or '<vazio>'}")
    glossary = _merged_glossary(folder_glossary)
    values = dict(APPROVED_CONFIG)
    values.update({
        "ollama_url": ollama_url,
        "model": model,
        "series_title": subtitle_path.parent.name,
        "episode_title": subtitle_path.stem,
        "glossary_path": str(Path(__file__).with_name("glossaries") / "v2_1_2_glossary.json"),
        # Match the production image, which deliberately has no optional
        # system dictionary installed.  The detector must work from its
        # built-in conservative vocabulary alone.
        "english_dictionary_path": "/nonexistent/american-english",
    })
    # Keep the existing timeout variable as the single operational timeout
    # source; the approved value remains the safe default.
    if os.environ.get("TRANSLATOR_OLLAMA_TIMEOUT"):
        values["timeout_seconds"] = float(os.environ["TRANSLATOR_OLLAMA_TIMEOUT"])
    if os.environ.get("V213_RETRY_BUDGET_CALLS"):
        values["retry_budget_calls"] = int(os.environ["V213_RETRY_BUDGET_CALLS"])
    if os.environ.get("V213_DIAGNOSTIC_CAPTURE"):
        values["diagnostic_capture"] = os.environ["V213_DIAGNOSTIC_CAPTURE"].lower() in {"1", "true", "yes"}
    if os.environ.get("V213_HARD_STOP_CALLS"):
        values["diagnostic_hard_stop_calls"] = int(os.environ["V213_HARD_STOP_CALLS"])
    return Config(**values), glossary


def _fsync_file(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def translate_subtitle_file_v2_1_3(
    subtitle_path: Path,
    output_path: Path,
    glossary: dict[str, str] | None = None,
) -> dict:
    """Translate one ASS/SSA file and publish it only after full validation."""
    if subtitle_path.suffix.lower() not in {".ass", ".ssa"}:
        raise RuntimeError("V2.1.3 em produção aceita somente ASS/SSA; saída não publicada")
    if output_path.exists():
        raise FileExistsError(f"a saída final já existe: {output_path.name}")

    started = time.perf_counter()
    config, merged_glossary = _config(subtitle_path, glossary)
    original, events, profile = load_events(subtitle_path, merged_glossary)
    runner = Runner(events, profile, config, merged_glossary)
    summary = runner.run()
    if not summary.get("eligible_experimental"):
        raise RuntimeError(json.dumps({
            "reason": "v2_1_3_not_eligible",
            "resolved": summary.get("resolved"),
            "events": summary.get("events"),
            "critical_flags": summary.get("critical_flags", []),
            "flags": summary.get("flags", {}),
        }, ensure_ascii=False))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_tmp = tempfile.mkstemp(
        prefix=f".{output_path.name}.v2_1_3-",
        suffix=output_path.suffix,
        dir=str(output_path.parent),
    )
    os.close(fd)
    tmp_path = Path(raw_tmp)
    try:
        # The engine owns ASS reconstruction; this adapter owns the temporary
        # file, validation, fsync and final same-directory rename.
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
                "reason": "v2_1_3_structure_invalid",
                "issues": validation.get("issues", [])[:30],
            }, ensure_ascii=False))
        if output_path.exists():
            raise FileExistsError(f"a saída final apareceu durante o job: {output_path.name}")
        _fsync_file(tmp_path)
        os.replace(tmp_path, output_path)
        try:
            directory_fd = os.open(output_path.parent, os.O_RDONLY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    result = {
        "pipeline": APPROVED_PIPELINE,
        "model": config.model,
        "events": summary.get("events", 0),
        "resolved": summary.get("resolved", 0),
        "calls": summary.get("total_ollama_calls", 0),
        "initial_calls": summary.get("initial_ollama_calls", 0),
        "retry_calls": summary.get("actual_retry_ollama_calls", 0),
        "events_retried": summary.get("events_retried", 0),
        "flags": summary.get("flags", {}),
        "critical_flags": summary.get("critical_flags", []),
        "profile_fingerprint": profile.get("fingerprint"),
        "elapsed_client_seconds": round(time.perf_counter() - started, 3),
        "output": output_path.name,
    }
    for key in (
        "song_lyrics_preserved_count",
        "song_ambiguous_count",
        "romaji_preserved_count",
        "possible_untranslated_count",
        "confirmed_untranslated_count",
        "untranslated_retry_count",
        "optional_resource_status",
    ):
        if key in summary:
            result[key] = summary[key]
    print("V2_1_3_SUMMARY " + json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result


translate_subtitle_file = translate_subtitle_file_v2_1_3
