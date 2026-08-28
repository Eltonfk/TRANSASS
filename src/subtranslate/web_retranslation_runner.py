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
    # BaseTransport preenche base_url com 127.0.0.1 antes de retornar. A URL
    # de rede do container precisa ser injetada antes dessa construção.
    if provider == "ollama" and not str(section.get("base_url") or "").strip():
        ollama_url = os.environ.get("TRANSLATOR_OLLAMA_URL", "").strip()
        if ollama_url:
            section["base_url"] = ollama_url.rsplit("/api/chat", 1)[0]
    if not section.get("api_key") and provider in keys and keys[provider]:
        section["api_key"] = keys[provider]
    try:
        return transport_from_config(section, {"model": section.get("model")})
    except Exception:
        return None


def _project_v238_summary(result: dict) -> dict:
    """G3: projeta o resultado do orchestrator v2_3_8 para o formato
    exigido pelo web layer, incluindo v238_metrics (mesma lógica do app.py)."""
    stages = result.get("stages") if isinstance(result.get("stages"), list) else []
    karaoke = result.get("karaoke") if isinstance(result.get("karaoke"), dict) else {}
    primary_ledger = result.get("primary_ledger") if isinstance(result.get("primary_ledger"), list) else []
    failures = karaoke.get("failures") if isinstance(karaoke.get("failures"), list) else []
    structural = karaoke.get("structural_failures") if isinstance(karaoke.get("structural_failures"), list) else []
    unresolved = [row for row in primary_ledger
                  if isinstance(row, dict) and str(row.get("status", "")).upper() in {"BLOCKED", "SUSPECT"}]
    events = len(primary_ledger) if primary_ledger else int(karaoke.get("song_units") or 0)
    resolved = max(0, events - len(unresolved)) if events else 0
    # M7/SKIPPED_ALLOWED: unidades BLOCKED/SUSPECT permitidas (não publicáveis).
    llama_phase = result.get("llama_phase") if isinstance(result.get("llama_phase"), dict) else {}
    skipped_allowed = str(llama_phase.get("state", "")).upper() == "SKIPPED_ALLOWED"
    ok = not failures and not structural and (not unresolved or skipped_allowed)
    status = "COMPLETED" if ok else "FAILED"
    last_stage = stages[-1].get("id") if stages and isinstance(stages[-1], dict) else "FULL_TRANSLATION_V238"
    budget = result.get("operation_budget") if isinstance(result.get("operation_budget"), dict) else {}
    qwen_max = int(budget.get("qwen_physical_maximum") or 131)
    qwen_reserved = int(budget.get("qwen_reserved") or 0)
    calls = int(result.get("calls", 0) or 0)
    flags: dict = {}
    critical_flags: list[str] = []
    if unresolved and not skipped_allowed:
        flags["v238_unresolved_units"] = len(unresolved)
        critical_flags.append("v238_unresolved_units")
    elif unresolved and skipped_allowed:
        flags["v238_skipped_allowed_units"] = len(unresolved)
    return {
        "status": status,
        "stage": last_stage,
        "events": events,
        "resolved": resolved,
        "unresolved": len(unresolved),
        "flags": flags,
        "critical_flags": critical_flags,
        "stages": stages,
        "v238_metrics": {
            "calls": calls,
            "physical_client_calls": calls,
            "model_generation_calls": calls,
            "provider_requests": calls,
            "prompt_tokens": None,
            "completion_tokens": None,
            "elapsed_seconds": result.get("pipeline_wall_seconds"),
            "budget_used": int(budget.get("total_reserved", 0) or 0),
            "budget_remaining": max(0, qwen_max - qwen_reserved),
            "provider_mode": "LIVE_CAPTURED",
            "fallback_used": False,
        },
        "calls": calls,
        "retry_calls": int(result.get("retry_calls", 0) or 0),
    }


