"""Canonical provenance contract for multi-stage pipeline plans.

This module contains only lineage policy and safe result projection.  It does
not select a pipeline, translate text, or infer ancestry from version numbers.
Every augmentation edge points at the durable record created by the stage that
actually produced the bytes consumed by the augmentation.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any


TRANSLATED_FROM = "TRANSLATED_FROM"
KARAOKE_AUGMENTED_FROM = "KARAOKE_AUGMENTED_FROM"
RETRANSLATED_FROM = "RETRANSLATED_FROM"
V226_STAGE_PIPELINE = "v2_2_6"
V230_PLAN = "v2_3_0"
V226_STAGE_ID = "FULL_TRANSLATION_V226"
V230_STAGE_ID = "KARAOKE_AUGMENTATION_V230"


class LineageContractError(RuntimeError):
    """Raised when a durable multi-stage provenance contract cannot be met."""


def _record_id(record: dict[str, Any] | None, label: str) -> int:
    value = (record or {}).get("id")
    if value is None:
        raise LineageContractError(f"missing {label} record")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise LineageContractError(f"invalid {label} record id") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_v230_stage_artifact(stage_path: str | Path, *, expected_sha256: str | None = None) -> str:
    path = Path(stage_path).resolve()
    if not path.is_file():
        raise LineageContractError("missing V226 stage artifact")
    if path.suffix.lower() not in {".ass", ".ssa", ".srt"}:
        raise LineageContractError("unsupported V226 stage artifact format")
    digest = _sha256(path)
    if expected_sha256 and digest != expected_sha256:
        raise LineageContractError("V226 stage artifact SHA changed before archive")
    return digest


def public_summary(result: dict[str, Any] | None, *, stage_handle: str | None = None,
                   stage_sha256: str | None = None) -> dict[str, Any]:
    """Return a JSON-safe summary without internal/host paths.

    The internal execution result may carry an absolute stage path for the
    persistence boundary.  That path is deliberately removed before emitting
    either normal or retranslation summaries.
    """
    value = copy.deepcopy(dict(result or {}))
    internal = value.pop("_internal", None)
    if stage_handle:
        value["stage_artifact_handle"] = Path(stage_handle).name
    if stage_sha256:
        value["stage_artifact_sha256"] = str(stage_sha256)
    value.pop("stage_artifact_path", None)
    value.pop("library_db_path", None)
    return value


def assert_v230_contract(*, source_record: dict[str, Any] | None,
                         stage_record: dict[str, Any] | None,
                         stage_artifact: str | Path,
                         final_output: str | Path,
                         stage_pipeline: str = V226_STAGE_PIPELINE) -> tuple[int, int, str]:
    source_id = _record_id(source_record, "source")
    stage_id = _record_id(stage_record, "V226 stage")
    if stage_pipeline != V226_STAGE_PIPELINE:
        raise LineageContractError("wrong durable stage pipeline id")
    stage_sha = validate_v230_stage_artifact(stage_artifact)
    final = Path(final_output)
    if not final.is_file():
        raise LineageContractError("missing V230 final output")
    if Path(stage_artifact).resolve() == final.resolve():
        raise LineageContractError("stage artifact and final output must be distinct")
    return source_id, stage_id, stage_sha


def archive_v230_records(library: Any, *, source_record: dict[str, Any] | None,
                         stage_artifact: str | Path, final_output: str | Path,
                         stage_summary: dict[str, Any] | None,
                         final_summary: dict[str, Any] | None,
                         expected_stage_sha256: str | None = None,
                         job_id: str | None = None,
                         model: str | None = None,
                         publish: bool = True,
                         retranslated_from: int | None = None) -> dict[str, Any]:
    """Commit V226 stage then V230 final with truthful provenance.

    Stage commit is intentionally durable before final commit.  If the final
    archive fails, the stage record remains immutable history and publication
    is never attempted.
    """
    source_id = _record_id(source_record, "source")
    stage_path = Path(stage_artifact).resolve()
    final_path = Path(final_output).resolve()
    stage_sha = validate_v230_stage_artifact(stage_path, expected_sha256=expected_stage_sha256)
    if not final_path.is_file():
        raise LineageContractError("missing V230 final output")
    if stage_path == final_path:
        raise LineageContractError("stage artifact and final output must be distinct")
    stage_summary = dict(stage_summary or {})
    final_summary = dict(final_summary or {})
    episode_id = source_record.get("episode_id")
    if episode_id is None:
        raise LineageContractError("source record missing episode id")
    if retranslated_from is not None and hasattr(library, "get_record"):
        old_record = library.get_record(int(retranslated_from))
        if not old_record or old_record.get("episode_id") != episode_id:
            raise LineageContractError("retranslation lineage episode mismatch")

    stage_record = library.ingest_file(
        stage_path, episode_id=int(episode_id), language="pt-BR", source_language="eng",
        source_kind="TRANSLATED", original_filename=stage_path.name, job_id=job_id,
        pipeline_version=V226_STAGE_PIPELINE, model=model,
        validation_status="VALIDATED", review_status="VALIDATED", preferred=False,
        events_total=stage_summary.get("events"), created_by="v2_3_0-durable-stage",
        require_authorized_path=False,
    )
    stage_record_id = _record_id(stage_record, "V226 stage")
    library.add_lineage(stage_record_id, source_id, TRANSLATED_FROM)

    try:
        final_record = library.ingest_file(
            final_path, episode_id=int(episode_id), language="pt-BR", source_language="eng",
            source_kind="TRANSLATED", original_filename=final_path.name, job_id=job_id,
            pipeline_version=V230_PLAN, model=model,
            validation_status="VALIDATED", review_status="VALIDATED", preferred=True,
            events_total=final_summary.get("events"), created_by="v2_3_0-final",
            require_authorized_path=False,
        )
        final_id = _record_id(final_record, "V230 final")
        library.add_lineage(final_id, source_id, TRANSLATED_FROM)
        library.add_lineage(final_id, stage_record_id, KARAOKE_AUGMENTED_FROM)
        if retranslated_from is not None and int(retranslated_from) != source_id:
            library.add_lineage(final_id, int(retranslated_from), RETRANSLATED_FROM)
        if publish:
            library.publish(final_id, target_path=final_path)
    except Exception:
        # Immutable stage history is deliberately retained.  The caller owns
        # removal of the uncommitted final sidecar.
        raise

    return {
        "stage_record": stage_record,
        "final_record": final_record,
        "stage_record_id": stage_record_id,
        "final_record_id": final_id,
        "stage_sha256": stage_sha,
        "lineages": {
            "stage": [[stage_record_id, source_id, TRANSLATED_FROM]],
            "final": [[final_id, source_id, TRANSLATED_FROM], [final_id, stage_record_id, KARAOKE_AUGMENTED_FROM]],
        },
    }


__all__ = [
    "LineageContractError", "TRANSLATED_FROM", "KARAOKE_AUGMENTED_FROM",
    "RETRANSLATED_FROM", "V226_STAGE_PIPELINE", "V230_PLAN", "V226_STAGE_ID",
    "V230_STAGE_ID", "validate_v230_stage_artifact", "assert_v230_contract",
    "archive_v230_records", "public_summary",
]
