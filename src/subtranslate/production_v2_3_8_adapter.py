"""Canonical reusable V2.3.8 full-translation adapter.

This adapter is intentionally thin: linguistic execution remains behind the
existing canonical V2.2.6 adapter contract, while V2.3.8 adds a generic,
non-publishable stage boundary and strict deterministic validation. No fixed
episode or review fixture is reachable here.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from production_v2_2_6_adapter import translate_subtitle_file_v2_2_6
from v238_full_translation_stage import PIPELINE_ID, STAGE_ID, validate_v238_candidate


APPROVED_PIPELINE = PIPELINE_ID


def translate_subtitle_file_v2_3_8(*args: Any, **kwargs: Any) -> dict[str, Any]:
    result = dict(translate_subtitle_file_v2_2_6(*args, **kwargs))
    source = Path(args[0] if args else kwargs["source_path"])
    output = Path(args[1] if len(args) > 1 else kwargs["output_path"])
    validation = validate_v238_candidate(source, output)
    result.update({
        "pipeline": PIPELINE_ID,
        "stage_id": STAGE_ID,
        "publishable": False,
        "durable_intermediate": True,
        "v238_validation": validation,
    })
    return result


__all__ = ["APPROVED_PIPELINE", "translate_subtitle_file_v2_3_8"]