def _run_pipeline(args, pipeline: str, transport: Any | None, source_language: str) -> dict[str, Any]:
    scratch = Path(tempfile.mkdtemp(prefix=".web-retranslation-", dir="/tmp"))
    try:
        safe_series = re.sub(r"[^\w .!'-]+", "_", args.series_title).strip() or "Anime"
        safe_episode = re.sub(r"[^\w .!'-]+", "_", args.episode_title).strip() or "Episode"
        semantic_dir = scratch / safe_series
        semantic_dir.mkdir(parents=True, exist_ok=True)
        semantic_source = semantic_dir / (safe_episode + args.source.suffix.lower())
        semantic_source.symlink_to(args.source)
        if pipeline == "v2_3_8":
            # G1: caminho V2.3.8 exige execution context completo
            # (response_provider, base_materializer, transport, identidade).
            import hashlib
            import uuid

            from web_execution_context import build_v238_execution_context
            from web_durable_provider import WebDurableResponseProvider

            transport_config = load_transport_config(TRANSPORT_CONFIG_PATH)
            job_root = Path(os.environ.get("TRANSLATOR_WEB_STATE_DIR", "/app/state")) / "v238-runs" / str(args.job_id)
            capture_root = job_root / "captures"
            capture_root.mkdir(parents=True, exist_ok=True)
            provider = WebDurableResponseProvider(
                transport_config, mode="LIVE_CAPTURED", capture_root=capture_root,
            )
            glossary = {}
            glossary_hash = hashlib.sha256(b"no-glossary").hexdigest()
            ctx = build_v238_execution_context(
                job={"id": args.job_id, "episode_id": args.episode_id,
                     "anime_series_id": args.anime_series_id},
                transport_config=transport_config,
                source_language=source_language,
                operation_id=uuid.uuid4().hex,
                execution_mode="LIVE_CAPTURED",
                capture_root=capture_root,
                authorized_primary_models=transport_config.get("authorized_primary_models") or ["qwen", "gemini"],
                glossary=glossary,
                glossary_hash=glossary_hash,
                stage_completion_root=job_root / "completions",
                checkpoint_root=job_root / "checkpoints",
                job_id=args.job_id,
                prompt_schema_hash=os.environ.get("PROMPT_SCHEMA_HASH") or "v238-rc7b1",
                configuration_hash=os.environ.get("CONFIGURATION_HASH") or "v238-web-1",
                candidate_commit=os.environ.get("CANDIDATE_COMMIT") or "7eb7b5d",
                candidate_image_id=os.environ.get("CANDIDATE_IMAGE_ID") or "v2.4.9-track2-v238",
            )
            ctx["response_provider"] = provider
            ctx["operation"] = "RETRANSLATE"
            ctx["defer_intermediate_cleanup"] = False
            if transport is not None:
                # Ollama: base_url default 127.0.0.1 é inacessível no container.
                if str(getattr(transport, "name", "")) == "ollama" and not getattr(transport, "base_url", None):
                    ollama_url = os.environ.get("TRANSLATOR_OLLAMA_URL", "")
                    if ollama_url:
                        transport.base_url = ollama_url.rsplit("/api/chat", 1)[0]
                ctx["transport"] = transport
            result = execute_pipeline_plan(pipeline, semantic_source, args.output, ctx)
        else:
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
    parser.add_argument("--no-fallback", action="store_true")
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
    if not args.no_fallback and (not isinstance(result, dict) or not args.output.is_file()) and fallback is not None:
        # Primary failed to produce the output: retry once with the fallback.
        used_fallback = True
        result = _run_pipeline(args, pipeline, fallback, source_language)

    internal = (result or {}).get("_internal") if isinstance(result, dict) else None
    stage_handle = Path(internal["stage_artifact_path"]).name if isinstance(internal, dict) and internal.get("stage_artifact_path") else None
    stage_sha256 = internal.get("stage_sha256") if isinstance(internal, dict) else None
    if pipeline == "v2_3_8":
        # G3: projeção V2.3.8 com v238_metrics (o public_summary não projeta).
        result = _project_v238_summary(result)
    else:
        result = public_summary(result, stage_handle=stage_handle, stage_sha256=stage_sha256)
    result["pipeline"] = pipeline
    result["output"] = args.output.name
    result["transport_used"] = "fallback" if used_fallback else "primary"
    print("WEB_RETRANSLATION_SUMMARY " + json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
