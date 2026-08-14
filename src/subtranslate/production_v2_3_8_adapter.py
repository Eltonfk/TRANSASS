"""Canonical V2.3.8 full-translation adapter.

The adapter owns only the linguistic seam and delegates the V2.3.8 behavior
to :func:`execute_v238_stage`.  Replay harnesses are never imported here.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from v238_base_materializer import (
    BaseTranslationMaterializerError,
    CanonicalV226LiveMaterializer,
    require_materializer,
)
from v238_full_translation_stage import PIPELINE_ID, STAGE_ID, execute_v238_stage
from v238_llama_policy import enforce_v238_runtime_context
from v238_llama_policy import (
    CanonicalLlamaProvider,
    LLAMA_MODEL_DIGEST,
    LLAMA_MODEL_TAG,
    OperationCallBudget,
    run_single_fallback_phase,
)
import time


APPROVED_PIPELINE = PIPELINE_ID


def translate_subtitle_file_v2_3_8(*args: Any, **kwargs: Any) -> dict[str, Any]:
    source = Path(args[0] if args else kwargs["source_path"])
    output = Path(args[1] if len(args) > 1 else kwargs["output_path"])
    execution_context = dict(kwargs.pop("execution_context", {}) or {})
    provider = execution_context.get("response_provider")
    if provider is None:
        raise RuntimeError("V238_EXECUTION_CONTEXT_REQUIRED")
    execution_context.update(enforce_v238_runtime_context(execution_context))
    execution_context.setdefault("v238_allow_primary_ledger_failures", True)
    full_started = time.perf_counter()

    # Every mode traverses the same materializer protocol.  Live execution
    # receives the canonical V226 implementation; replay and fixture modes
    # must be injected explicitly and cannot silently cross boundaries.
    materializer = execution_context.get("base_materializer")
    if materializer is None and getattr(provider, "mode", "") == "LIVE_CAPTURED":
        materializer = CanonicalV226LiveMaterializer()
        execution_context["base_materializer"] = materializer
    selected = require_materializer(execution_context)
    base_summary = dict(selected.materialize(source, output.with_name(f".{output.name}.v226-base.ass"), context=execution_context) or {})
    base = Path(base_summary.get("output_path") or output.with_name(f".{output.name}.v226-base.ass"))
    if not base.is_file():
        raise BaseTranslationMaterializerError("V238_BASE_MATERIALIZER_DID_NOT_CREATE_OUTPUT")
    primary_ledger = base_summary.get("primary_ledger") or execution_context.get("primary_ledger")
    if primary_ledger is None:
        if str(getattr(selected, "mode", "")).upper() == "CANONICAL_V226_LIVE":
            raise BaseTranslationMaterializerError("V238_V226_PRIMARY_LEDGER_REQUIRED")
        primary_ledger = []
    budget = execution_context.get("operation_budget")
    if budget is not None and not isinstance(budget, OperationCallBudget):
        raise BaseTranslationMaterializerError("V238_OPERATION_BUDGET_INVALID")
    result = execute_v238_stage(source, output, context={**execution_context, "base_materializer_summary": base_summary, "primary_ledger": primary_ledger}, base_translation=base)
    eligible = [row for row in primary_ledger if str(row.get("status", "")).upper() in {"BLOCKED", "SUSPECT"}] if isinstance(primary_ledger, list) else []
    llama_phase = {"state": "NO_ELIGIBLE_UNITS", "eligible_count": 0, "calls": 0, "load_requested": False, "unload_requested": False, "publishable": False, "results": [], "lineage": []}
    if eligible:
        llama_provider = execution_context.get("llama_provider")
        if llama_provider is None:
            raise BaseTranslationMaterializerError("V238_CANONICAL_LLAMA_PROVIDER_REQUIRED")
        llama_tag = str(execution_context.get("llama_model_tag") or LLAMA_MODEL_TAG)
        llama_digest = str(execution_context.get("llama_model_digest") or LLAMA_MODEL_DIGEST)
        boundary = CanonicalLlamaProvider(
            llama_provider,
            model_tag=llama_tag,
            model_digest=llama_digest,
            budget=budget,
            load=execution_context.get("llama_load"),
            unload=execution_context.get("llama_unload"),
        )
        llama_phase = run_single_fallback_phase(
            primary_ledger,
            boundary,
            model_tag=llama_tag,
            model_digest=llama_digest,
            max_calls=1,
            capture_root=execution_context.get("llama_capture_root") or execution_context.get("capture_root"),
            load=boundary.load,
            unload=boundary.unload,
            budget=budget,
        )
    result["llama_phase"] = llama_phase
    result["primary_ledger"] = primary_ledger
    result["operation_budget"] = budget.snapshot() if budget is not None and hasattr(budget, "snapshot") else None
    result["full_translation_wall_seconds"] = time.perf_counter() - full_started
    result["model_calls"] = int(result.get("model_calls", 0) or 0) + int(llama_phase.get("calls", 0) or 0)
    result["network_calls"] = int(result.get("network_calls", 0) or 0)
    aggregated = result.setdefault("aggregated_metrics", {})
    aggregated.update({
        "v226_primary_requests": int((base_summary.get("metrics") or {}).get("primary_requests", (base_summary.get("metrics") or {}).get("calls", 0)) or 0),
        "v226_physical_attempts": int((base_summary.get("metrics") or {}).get("physical_attempts", (base_summary.get("metrics") or {}).get("calls", 0)) or 0),
        "v226_model_generation_attempts": int((base_summary.get("metrics") or {}).get("model_generation_attempts", (base_summary.get("metrics") or {}).get("calls", 0)) or 0),
        "v226_retries": int((base_summary.get("metrics") or {}).get("retries", 0) or 0),
        "llama_generation_calls": int(llama_phase.get("generation_calls", 0) or 0),
        "llama_control_calls": int(llama_phase.get("control_calls", 0) or 0),
        "llama_load_time_seconds": float(llama_phase.get("load_time_seconds", 0.0) or 0.0),
        "llama_unload_time_seconds": float(llama_phase.get("unload_time_seconds", 0.0) or 0.0),
        "model_calls_total": int(result.get("model_calls", 0) or 0),
    })
    stage_measurements = dict(result.get("metrics_measurements") or {})
    stage_measurements.update({
        "v226_wall_seconds": {
            "value": (base_summary.get("metrics") or {}).get("elapsed_seconds", (base_summary.get("metrics") or {}).get("elapsed_client_seconds", 0.0)),
            "measurement_status": "MEASURED" if base_summary.get("metrics") else "UNAVAILABLE",
            "measurement_source": "CanonicalV226LiveMaterializer",
        },
        "v238_wall_seconds": {
            "value": result.get("v238_wall_seconds"),
            "measurement_status": "MEASURED" if result.get("v238_wall_seconds") is not None else "UNAVAILABLE",
            "measurement_source": "v238_full_translation_stage",
        },
        "full_translation_wall_seconds": {
            "value": result.get("full_translation_wall_seconds"),
            "measurement_status": "MEASURED",
            "measurement_source": "production_v2_3_8_adapter",
        },
        "llama_generation_calls": {
            "value": int(llama_phase.get("generation_calls", 0) or 0),
            "measurement_status": "MEASURED" if eligible else "NOT_APPLICABLE",
            "measurement_source": "canonical_llama_boundary",
        },
        "llama_control_calls": {
            "value": int(llama_phase.get("control_calls", 0) or 0),
            "measurement_status": "MEASURED" if eligible else "NOT_APPLICABLE",
            "measurement_source": "canonical_llama_boundary",
        },
        "model_load_time_seconds": {
            "value": float(llama_phase.get("load_time_seconds", 0.0) or 0.0),
            "measurement_status": "MEASURED" if eligible else "NOT_APPLICABLE",
            "measurement_source": "canonical_llama_boundary",
        },
        "model_swap_time_seconds": {
            "value": 0.0,
            "measurement_status": "NOT_APPLICABLE",
            "measurement_source": "canonical_llama_boundary",
        },
        "unload_time_seconds": {
            "value": float(llama_phase.get("unload_time_seconds", 0.0) or 0.0),
            "measurement_status": "MEASURED" if eligible else "NOT_APPLICABLE",
            "measurement_source": "canonical_llama_boundary",
        },
    })
    result["metrics_measurements"] = stage_measurements
    result["metrics_before"] = result.get("metrics_before") or {"model_generation_calls": 0, "application_network_calls": 0}
    result["metrics_after"] = result.get("metrics_after") or result.get("metrics") or {}
    result["metrics_delta"] = result.get("metrics_delta") or {}
    result["base_materializer_metrics"] = base_summary.get("metrics") or {}
    result["v238_metrics"] = result.get("v238_metrics") or {}
    result["base_materializer"] = base_summary
    base.unlink(missing_ok=True)
    result.update({
        "pipeline": PIPELINE_ID,
        "stage_id": STAGE_ID,
        "publishable": False,
        "durable_intermediate": True,
        "legacy_fallback_enabled": False,
        "llama_policy": "OBJECTIVE_SINGLE_GROUP_PHASE",
        "candidate_state": llama_phase.get("state", "NO_ELIGIBLE_UNITS"),
    })
    return result


__all__ = ["APPROVED_PIPELINE", "translate_subtitle_file_v2_3_8"]
