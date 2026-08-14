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


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    os.close(fd)
    temporary = Path(raw)
    try:
        temporary.write_bytes(_canonical_bytes(value))
        _fsync_file(temporary)
        os.replace(temporary, path)
        _fsync_dir(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_text(path: Path, value: str) -> None:
    _atomic_json(path, {"marker": value.rstrip("\n")})


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


def build_primary_ledger(summary: Mapping[str, Any], *, context: Mapping[str, Any], source_sha256: str) -> list[dict[str, Any]]:
    """Project the factual V226 result/call records into the V2.3.8 ledger.

    V226 remains the linguistic authority.  This projection never inspects
    rendered ASS text to invent failures; it uses the runner's durable result
    statuses, flags, retry counts and call identities.
    """
    rows = summary.get("primary_ledger") if isinstance(summary.get("primary_ledger"), list) else summary.get("primary_results")
    if not isinstance(rows, list):
        rows = summary.get("results")
    calls = summary.get("primary_calls") if isinstance(summary.get("primary_calls"), list) else []
    if not calls and isinstance(summary.get("calls"), list):
        calls = summary.get("calls")
    if not isinstance(rows, list):
        if str(context.get("execution_mode") or "").upper() != "LIVE_CAPTURED":
            return []
        raise BaseTranslationMaterializerError("V238_V226_PRIMARY_LEDGER_UNAVAILABLE")
    model_tag = context.get("model") or context.get("model_override") or summary.get("model")
    model_digest = context.get("primary_model_digest") or context.get("model_digest")
    call_refs: dict[str, list[str]] = {}
    for call in calls:
        if not isinstance(call, Mapping):
            continue
        call_id = call.get("call_id") or call.get("capture_id")
        if not call_id:
            continue
        for event_id in call.get("event_ids", []) or []:
            call_refs.setdefault(str(event_id), []).append(str(call_id))
    ledger: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        event_id = row.get("event_id", row.get("id"))
        if event_id is None:
            raise BaseTranslationMaterializerError("V238_V226_LEDGER_EVENT_ID_REQUIRED")
        flags = [str(flag) for flag in (row.get("flags") or [])]
        failure = str(row.get("failure_reason") or "")
        status = str(row.get("status") or "").lower()
        if status == "resolved":
            objective_status, reason = "RESOLVED", ""
        else:
            objective_status = "SUSPECT" if row.get("retry_recommended") or "POSSIBLE_UNTRANSLATED" in " ".join(flags) else "BLOCKED"
            joined = (failure + " " + " ".join(flags)).upper()
            if "SCHEMA" in joined or "JSON" in joined:
                reason = "PRIMARY_SCHEMA_REJECTED"
            elif int(row.get("retry_count", 0) or 0) > 0 or "RETRY" in joined or "BUDGET" in joined:
                reason = "PRIMARY_RETRIES_EXHAUSTED"
            elif joined:
                reason = "PRIMARY_VALIDATION_REJECTED"
            else:
                reason = "DETERMINISTIC_SUSPECT_FLAG"
        ledger.append({
            "episode_id": context.get("episode_id"),
            "source_object_sha256": source_sha256,
            "source_object": source_sha256,
            "canonical_unit_id": str(row.get("canonical_unit_id") or f"v226-event-{event_id}"),
            "event_id": event_id,
            "primary_model_tag": row.get("final_model") or model_tag,
            "primary_model_digest": model_digest,
            "primary_attempts": int(row.get("retry_count", 0) or 0) + (1 if status else 0),
            "status": objective_status,
            "objective_reason_code": reason,
            "reason_code": reason,
            "capture_references": call_refs.get(str(event_id), []),
            "flags": flags,
            "failure_reason": failure,
        })
    return ledger


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
        mode = str(context.get("execution_mode") or "").upper()
        if mode == "LIVE_CAPTURED":
            required = ("operation_id", "episode_id", "anime_series_id", "model", "model_digest", "prompt_schema_hash", "glossary_hash", "configuration_hash", "candidate_commit", "candidate_image_id")
            missing = [key for key in required if context.get(key) in (None, "")]
            if missing:
                raise BaseTranslationMaterializerError("V238_LIVE_CHECKPOINT_IDENTITY_MISSING:" + ",".join(missing))
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
            "candidate_image_id": context.get("candidate_image_id"),
        }
        identity["identity_sha256"] = hashlib.sha256(_canonical_bytes(identity)).hexdigest()
        return identity

    def materialize(self, source: str | Path, output: str | Path, *, context: Mapping[str, Any]) -> Mapping[str, Any]:
        source_path, output_path = Path(source), Path(output)
        if not source_path.is_file():
            raise BaseTranslationMaterializerError("V238_BASE_SOURCE_MISSING")
        identity = self._identity(source_path, context)
        context_memory_db_root = context.get("memory_db_root")
        context_memory_root = context.get("memory_root")
        if context_memory_db_root is not None and context_memory_root is not None:
            try:
                if Path(context_memory_db_root).resolve() != Path(context_memory_root).resolve():
                    raise BaseTranslationMaterializerError("V238_MEMORY_ROOTS_DIVERGE")
            except OSError as exc:
                raise BaseTranslationMaterializerError("V238_MEMORY_ROOT_RESOLUTION_FAILED") from exc
        memory_db_root = context_memory_db_root if context_memory_db_root is not None else context_memory_root
        root = Path(context.get("checkpoint_root") or context.get("state_root") or os.environ.get("TRANSLATOR_WEB_STATE_DIR", "/tmp"))
        checkpoint = root / "v238-base-checkpoints" / identity["operation_id"]
        checkpoint.mkdir(parents=True, exist_ok=True)
        manifest_path, base_path, complete_path = checkpoint / "manifest.json", checkpoint / "base.ass", checkpoint / "COMPLETE"
        pending_base_path = checkpoint / "pending-base.ass"
        pending_summary_path = checkpoint / "pending-summary.json"
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
                    "lineage": manifest.get("lineage_reference"), "metrics": manifest.get("metrics", _normal_metrics({})),
                    "primary_ledger": manifest.get("primary_ledger", []), "primary_ledger_status": "PASS"}
        claim_path = checkpoint / "CLAIM"
        ledger_path = checkpoint / "primary-ledger.json"
        # A V226 return is a durable boundary even when the process faults
        # before the normal base checkpoint is promoted.  On the next run,
        # promote the pending base/summary without re-entering V226.  The
        # identity sidecar prevents cross-operation or cross-episode reuse.
        if pending_base_path.is_file() and pending_summary_path.is_file() and not complete_path.is_file():
            try:
                pending = json.loads(pending_summary_path.read_text(encoding="utf-8"))
                if pending.get("identity_sha256") != identity["identity_sha256"]:
                    raise BaseTranslationMaterializerError("V238_PENDING_V226_IDENTITY_MISMATCH")
                pending_summary = pending.get("summary")
                if not isinstance(pending_summary, Mapping):
                    raise BaseTranslationMaterializerError("V238_PENDING_V226_SUMMARY_INVALID")
                _atomic_copy(pending_base_path, base_path)
                recovered_ledger = build_primary_ledger(pending_summary, context=context, source_sha256=identity["source_sha256"])
                _atomic_json(ledger_path, recovered_ledger)
                pending_base_path.unlink(missing_ok=True)
                pending_summary_path.unlink(missing_ok=True)
            except BaseTranslationMaterializerError:
                raise
            except Exception as exc:
                raise BaseTranslationMaterializerError("V238_PENDING_V226_RECOVERY_FAILED") from exc
        # A crash after a durable base or manifest must be resumable without
        # rerunning V226.  A claim without the durable base/ledger remains an
        # in-flight ambiguity and fails closed as required.
        if base_path.is_file() and ledger_path.is_file() and not complete_path.is_file():
            try:
                recovered_ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
                parsed = __import__("pysubs2").load(str(base_path), format="ass")
                base_sha = hashlib.sha256(base_path.read_bytes()).hexdigest()
                if manifest_path.is_file():
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    if manifest.get("identity", {}).get("identity_sha256") != identity["identity_sha256"] or manifest.get("base_sha256") != base_sha:
                        raise BaseTranslationMaterializerError("V238_CHECKPOINT_IDENTITY_MISMATCH")
                    cardinality = int(manifest.get("cardinality", len(parsed.events)))
                    metrics = manifest.get("metrics", _normal_metrics({}))
                else:
                    cardinality = len(parsed.events)
                    metrics = _normal_metrics({})
                    manifest = {"identity": identity, "base_sha256": base_sha, "cardinality": cardinality,
                                "parse_status": "PASS", "validation_status": "PASS", "created_at": "RECOVERED",
                                "completed_at": None, "metrics": metrics,
                                "lineage_reference": {"source_sha256": identity["source_sha256"], "pipeline": identity["pipeline_id"], "stage": identity["stage_id"]},
                                "primary_ledger": recovered_ledger, "recovered": True}
                    _atomic_json(manifest_path, manifest)
                if cardinality <= 0:
                    raise BaseTranslationMaterializerError("V238_CHECKPOINT_RECOVERED_BASE_EMPTY")
                _atomic_text(complete_path, "COMPLETE")
                claim_path.unlink(missing_ok=True)
                _fsync_dir(checkpoint)
                _atomic_copy(base_path, output_path)
                return {"mode": self.mode, "checkpoint": str(checkpoint), "checkpoint_identity": identity,
                        "checkpoint_reused": 1, "checkpoint_created": 0, "checkpoint_resumed": 1,
                        "base_sha256": base_sha, "event_count": cardinality, "parse_status": "PASS",
                        "validation_status": "PASS", "lineage": manifest.get("lineage_reference"),
                        "metrics": metrics, "primary_ledger": recovered_ledger, "primary_ledger_status": "PASS"}
            except BaseTranslationMaterializerError:
                raise
            except Exception as exc:
                raise BaseTranslationMaterializerError("V238_CHECKPOINT_RECOVERY_FAILED") from exc
        if any(path.exists() for path in (claim_path, base_path, manifest_path, ledger_path)):
            raise BaseTranslationMaterializerError("V238_CHECKPOINT_PARTIAL_OR_CONCURRENT_CLAIM")
        try:
            claim_fd = os.open(str(claim_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.write(claim_fd, (identity["identity_sha256"] + "\n").encode("ascii"))
            os.fsync(claim_fd)
            os.close(claim_fd)
        except FileExistsError as exc:
            raise BaseTranslationMaterializerError("V238_CHECKPOINT_CONCURRENT_CLAIM") from exc
        # The frozen V2.2.5 seam names its Translation Memory root
        # ``memory_db_root``.  Keep the V2.3.8 context spelling flexible, but
        # never forward the historical ``memory_root`` keyword to V2.2.5.
        base_kwargs = {key: context.get(key) for key in ("glossary", "anime_series_id", "episode_id", "job_id") if context.get(key) is not None}
        if memory_db_root is not None:
            base_kwargs["memory_db_root"] = memory_db_root
        base_kwargs["execution_context"] = dict(context)
        fd, raw = tempfile.mkstemp(prefix=".v238-base-", suffix=".ass", dir=str(checkpoint))
        os.close(fd)
        temporary = Path(raw)
        # The frozen V2.2.5 adapter atomically creates its output and rejects
        # an already-existing destination.  mkstemp gives us a collision-free
        # name, but its placeholder must not be forwarded as an existing file.
        temporary.unlink(missing_ok=True)
        saved_budget = os.environ.get("V213_HARD_STOP_CALLS")
        configured_budget = context.get("hard_call_budget")
        if configured_budget is not None:
            os.environ["V213_HARD_STOP_CALLS"] = str(int(configured_budget))
        try:
            summary = translate_subtitle_file_v2_2_6(source_path, temporary, **base_kwargs)
            _atomic_copy(temporary, pending_base_path)
            _atomic_json(pending_summary_path, {"identity_sha256": identity["identity_sha256"], "summary": summary})
            if context.get("fault_injection") == "after_v226_return":
                raise RuntimeError("V238_FAULT_AFTER_V226_RETURN")
            primary_calls = summary.get("calls", []) if isinstance(summary, Mapping) and isinstance(summary.get("calls", []), list) else []
            primary_model = str(context.get("model") or context.get("model_override") or summary.get("model") or "")
            if any(str(call.get("model", "")).casefold().startswith("llama") for call in primary_calls if isinstance(call, Mapping)):
                raise BaseTranslationMaterializerError("V238_LEGACY_LLAMA_REACHABLE_DURING_PRIMARY_QWEN")
            if str(context.get("execution_mode") or "").upper() == "LIVE_CAPTURED" and primary_model and not primary_model.casefold().startswith("qwen"):
                raise BaseTranslationMaterializerError("V238_PRIMARY_MODEL_AUTHORITY_NOT_QWEN")
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
            primary_ledger = build_primary_ledger(summary, context=context, source_sha256=identity["source_sha256"])
            _atomic_json(ledger_path, primary_ledger)
            if context.get("fault_injection") == "after_base_ass":
                raise RuntimeError("V238_FAULT_AFTER_BASE_ASS")
            manifest = {"identity": identity, "base_sha256": base_sha, "cardinality": cardinality,
                        "parse_status": "PASS", "validation_status": "PASS", "created_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
                        "completed_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
                        "metrics": _normal_metrics(summary), "lineage_reference": {"source_sha256": identity["source_sha256"], "pipeline": identity["pipeline_id"], "stage": identity["stage_id"]},
                        "primary_ledger": primary_ledger}
            _atomic_json(manifest_path, manifest)
            if context.get("fault_injection") == "after_manifest":
                raise RuntimeError("V238_FAULT_AFTER_MANIFEST")
            if context.get("fault_injection") == "before_complete":
                raise RuntimeError("V238_FAULT_BEFORE_COMPLETE")
            _atomic_text(complete_path, "COMPLETE")
            claim_path.unlink(missing_ok=True); _fsync_dir(checkpoint)
            pending_base_path.unlink(missing_ok=True)
            pending_summary_path.unlink(missing_ok=True)
            _atomic_copy(base_path, output_path)
            return {"mode": self.mode, "checkpoint": str(checkpoint), "checkpoint_identity": identity,
                    "checkpoint_reused": 0, "checkpoint_created": 1, "base_sha256": base_sha,
                    "event_count": cardinality, "parse_status": "PASS", "validation_status": "PASS",
                    "lineage": manifest["lineage_reference"], "metrics": manifest["metrics"],
                    "primary_ledger": primary_ledger, "primary_ledger_status": "PASS"}
        except BaseTranslationMaterializerError:
            raise
        except Exception as exc:
            raise BaseTranslationMaterializerError("V238_V226_CHECKPOINT_CREATION_FAILED") from exc
        finally:
            if configured_budget is not None:
                if saved_budget is None:
                    os.environ.pop("V213_HARD_STOP_CALLS", None)
                else:
                    os.environ["V213_HARD_STOP_CALLS"] = saved_budget
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


__all__ = ["BaseTranslationMaterializer", "BaseTranslationMaterializerError", "CanonicalV226LiveMaterializer", "build_primary_ledger", "require_materializer"]
