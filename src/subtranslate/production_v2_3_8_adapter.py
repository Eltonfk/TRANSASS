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


APPROVED_PIPELINE = PIPELINE_ID


def translate_subtitle_file_v2_3_8(*args: Any, **kwargs: Any) -> dict[str, Any]:
    source = Path(args[0] if args else kwargs["source_path"])
    output = Path(args[1] if len(args) > 1 else kwargs["output_path"])
    execution_context = dict(kwargs.pop("execution_context", {}) or {})
    provider = execution_context.get("response_provider")
    if provider is None:
        raise RuntimeError("V238_EXECUTION_CONTEXT_REQUIRED")

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
    result = execute_v238_stage(source, output, context={**execution_context, "base_materializer_summary": base_summary}, base_translation=base)
    result["base_materializer"] = base_summary
    base.unlink(missing_ok=True)
    result.update({
        "pipeline": PIPELINE_ID,
        "stage_id": STAGE_ID,
        "publishable": False,
        "durable_intermediate": True,
    })
    return result


__all__ = ["APPROVED_PIPELINE", "translate_subtitle_file_v2_3_8"]
