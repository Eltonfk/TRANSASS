#!/usr/bin/env python3
"""Run one controlled retranslation from an archived source.

The command has no publication side effect.  It is intentionally a thin
adapter dispatcher and keeps all linguistic semantics in the selected core.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from pipeline_orchestrator import execute_pipeline_plan
from pipeline_lineage import public_summary
from transport_providers import transport_from_config
from transport_config_store import load_transport_config

TRANSPORT_CONFIG_PATH = Path(os.environ.get(
    "TRANSPORT_CONFIG_PATH", "/app/state/transport_config.json"))


def _provider_for(engine: dict[str, Any] | None, keys: dict[str, str]) -> Any | None:
    if not engine:
        return None
    section = dict(engine)
    provider = str(section.get("provider", "")).lower()
    if not section.get("api_key") and provider in keys and keys[provider]:
        section["api_key"] = keys[provider]
    try:
        return transport_from_config(section, {"model": section.get("model")})
    except Exception:
        return None


def _run_pipeline(args, pipeline: str, transport: Any | None, source_language: str) -> dict[str, Any]:
    scratch = Path(tempfile.mkdtemp(prefix=".web-retranslation-", dir="/tmp"))
    try:
        safe_series = re.sub(r"[^\w .!'-]+", "_", args.series_title).strip() or "Anime"
        safe_episode = re.sub(r"[^\w .!'-]+", "_", args.episode_title).strip() or "Episode"
        semantic_dir = scratch / safe_series
        semantic_dir.mkdir(parents=True, exist_ok=True)
        semantic_source = semantic_dir / (safe_episode + args.source.suffix.lower())
        semantic_source.symlink_to(args.source)
        result = execute_pipeline_plan(
            pipeline, semantic_source, args.output,
            {
                "operation": "RETRANSLATE",
                "memory_root": args.memory_root,
                "anime_series_id": args.anime_series_id,
                "episode_id": args.episode_id,
                "job_id": args.job_id,
                "model_override": os.environ.get("TRANSLATOR_OLLAMA_MODEL"),
                "ollama_url": os.environ.get("TRANSLATOR_OLLAMA_URL"),
                "defer_intermediate_cleanup": pipeline == "v2_3_0",
                "transport": transport,
                "source_language": source_language,
            },
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--memory-root", type=Path)
    parser.add_argument("--anime-series-id", type=int)
    parser.add_argument("--episode-id", type=int)
    parser.add_argument("--job-id", default="web-retranslation")
    parser.add_argument("--pipeline", default=os.environ.get("TRANSLATOR_PIPELINE", "legacy"))
    parser.add_argument("--series-title", default="Anime")
    parser.add_argument("--episode-title", default="Episode")
    args = parser.parse_args()
    if args.source.suffix.lower() not in {".ass", ".ssa"}:
        raise SystemExit("FONTE ORIGINAL NÃO DISPONÍVEL: formato não suportado")
    if args.output.exists():
        raise SystemExit("saída de retradução já existe")
    pipeline = str(args.pipeline).lower()

    # Transport config from the web UI: primary engine, optional fallback.
    transport_config = load_transport_config(TRANSPORT_CONFIG_PATH)
    keys = transport_config.get("keys") or {}
    primary = _provider_for(transport_config.get("primary"), keys)
    fallback = _provider_for(transport_config.get("fallback"), keys)
    # Source language precedence: the per-job environment injected by the web
    # queue wins, then the global transport config, then English.  The env
    # must come first because the transport store always materializes a
    # non-empty default that would otherwise mask the per-job selection.
    source_language = (
        os.environ.get("TRANSLATOR_SOURCE_LANGUAGE")
        or transport_config.get("source_language")
        or "inglês"
    )

    result = _run_pipeline(args, pipeline, primary, source_language)
    used_fallback = False
    if (not isinstance(result, dict) or not args.output.is_file()) and fallback is not None:
        # Primary failed to produce the output: retry once with the fallback.
        used_fallback = True
        result = _run_pipeline(args, pipeline, fallback, source_language)

    internal = (result or {}).get("_internal") if isinstance(result, dict) else None
    stage_handle = Path(internal["stage_artifact_path"]).name if isinstance(internal, dict) and internal.get("stage_artifact_path") else None
    stage_sha256 = internal.get("stage_sha256") if isinstance(internal, dict) else None
    result = public_summary(result, stage_handle=stage_handle, stage_sha256=stage_sha256)
    result["pipeline"] = pipeline
    result["output"] = args.output.name
    result["transport_used"] = "fallback" if used_fallback else "primary"
    print("WEB_RETRANSLATION_SUMMARY " + json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
