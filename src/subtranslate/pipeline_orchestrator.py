"""Canonical candidate executor for complete pipeline plans.

The orchestrator owns dispatch and stage ordering only.  Existing adapters
remain the authorities for translation, parsing, validation and retry
semantics.  It is intentionally import-lazy so offline registry tests never
load or call a model client.
"""
from __future__ import annotations

import importlib
import hashlib
import json
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from pipeline_registry import PipelinePlan, UnsupportedPipelineError, get_pipeline_plan
from v238_llama_policy import DEFAULT_QWEN_PHYSICAL_MAXIMUM


class PipelineStageValidationError(RuntimeError):
    """A stage returned an ineligible result; no final candidate is valid."""

    def __init__(self, *, plan_id: str, stage_id: str, result: dict[str, Any]):
        self.details = {
            "plan_id": plan_id,
            "stage_id": stage_id,
            "failure_count": len(result.get("failures", [])) if isinstance(result.get("failures", []), list) else 0,
            "failure_reasons": [item.get("reason") for item in result.get("failures", []) if isinstance(item, dict)],
            "unsupported_count": result.get("unsupported", 0),
            "structural_failure_count": len(result.get("structural_failures", [])) if isinstance(result.get("structural_failures", []), list) else 0,
            "song_units": result.get("song_units", 0),
            "translated_units": result.get("translated_units", 0),
            "already_translated_units": result.get("already_translated_units", 0),
            "already_translated_events": result.get("already_translated_events", 0),
        }
        super().__init__(json.dumps(self.details, sort_keys=True))


GEMINI_PHYSICAL_CALL_FLOOR = 131
GROQ_PHYSICAL_CALL_FLOOR = 192
DEEPSEEK_PHYSICAL_CALL_FLOOR = 192
QWEN_PHYSICAL_CALL_FLOOR = DEFAULT_QWEN_PHYSICAL_MAXIMUM


def gemini_operation_limits(profile: dict[str, Any] | None) -> tuple[int, int]:
    """Return ``(semantic_retries, physical_calls)`` for the Gemini profile.

    The profile's retry value describes corrective attempts, while the
    physical ledger also has to cover the initial batches and any diagnostic
    isolation calls.  Keeping a separate floor preserves the canonical safety
    ceiling without imposing the retry count as an accidental batch limit.
    """
    profile = profile if isinstance(profile, dict) else {}
    semantic = max(1, int(profile.get("retry_budget", 32) or 32))
    return semantic, max(GEMINI_PHYSICAL_CALL_FLOOR, semantic)


def groq_operation_limits(profile: dict[str, Any] | None) -> tuple[int, int]:
    """Return bounded semantic/physical budgets for the Groq profile."""
    profile = profile if isinstance(profile, dict) else {}
    semantic = max(1, int(profile.get("retry_budget", 16) or 16))
    return semantic, max(GROQ_PHYSICAL_CALL_FLOOR, semantic)


def deepseek_operation_limits(profile: dict[str, Any] | None) -> tuple[int, int]:
    """Return bounded semantic/physical budgets for the DeepSeek profile."""
    profile = profile if isinstance(profile, dict) else {}
    semantic = max(1, int(profile.get("retry_budget", 32) or 32))
    return semantic, max(DEEPSEEK_PHYSICAL_CALL_FLOOR, semantic)


def qwen_operation_limits(value: Any = None) -> tuple[int, int]:
    """Return the bounded physical budget for the local Qwen/Ollama path.

    Qwen has no hosted profile with a separate semantic retry allowance.  The
    physical ceiling therefore remains an explicit operator setting, with a
    conservative default large enough for a long episode plus bounded
    isolation/retry work.  A caller may still lower/raise it deliberately via
    ``V238_QWEN_PHYSICAL_MAXIMUM`` or the execution context.
    """
    raw = value
    if raw in (None, ""):
        raw = os.environ.get("V238_QWEN_PHYSICAL_MAXIMUM", QWEN_PHYSICAL_CALL_FLOOR)
    try:
        configured = int(raw)
    except (TypeError, ValueError):
        configured = QWEN_PHYSICAL_CALL_FLOOR
    return 0, max(1, configured)


