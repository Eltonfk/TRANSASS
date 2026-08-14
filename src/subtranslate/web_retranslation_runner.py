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
            },
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    internal = (result or {}).get("_internal") if isinstance(result, dict) else None
    stage_handle = Path(internal["stage_artifact_path"]).name if isinstance(internal, dict) and internal.get("stage_artifact_path") else None
    stage_sha256 = internal.get("stage_sha256") if isinstance(internal, dict) else None
    result = public_summary(result, stage_handle=stage_handle, stage_sha256=stage_sha256)
    result["pipeline"] = pipeline
    result["output"] = args.output.name
    print("WEB_RETRANSLATION_SUMMARY " + json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
