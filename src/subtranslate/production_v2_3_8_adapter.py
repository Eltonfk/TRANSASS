"""Canonical V2.3.8 full-translation adapter.

The adapter owns only the linguistic seam and delegates the V2.3.8 behavior
to :func:`execute_v238_stage`.  Replay harnesses are never imported here.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from production_v2_2_6_adapter import translate_subtitle_file_v2_2_6
from v238_full_translation_stage import PIPELINE_ID, STAGE_ID, execute_v238_stage


APPROVED_PIPELINE = PIPELINE_ID


def translate_subtitle_file_v2_3_8(*args: Any, **kwargs: Any) -> dict[str, Any]:
    source = Path(args[0] if args else kwargs["source_path"])
    output = Path(args[1] if len(args) > 1 else kwargs["output_path"])
    execution_context = dict(kwargs.pop("execution_context", {}) or {})
    provider = execution_context.get("response_provider")
    if provider is None:
        raise RuntimeError("V238_EXECUTION_CONTEXT_REQUIRED")

    # Offline/test providers supply the linguistic seam themselves and avoid
    # importing the legacy model runner.  Live mode keeps the established
    # V2.2.6 linguistic authority, then feeds its durable bytes through the
    # actual V2.3.8 stage before the canonical V2.3.0 augmentation.
    mode = getattr(provider, "mode", "")
    if mode in {"TEST_FAKE", "OFFLINE_REPLAY"}:
        result = execute_v238_stage(source, output, context=execution_context)
    else:
        with tempfile.TemporaryDirectory(prefix=".v238-v226-") as raw:
            base = Path(raw) / output.name
            base_kwargs = dict(kwargs)
            base_kwargs.pop("execution_context", None)
            translate_subtitle_file_v2_2_6(source, base, **base_kwargs)
            result = execute_v238_stage(source, output, context=execution_context, base_translation=base)
    result.update({
        "pipeline": PIPELINE_ID,
        "stage_id": STAGE_ID,
        "publishable": False,
        "durable_intermediate": True,
    })
    return result


__all__ = ["APPROVED_PIPELINE", "translate_subtitle_file_v2_3_8"]