def _context(context: dict[str, Any] | None) -> dict[str, Any]:
    return dict(context or {})


def _build_v238_operation_budget(ctx: dict[str, Any]) -> Any:
    """Build the single physical-call budget owned by the orchestrator.

    Provider profiles are execution policy, not transport credentials.  The
    web context supplies the sanitized profile fields and this boundary turns
    them into the same mutable budget object consumed by V226/V238.
    """
    existing = ctx.get("operation_budget")
    if existing is not None:
        return existing
    from v238_llama_policy import OperationCallBudget

    gemini_profile = ctx.get("gemini_profile") or {}
    if not gemini_profile:
        maybe_cfg = ctx.get("transport_config") or {}
        gemini_profile = maybe_cfg.get("gemini_profile") or {}

    maybe_cfg = ctx.get("transport_config") or {}
    groq_profile = ctx.get("groq_profile") or maybe_cfg.get("groq_profile") or {}
    deepseek_profile = ctx.get("deepseek_profile") or maybe_cfg.get("deepseek_profile") or {}
    provider_name = str(
        ctx.get("provider")
        or getattr(ctx.get("transport"), "name", "")
        or ""
    ).lower()

    if provider_name == "gemini" and bool(gemini_profile.get("enabled", False)):
        _semantic_retry_budget, physical_maximum = gemini_operation_limits(gemini_profile)
    elif provider_name == "groq" and bool(groq_profile.get("enabled", False)):
        _semantic_retry_budget, physical_maximum = groq_operation_limits(groq_profile)
    elif provider_name == "deepseek" and bool(deepseek_profile.get("enabled", False)):
        _semantic_retry_budget, physical_maximum = deepseek_operation_limits(deepseek_profile)
    else:
        _semantic_retry_budget, physical_maximum = qwen_operation_limits(
            ctx.get("qwen_physical_maximum")
        )
    return OperationCallBudget(
        qwen_physical_maximum=physical_maximum,
        llama_generation_maximum=1,
    )


def _call_full_adapter(plan_id: str, source: Path, output: Path, context: dict[str, Any]) -> Any:
    plan = get_pipeline_plan(plan_id)
    module_name, function_name = plan.adapter_module, plan.adapter_function
    if not module_name or not function_name:
        raise UnsupportedPipelineError(f"pipeline plan has no full adapter: {plan_id}")
    function: Callable[..., Any] = getattr(importlib.import_module(module_name), function_name)
    glossary = context.get("glossary")
    if plan_id in {"v2_1_2", "v2_1_3"}:
        return function(source, output, glossary=glossary)
    kwargs = dict(
        glossary=glossary,
        memory_db_root=context.get("memory_root"),
        anime_series_id=context.get("anime_series_id"),
        episode_id=context.get("episode_id"),
        job_id=context.get("job_id"),
    )
    if plan_id in {"v2_3_0", "v2_3_8"}:
        kwargs["execution_context"] = context
    return function(
        source, output, **kwargs,
    )


def _call_legacy(source: Path, output: Path, context: dict[str, Any]) -> Any:
    module = importlib.import_module("anime_subtitle_translator")
    return module.translate_subtitle_file(source, output, glossary=context.get("glossary"))


def _temporary_intermediate(output: Path, stage_id: str) -> Path:
    fd, raw = tempfile.mkstemp(prefix=f".{stage_id.lower()}-{output.stem}-", suffix=output.suffix, dir=str(output.parent))
    os.close(fd)
    intermediate = Path(raw)
    intermediate.unlink(missing_ok=True)
    return intermediate


def _result(plan_id: str, stage_id: str, adapter_result: Any, output: Path) -> dict[str, Any]:
    """Keep adapter summary fields available to existing callers."""
    result = dict(adapter_result) if isinstance(adapter_result, dict) else {"adapter_result": adapter_result}
    result.update({"plan_id": plan_id, "pipeline": plan_id, "stages": [{"id": stage_id, "result": adapter_result}], "output": result.get("output") if result.get("output") == output.name else output.name})
    return result


