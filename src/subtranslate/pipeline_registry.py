"""Canonical candidate registry for complete pipeline plans.

This module is the only candidate authority for plan identity, support and
stage ordering.  It deliberately contains no translation implementation.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


class UnsupportedPipelineError(ValueError):
    """Raised when a caller requests an unknown or unsupported plan."""


@dataclass(frozen=True)
class PipelinePlan:
    id: str
    display_label: str
    supported: bool
    deprecated: bool
    full_pipeline: bool
    stages: tuple[str, ...]
    supports_normal_translation: bool
    supports_retranslation: bool
    supports_verify: bool
    archive_translation: bool
    model_family: str | None = None
    notes: str = ""
    adapter_module: str | None = None
    adapter_function: str | None = None
    augmentation_module: str | None = None
    augmentation_function: str | None = None


def _plan(plan_id: str, label: str, stages: tuple[str, ...], *,
          verify: bool = False, retranslation: bool = True,
          deprecated: bool = False, model_family: str | None = None,
          notes: str = "", archive_translation: bool = False,
          adapter_module: str | None = None, adapter_function: str | None = None,
          augmentation_module: str | None = None, augmentation_function: str | None = None) -> PipelinePlan:
    return PipelinePlan(
        id=plan_id,
        display_label=label,
        supported=True,
        deprecated=deprecated,
        full_pipeline=True,
        stages=stages,
        supports_normal_translation=True,
        supports_retranslation=retranslation,
        supports_verify=verify,
        archive_translation=archive_translation,
        model_family=model_family,
        notes=notes,
        adapter_module=adapter_module,
        adapter_function=adapter_function,
        augmentation_module=augmentation_module,
        augmentation_function=augmentation_function,
    )


_PLANS = {
    "legacy": _plan(
        "legacy", "Legacy", ("LEGACY_TRANSLATION",), verify=True,
        retranslation=False,
        notes="Explicit legacy plan; it is never an implicit unknown-token fallback.",
    ),
    "v2_1_2": _plan("v2_1_2", "V2.1.2", ("FULL_TRANSLATION_V212",), adapter_module="production_v2_1_2_adapter", adapter_function="translate_subtitle_file_v2_1_2"),
    "v2_1_3": _plan("v2_1_3", "V2.1.3", ("FULL_TRANSLATION_V213",), archive_translation=True, adapter_module="production_v2_1_3_adapter", adapter_function="translate_subtitle_file_v2_1_3"),
    "v2_2_0": _plan("v2_2_0", "V2.2.0", ("FULL_TRANSLATION_V220",), archive_translation=True, adapter_module="production_v2_2_0_adapter", adapter_function="translate_subtitle_file_v2_2_0"),
    "v2_2_1": _plan("v2_2_1", "V2.2.1", ("FULL_TRANSLATION_V221",), archive_translation=True, adapter_module="production_v2_2_1_adapter", adapter_function="translate_subtitle_file_v2_2_1"),
    "v2_2_2": _plan("v2_2_2", "V2.2.2", ("FULL_TRANSLATION_V222",), archive_translation=True, adapter_module="production_v2_2_2_adapter", adapter_function="translate_subtitle_file_v2_2_2"),
    "v2_2_3": _plan("v2_2_3", "V2.2.3", ("FULL_TRANSLATION_V223",), archive_translation=True, adapter_module="production_v2_2_3_adapter", adapter_function="translate_subtitle_file_v2_2_3"),
    "v2_2_4": _plan("v2_2_4", "V2.2.4", ("FULL_TRANSLATION_V224",), archive_translation=True, adapter_module="production_v2_2_4_adapter", adapter_function="translate_subtitle_file_v2_2_4"),
    "v2_2_5": _plan("v2_2_5", "V2.2.5", ("FULL_TRANSLATION_V225",), archive_translation=True, adapter_module="production_v2_2_5_adapter", adapter_function="translate_subtitle_file_v2_2_5"),
    "v2_2_6": _plan("v2_2_6", "V2.2.6", ("FULL_TRANSLATION_V226",), archive_translation=True, adapter_module="production_v2_2_6_adapter", adapter_function="translate_subtitle_file_v2_2_6"),
    "v2_3_0": _plan(
        "v2_3_0", "V2.3.0", ("FULL_TRANSLATION_V226", "KARAOKE_AUGMENTATION_V230"),
        notes="Complete plan: V2.2.6 linguistic materialization followed by V2.3.0 karaoke augmentation.",
        adapter_module="production_v2_2_6_adapter", adapter_function="translate_subtitle_file_v2_2_6",
        augmentation_module="production_v2_3_0_adapter", augmentation_function="augment_karaoke_candidate_v2_3_0", archive_translation=True,
    ),
}

PLANS: Mapping[str, PipelinePlan] = MappingProxyType(_PLANS)


def get_pipeline_plan(plan_id: str) -> PipelinePlan:
    token = str(plan_id or "").strip().lower()
    try:
        plan = PLANS[token]
    except KeyError as exc:
        raise UnsupportedPipelineError(f"unsupported pipeline plan: {token or '<empty>'}") from exc
    if not plan.supported:
        raise UnsupportedPipelineError(f"pipeline plan is not supported: {token}")
    return plan


def resolve_pipeline(plan_id: str) -> PipelinePlan:
    """Resolve explicitly; unknown values fail closed and never become legacy."""
    return get_pipeline_plan(plan_id)


def pipeline_info(plan_id: str, *, model: str = "", service_available_for_mutation: bool = True) -> dict:
    plan = get_pipeline_plan(plan_id)
    return {
        "configured_pipeline": plan.id,
        "effective_pipeline_plan": plan.id,
        "pipeline": plan.id,
        "pipeline_label": plan.display_label,
        "supported": plan.supported,
        "full_pipeline": plan.full_pipeline,
        "stages": list(plan.stages),
        "normal_translation_supported": plan.supports_normal_translation,
        "retranslation_supported": plan.supports_retranslation,
        "verify_supported": plan.supports_verify,
        "service_available_for_mutation": bool(service_available_for_mutation),
        "archive_translation": plan.archive_translation,
        "model": model or "não configurado",
        "deprecated": plan.deprecated,
        "notes": plan.notes,
    }


__all__ = ["PLANS", "PipelinePlan", "UnsupportedPipelineError", "get_pipeline_plan", "resolve_pipeline", "pipeline_info"]
