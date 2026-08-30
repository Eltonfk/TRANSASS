#!/usr/bin/env python3
"""Run one controlled retranslation from an archived source.

The command has no publication side effect.  It is intentionally a thin
adapter dispatcher and keeps all linguistic semantics in the selected core.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import tempfile
from types import SimpleNamespace
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
    provider_metrics = result.get("provider_metrics") if isinstance(result.get("provider_metrics"), dict) else result.get("metrics")
    provider_metrics = provider_metrics if isinstance(provider_metrics, dict) else {}
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
            "physical_client_calls": int(provider_metrics.get("physical_client_calls", calls) or 0),
            "model_generation_calls": int(provider_metrics.get("model_generation_calls", calls) or 0),
            "provider_requests": int(provider_metrics.get("provider_requests", calls) or 0),
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
            if transport is not None:
                transport_provider = getattr(transport, "name", "ollama")
                transport_model = getattr(transport, "model", None)
                primary = transport_config.get("primary") or {}
                fallback = transport_config.get("fallback") or {}
                if (str(fallback.get("provider", "")).lower() == str(transport_provider).lower()
                        and str(fallback.get("model", "")) == str(transport_model or "")):
                    active_digest = (fallback.get("model_digest")
                                     or transport_config.get("fallback_model_digest"))
                else:
                    active_digest = (getattr(transport, "model_digest", None)
                                     or primary.get("model_digest")
                                     or transport_config.get("primary_model_digest")
                                     or transport_config.get("model_digest"))
                active = {"provider": getattr(transport, "name", "ollama"),
                          "model": getattr(transport, "model", None),
                          "base_url": getattr(transport, "base_url", None),
                          # The web transport object does not necessarily
                          # carry the canonical digest.  Preserve the
                          # persisted model identity when the provider omits
                          # it instead of silently turning LIVE_CAPTURED
                          # into an identity-missing failure.
                          "model_digest": active_digest}
                transport_config = dict(transport_config)
                transport_config["primary"] = active
                transport_config["model_digest"] = active["model_digest"]
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
                prompt_schema_hash=os.environ.get("PROMPT_SCHEMA_HASH"),
                configuration_hash=os.environ.get("CONFIGURATION_HASH"),
                candidate_commit=os.environ.get("CANDIDATE_COMMIT"),
                candidate_image_id=os.environ.get("CANDIDATE_IMAGE_ID"),
            )
            # Gemini profile: aplica modelo válido e budget, fallback se sem key
            primary_provider = str((transport_config.get("primary") or {}).get("provider", "")).lower()
            gemini_profile = transport_config.get("gemini_profile") or {}
            if primary_provider == "gemini" and gemini_profile.get("enabled", True):
                if not (transport_config.get("keys") or {}).get("gemini"):
                    print("AVISO: Gemini sem API key — fallback para ollama", flush=True)
                    fallback = transport_config.get("fallback") or {"provider": "ollama", "model": "qwen3.5:9b"}
                    if fallback and fallback.get("provider"):
                        transport_config["primary"] = dict(fallback)
                        ctx["model"] = str(fallback.get("model") or "")
                        # não aplica profile gemini
                        # segue para response_provider que será recriado? provider já criado com gemini, mas ctx model será ollama — precisa recriar provider?
                        # recria provider com fallback
                        from web_durable_provider import WebDurableResponseProvider
                        provider = WebDurableResponseProvider(transport_config, mode="LIVE_CAPTURED", capture_root=capture_root)
                        ctx["response_provider"] = provider
                        ctx["model"] = str(fallback.get("model") or "")
                    # pula aplicação do profile
                else:
                    # Corrige modelo inválido (ex: 3.6-flash) para o do profile
                    gemini_model = str(gemini_profile.get("model", "gemini-1.5-flash")).strip()
                    if gemini_model and str((transport_config.get("primary") or {}).get("model") or "") != gemini_model:
                        if "3.6" in str(transport_config.get("primary", {}).get("model") or ""):
                            transport_config["primary"]["model"] = gemini_model
                            ctx["model"] = gemini_model
                    from v238_llama_policy import OperationCallBudget
                    retry_budget = max(1, int(gemini_profile.get("retry_budget", 32)))
                    if retry_budget < 16:
                        retry_budget = 32
                    if "operation_budget" not in ctx or ctx.get("operation_budget") is None:
                        ctx["operation_budget"] = OperationCallBudget(qwen_physical_maximum=retry_budget, llama_generation_maximum=1)
                        ctx["gemini_profile"] = gemini_profile
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
            # Adapters emit full ledgers for CLI diagnostics.  The web runner
            # must not stream those multi-megabyte JSON blobs to its caller:
            # a bounded pipe can block the translation process before it
            # reaches the durable completion marker.  The runner emits its
            # compact public summary below instead.
            with open(os.devnull, "w", encoding="utf-8") as quiet_output:
                with contextlib.redirect_stdout(quiet_output):
                    result = execute_pipeline_plan(pipeline, semantic_source, args.output, ctx)
        else:
            with open(os.devnull, "w", encoding="utf-8") as quiet_output:
                with contextlib.redirect_stdout(quiet_output):
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


def _selective_event_ids(raw: str) -> list[int]:
    values = []
    for token in str(raw or "").split(","):
        token = token.strip()
        if token:
            values.append(int(token))
    return sorted(set(values))


def _run_selective_retranslation(args, pipeline: str, transport: Any | None,
                                 source_language: str) -> dict[str, Any]:
    """Retry only explicitly failed event IDs and merge atomically.

    The primary artifact remains the source of truth for every event that is
    not listed.  This path is intentionally explicit; normal web fallback is
    reserved for transport failure and must not repeat a whole episode after
    a linguistic validation failure.
    """
    import pysubs2

    event_ids = _selective_event_ids(args.failed_event_ids)
    if not event_ids:
        raise SystemExit("nenhum event_id seletivo informado")
    if args.selective_base_output is None or not args.selective_base_output.is_file():
        raise SystemExit("artefato primário seletivo não encontrado")
    if args.output.exists():
        raise SystemExit("saída de retradução já existe")
    original = pysubs2.load(str(args.source), format="ass")
    base = pysubs2.load(str(args.selective_base_output), format="ass")
    missing = [event_id for event_id in event_ids if event_id < 0 or event_id >= len(original.events)]
    if missing or len(original.events) != len(base.events):
        raise SystemExit("cardinalidade incompatível para reprocessamento seletivo")
    subset = pysubs2.SSAFile()
    subset.info = dict(original.info)
    subset.styles = dict(original.styles)
    for event_id in event_ids:
        subset.events.append(original.events[event_id])
    with tempfile.TemporaryDirectory(prefix=".web-selective-", dir="/tmp") as work:
        subset_source = Path(work) / "failed-events.ass"
        subset_output = Path(work) / "failed-events.out.ass"
        subset.save(str(subset_source), format="ass")
        subset_args = SimpleNamespace(**vars(args))
        subset_args.source = subset_source
        subset_args.output = subset_output
        subset_args.no_fallback = True
        result = _run_pipeline(subset_args, pipeline, transport, source_language)
        if not subset_output.is_file():
            raise SystemExit("reprocessamento seletivo não produziu saída")
        ledger = result.get("primary_ledger") if isinstance(result, dict) else None
        unresolved = [row for row in (ledger or [])
                      if isinstance(row, dict) and str(row.get("status", "")).upper() in {"BLOCKED", "SUSPECT"}]
        if unresolved:
            raise SystemExit(f"reprocessamento seletivo ainda possui {len(unresolved)} evento(s) não resolvido(s)")
        retry = pysubs2.load(str(subset_output), format="ass")
        if len(retry.events) != len(event_ids):
            raise SystemExit("saída seletiva com cardinalidade incompatível")
        merged = pysubs2.load(str(args.selective_base_output), format="ass")
        for event_id, retry_event in zip(event_ids, retry.events):
            merged.events[event_id].text = retry_event.text
        merged.save(str(args.output), format="ass")
    result["selective_retranslation"] = True
    result["selective_event_ids"] = event_ids
    result["transport_used"] = "primary-selective"
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
    parser.add_argument("--failed-event-ids", help="event IDs explícitos para retry seletivo")
    parser.add_argument("--selective-base-output", type=Path,
                        help="artefato primário a preservar durante retry seletivo")
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

    if args.failed_event_ids:
        result = _run_selective_retranslation(args, pipeline, primary, source_language)
    else:
        try:
            result = _run_pipeline(args, pipeline, primary, source_language)
        except Exception as primary_error:
            message = str(primary_error).lower()
            transport_failure = any(token in message for token in (
                "connectionerror", "timeout", "connection refused", "max retries",
                "temporarily unavailable", "name or service not known",
            ))
            if args.no_fallback or fallback is None or not transport_failure:
                raise
            result = None
    used_fallback = False
    if not args.no_fallback and (
        not isinstance(result, dict) or not args.output.is_file()
    ) and fallback is not None:
        # Only a transport failure or missing artifact may use a transport
        # fallback. Linguistic validation failures require explicit selective
        # retry so an entire episode is never silently repeated.
        used_fallback = True
        if args.output.is_file():
            primary_output = args.output.with_name(f".{args.output.name}.primary.ass")
            shutil.copy2(args.output, primary_output)
            args.output.unlink()
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
    result.setdefault("transport_used", "fallback" if used_fallback else "primary")
    print("WEB_RETRANSLATION_SUMMARY " + json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