def _validate_v230_result(result: Any, *, plan_id: str = "v2_3_0", stage_id: str = "KARAOKE_AUGMENTATION_V230") -> dict[str, Any]:
    if not isinstance(result, dict):
        raise PipelineStageValidationError(plan_id=plan_id, stage_id=stage_id, result={})
    failures = result.get("failures")
    structural = result.get("structural_failures")
    unsupported = result.get("unsupported", 0)
    song_units = result.get("song_units", 0)
    translated_units = result.get("translated_units", 0)
    valid = (
        isinstance(failures, list) and not failures
        and isinstance(structural, list) and not structural
        and isinstance(unsupported, int) and unsupported == 0
        and isinstance(song_units, int) and isinstance(translated_units, int)
        and translated_units == song_units
    )
    if not valid:
        raise PipelineStageValidationError(plan_id=plan_id, stage_id=stage_id, result=result)
    return result


def execute_pipeline_plan(plan_id: str, source_path: str | Path, output_path: str | Path,
                          context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Execute one complete plan, returning adapter summaries.

    Unknown plans fail before any adapter import.  V2.3.0 uses a unique
    intermediate path for the V2.2.6 stage and removes it on every normal
    success/failure path unless a test explicitly requests debug retention.
    """
    plan: PipelinePlan = get_pipeline_plan(plan_id)
    source = Path(source_path)
    output = Path(output_path)
    ctx = _context(context)
    if plan.id == "v2_3_8":
        mode = str(ctx.get("execution_mode") or getattr(ctx.get("response_provider"), "mode", "TEST_FAKE")).upper()
        if mode == "LIVE_CAPTURED" and not ctx.get("operation_id"):
            raise ValueError("V238_LIVE_OPERATION_ID_REQUIRED")
        if mode != "LIVE_CAPTURED":
            ctx.setdefault("operation_id", f"offline-{uuid.uuid4()}")
        # Gemini profile: retry_budget is the semantic retry allowance.  It
        # must not also cap the physical requests needed for the initial
        # batches (a 400+ event episode already needs about 30 calls at the
        # profile's batch size).  Keep the canonical physical safety ceiling
        # separate so one malformed batch can still be isolated and retried.
        # Provider identity is explicit. Model prefixes are not an authority:
        # an OpenAI-compatible endpoint can legitimately serve an ``openai/``
        # or ``qwen/`` model without being Groq.
        ctx["operation_budget"] = _build_v238_operation_budget(ctx)
    if ctx.get("operation") == "RETRANSLATE" and not plan.supports_retranslation:
        raise UnsupportedPipelineError(f"retranslation is not supported by pipeline plan: {plan.id}")
    if output.exists():
        raise FileExistsError(f"final output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    if plan.id == "legacy":
        result = _call_legacy(source, output, ctx)
        return _result(plan.id, "LEGACY_TRANSLATION", result, output)
    if plan.id not in {"v2_3_0", "v2_3_8"}:
        result = _call_full_adapter(plan.id, source, output, ctx)
        return _result(plan.id, plan.stages[0], result, output)

    pipeline_started = time.perf_counter()
    intermediate = _temporary_intermediate(output, plan.stages[0])
    stage_results: list[dict[str, Any]] = []
    keep_debug = bool(ctx.get("debug_keep_intermediate", False))
    defer_cleanup = bool(ctx.get("defer_intermediate_cleanup", False))
    try:
        full_stage_plan = "v2_2_6" if plan.id == "v2_3_0" else "v2_3_8"
        full_stage_result = _call_full_adapter(full_stage_plan, source, intermediate, ctx)
        full_stage_result = dict(full_stage_result) if isinstance(full_stage_result, dict) else {"adapter_result": full_stage_result}
        full_stage_result.setdefault("timing", {})
        full_stage_result["timing"].setdefault("pipeline_started", pipeline_started)
        full_stage_result["timing"]["full_translation_wall_seconds"] = full_stage_result.get("full_translation_wall_seconds")
        stage_results.append({"id": plan.stages[0], "result": full_stage_result})
        v230 = getattr(importlib.import_module(plan.augmentation_module), plan.augmentation_function)
        karaoke_kwargs: dict[str, Any] = {
            "model": ctx.get("model") or ctx.get("model_override"),
            "ollama_url": ctx.get("ollama_url"),
            "original_source_path": source,
        }
        karaoke_provider = ctx.get("karaoke_translator")
        if callable(karaoke_provider):
            karaoke_kwargs["translator"] = karaoke_provider
        v230_started = time.perf_counter()
        v230_result = v230(intermediate, output, **karaoke_kwargs)
        v230_elapsed = time.perf_counter() - v230_started
        v230_result = _validate_v230_result(v230_result, plan_id=plan.id)
        stage_results.append({"id": "KARAOKE_AUGMENTATION_V230", "result": v230_result})
        if not output.is_file():
            raise RuntimeError("v2_3_0 augmentation completed without final candidate")
        # Preserve the full V2.2.6 summary at top level, then expose the
        # V2.3.0 metadata separately.  `calls` is total model calls across
        # both stages; retry_calls remains the base adapter retry count.
        result = dict(full_stage_result) if isinstance(full_stage_result, dict) else {"adapter_result": full_stage_result}
        base_calls = result.get("calls", result.get("total_ollama_calls", 0))
        base_retries = result.get("retry_calls", result.get("actual_retry_ollama_calls", 0))
        # ``provider_calls`` counts calls through the selected hosted/local
        # provider; ``ollama_calls`` is retained for direct legacy callers.
        # Both may be present in a result, so totalize them instead of making
        # the new field hide the legacy count.
        v230_calls = sum(
            value for value in (
                v230_result.get("provider_calls", 0),
                v230_result.get("ollama_calls", 0),
            )
            if isinstance(value, int)
        )
        result.update({
            "plan_id": plan.id,
            "pipeline": plan.id,
            "stages": stage_results,
            "output": output.name,
            "calls": base_calls + v230_calls if isinstance(base_calls, int) and isinstance(v230_calls, int) else base_calls,
            "retry_calls": base_retries,
            "karaoke": {
                key: v230_result.get(key)
                for key in ("song_units", "translated_units", "translated_events", "already_translated_units", "already_translated_events", "unsupported", "failures", "structural_failures", "ollama_calls", "provider_calls", "input_sha256", "output_sha256")
                if key in v230_result
            },
            "metrics_measurements": {
                "pipeline_wall_seconds": {"value": time.perf_counter() - pipeline_started, "measurement_status": "MEASURED", "measurement_source": "pipeline_orchestrator"},
                "full_translation_wall_seconds": {"value": result.get("full_translation_wall_seconds"), "measurement_status": "MEASURED" if result.get("full_translation_wall_seconds") is not None else "UNAVAILABLE", "measurement_source": "production_v2_3_8_adapter"},
                "v230_wall_seconds": {"value": v230_elapsed, "measurement_status": "MEASURED", "measurement_source": "production_v2_3_0_adapter"},
            },
            "pipeline_wall_seconds": time.perf_counter() - pipeline_started,
            "v230_wall_seconds": v230_elapsed,
            "operation_budget": ctx.get("operation_budget").snapshot() if hasattr(ctx.get("operation_budget"), "snapshot") else None,
        })
        if defer_cleanup:
            # This is an internal persistence hand-off only.  The caller must
            # archive the exact bytes before deleting the artifact.  It is
            # removed by pipeline_lineage.public_summary() before any marker
            # is emitted to the Web/control-plane consumer.
            result["_internal"] = {
                "stage_artifact_path": str(intermediate),
                "stage_pipeline": full_stage_plan,
                "stage_id": plan.stages[0],
                "stage_result": full_stage_result,
                "stage_sha256": hashlib.sha256(intermediate.read_bytes()).hexdigest(),
                "cleanup_required": True,
            }
        return result
    except Exception:
        output.unlink(missing_ok=True)
        raise
    finally:
        if not keep_debug and not defer_cleanup:
            intermediate.unlink(missing_ok=True)


__all__ = ["execute_pipeline_plan", "UnsupportedPipelineError", "PipelineStageValidationError"]
