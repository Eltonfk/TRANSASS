"""Generic base-translation materializer seam for the V2.3.8 composition.

The runtime owns only the protocol and the canonical live V226 wrapper.  Any
historical episode replay implementation is injected by the execution
context and remains outside this package.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Mapping, Protocol

from production_v2_2_6_adapter import translate_subtitle_file_v2_2_6


_METRIC_KEYS = (
    "primary_requests", "physical_attempts", "model_generation_attempts",
    "successful_generations", "retries", "transport_failures",
    "schema_failures", "validation_failures", "prompt_tokens",
    "completion_tokens", "elapsed_seconds",
)


def _fsync_file(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def _fsync_dir(path: Path) -> None:
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=destination.suffix, dir=str(destination.parent))
    os.close(fd)
    temporary = Path(raw)
    try:
        shutil.copyfile(source, temporary)
        _fsync_file(temporary)
        os.replace(temporary, destination)
        _fsync_dir(destination.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _normal_metrics(summary: Mapping[str, Any] | None) -> dict[str, int | float]:
    summary = summary if isinstance(summary, Mapping) else {}
    aliases = {
        "primary_requests": ("primary_requests", "provider_requests", "requests", "calls"),
        "physical_attempts": ("physical_attempts", "physical_client_calls", "calls"),
        "model_generation_attempts": ("model_generation_attempts", "model_generation_calls", "ollama_calls"),
        "successful_generations": ("successful_generations", "resolved"),
        "retries": ("retries", "retry_calls", "actual_retry_ollama_calls"),
        "transport_failures": ("transport_failures", "application_network_failures"),
        "schema_failures": ("schema_failures",),
        "validation_failures": ("validation_failures", "failed"),
        "prompt_tokens": ("prompt_tokens",),
        "completion_tokens": ("completion_tokens",),
        "elapsed_seconds": ("elapsed_seconds", "elapsed_client_seconds", "elapsed_seconds_total"),
    }
    result: dict[str, int | float] = {}
    for key in _METRIC_KEYS:
        value = 0
        for candidate in aliases[key]:
            if isinstance(summary.get(candidate), (int, float)):
                value = summary[candidate]
                break
        result[key] = float(value) if key == "elapsed_seconds" else int(value)
    return result


class BaseTranslationMaterializerError(RuntimeError):
    """The selected base materializer is absent or violated its contract."""


class CanonicalV226LiveMaterializer:
    """Durable implementation of the canonical V2.2.6 base seam.

    The class is intentionally generic: operation identity, model authority,
    glossary/config hashes and checkpoint root are supplied by the execution
    context.  It never discovers a client or reuses a checkpoint across a
    different episode/configuration.
    """

    mode = "CANONICAL_V226_LIVE"

    def _identity(self, source: Path, context: Mapping[str, Any]) -> dict[str, Any]:
        source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
        identity = {
            "operation_id": str(context.get("operation_id") or uuid.uuid4()),
            "source_sha256": source_sha,
            "episode_id": context.get("episode_id"),
            "anime_series_id": context.get("anime_series_id"),
            "pipeline_id": str(context.get("pipeline_id") or "v2_3_8"),
            "stage_id": str(context.get("stage_id") or "FULL_TRANSLATION_V238"),
            "model_tag": context.get("model") or context.get("model_override"),
            "model_digest": context.get("model_digest"),
            "prompt_schema_hash": context.get("prompt_schema_hash"),
            "glossary_hash": context.get("glossary_hash"),
            "configuration_hash": context.get("configuration_hash"),
            "candidate_commit": context.get("candidate_commit"),
            "candidate_image": context.get("candidate_image"),
        }
        identity["identity_sha256"] = hashlib.sha256(_canonical_bytes(identity)).hexdigest()
        return identity

    def materialize(self, source: str | Path, output: str | Path, *, context: Mapping[str, Any]) -> Mapping[str, Any]:
        source_path, output_path = Path(source), Path(output)
        if not source_path.is_file():
            raise BaseTranslationMaterializerError("V238_BASE_SOURCE_MISSING")
        identity = self._identity(source_path, context)
        root = Path(context.get("checkpoint_root") or context.get("state_root") or os.environ.get("TRANSLATOR_WEB_STATE_DIR", "/tmp"))
        checkpoint = root / "v238-base-checkpoints" / identity["operation_id"]
        checkpoint.mkdir(parents=True, exist_ok=True)
        manifest_path, base_path, complete_path = checkpoint / "manifest.json", checkpoint / "base.ass", checkpoint / "COMPLETE"
        if complete_path.is_file() and manifest_path.is_file() and base_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("identity", {}).get("identity_sha256") != identity["identity_sha256"]:
                raise BaseTranslationMaterializerError("V238_CHECKPOINT_IDENTITY_MISMATCH")
            if hashlib.sha256(base_path.read_bytes()).hexdigest() != manifest.get("base_sha256"):
                raise BaseTranslationMaterializerError("V238_CHECKPOINT_BASE_HASH_MISMATCH")
            _atomic_copy(base_path, output_path)
            return {"mode": self.mode, "checkpoint": str(checkpoint), "checkpoint_identity": identity,
                    "checkpoint_reused": 1, "checkpoint_created": 0, "base_sha256": manifest["base_sha256"],
                    "event_count": manifest.get("cardinality", 0), "parse_status": "PASS", "validation_status": "PASS",
                    "lineage": manifest.get("lineage_reference"), "metrics": manifest.get("metrics", _normal_metrics({}))}
        base_kwargs = {key: context.get(key) for key in ("glossary", "memory_root", "anime_series_id", "episode_id", "job_id") if context.get(key) is not None}
        fd, raw = tempfile.mkstemp(prefix=".v238-base-", suffix=".ass", dir=str(checkpoint))
        os.close(fd)
        temporary = Path(raw)
        try:
            summary = translate_subtitle_file_v2_2_6(source_path, temporary, **base_kwargs)
            if not temporary.is_file():
                raise BaseTranslationMaterializerError("V238_V226_DID_NOT_CREATE_BASE")
            _fsync_file(temporary)
            base_sha = hashlib.sha256(temporary.read_bytes()).hexdigest()
            import pysubs2
            parsed = pysubs2.load(str(temporary), format="ass")
            cardinality = len(parsed.events)
            if cardinality <= 0:
                raise BaseTranslationMaterializerError("V238_V226_EMPTY_BASE")
            _atomic_copy(temporary, base_path)
            manifest = {"identity": identity, "base_sha256": base_sha, "cardinality": cardinality,
                        "parse_status": "PASS", "validation_status": "PASS", "created_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
                        "completed_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
                        "metrics": _normal_metrics(summary), "lineage_reference": {"source_sha256": identity["source_sha256"], "pipeline": identity["pipeline_id"], "stage": identity["stage_id"]}}
            manifest_path.write_bytes(_canonical_bytes(manifest)); _fsync_file(manifest_path)
            complete_path.write_text("COMPLETE\n", encoding="utf-8"); _fsync_file(complete_path); _fsync_dir(checkpoint)
            _atomic_copy(base_path, output_path)
            return {"mode": self.mode, "checkpoint": str(checkpoint), "checkpoint_identity": identity,
                    "checkpoint_reused": 0, "checkpoint_created": 1, "base_sha256": base_sha,
                    "event_count": cardinality, "parse_status": "PASS", "validation_status": "PASS",
                    "lineage": manifest["lineage_reference"], "metrics": manifest["metrics"]}
        except BaseTranslationMaterializerError:
            raise
        except Exception as exc:
            raise BaseTranslationMaterializerError("V238_V226_CHECKPOINT_CREATION_FAILED") from exc
        finally:
            temporary.unlink(missing_ok=True)


class BaseTranslationMaterializer(Protocol):
    mode: str

    def materialize(
        self,
        source: str | Path,
        output: str | Path,
        *,
        context: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Produce a durable base translation and return provenance metrics."""


def require_materializer(context: Mapping[str, Any]) -> BaseTranslationMaterializer:
    value = context.get("base_materializer")
    if value is None or not callable(getattr(value, "materialize", None)):
        raise BaseTranslationMaterializerError("V238_BASE_TRANSLATION_MATERIALIZER_REQUIRED")
    mode = str(getattr(value, "mode", "") or "").upper()
    if mode not in {"CANONICAL_V226_LIVE", "OFFLINE_GROUPED_CAPTURE_REPLAY", "TEST_FIXTURE"}:
        raise BaseTranslationMaterializerError("V238_UNKNOWN_BASE_MATERIALIZER_MODE")
    return value


__all__ = ["BaseTranslationMaterializer", "BaseTranslationMaterializerError", "CanonicalV226LiveMaterializer", "require_materializer"]
