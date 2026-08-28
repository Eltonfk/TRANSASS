"""Execution context factory for the V2.3.8 web integration (C1).

Builds the complete ``execution_context`` required by
``production_v2_3_8_adapter``.  Uses ``execution_mode`` (not ``mode``) because
the runtime reads ``execution_mode`` (pipeline_orchestrator.py:121,
v238_base_materializer.py:213,140).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


class TestFixtureMaterializer:
    """M10: materializer TEST_FIXTURE para TEST_FAKE/OFFLINE_REPLAY.

    Source-preserving: copia os eventos do source como base translation e
    produz um primary_ledger com todas as unidades RESOLVED.  Sem modelo,
    sem HTTP, determinístico.  Satisfaz require_materializer
    (v238_base_materializer.py:446-453, modo TEST_FIXTURE).
    """

    mode = "TEST_FIXTURE"

    def materialize(
        self,
        source: str | Path,
        output: str | Path,
        *,
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        import pysubs2

        source_path, output_path = Path(source), Path(output)
        if not source_path.is_file():
            raise RuntimeError("V238_BASE_SOURCE_MISSING")
        parsed = pysubs2.load(str(source_path), format="ass")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        parsed.save(str(output_path), format="ass")
        events = []
        for index, _event in enumerate(parsed.events):
            events.append({
                "event_id": index,
                "canonical_unit_id": f"fixture-event-{index}",
                "status": "RESOLVED",
                "primary_model_tag": "TEST_FIXTURE",
                "primary_attempts": 1,
            })
        return {
            "mode": self.mode,
            "output_path": str(output_path),
            "primary_ledger": events,
            "metrics": {
                "primary_requests": 0,
                "physical_attempts": 0,
                "model_generation_attempts": 0,
            },
        }


def build_v238_execution_context(
    *,
    job: dict,
    transport_config: dict,
    source_language: str,
    operation_id: str,
    execution_mode: str = "LIVE_CAPTURED",
    capture_root: Path | None = None,
    authorized_primary_models: list[str] | None = None,
    glossary: dict[str, str] | None = None,
    glossary_hash: str | None = None,
    stage_completion_root: Path | None = None,
    checkpoint_root: Path | None = None,
    job_id: str | None = None,
    prompt_schema_hash: str | None = None,
    configuration_hash: str | None = None,
    candidate_commit: str | None = None,
    candidate_image_id: str | None = None,
    llama_model_tag: str | None = None,
    llama_model_digest: str | None = None,
) -> dict:
    """Constrói o execution_context completo exigido por production_v2_3_8_adapter.

    ``operation_budget`` e ``base_materializer`` NÃO são criados aqui: o
    orchestrator auto-cria o budget (pipeline_orchestrator.py:126) e o adapter
    auto-cria o materializer em LIVE_CAPTURED
    (production_v2_3_8_adapter.py:45-48).
    """
    primary = transport_config.get("primary") or {}
    model = str(primary.get("model") or "")
    ctx: dict[str, Any] = {
        "operation_id": operation_id,
        "execution_mode": execution_mode,
        "source_language": source_language,
        "model": model,
        "model_digest": transport_config.get("model_digest"),
        "episode_id": job.get("episode_id"),
        "anime_series_id": job.get("anime_series_id"),
        "job_id": job_id or job.get("id"),
        "glossary": glossary,
        "glossary_hash": glossary_hash,
        "authorized_primary_models": list(authorized_primary_models or ["qwen"]),
        "prompt_schema_hash": prompt_schema_hash,
        "configuration_hash": configuration_hash,
        "candidate_commit": candidate_commit,
        "candidate_image_id": candidate_image_id,
        "llama_model_tag": llama_model_tag,
        "llama_model_digest": llama_model_digest,
    }
    if capture_root is not None:
        ctx["capture_root"] = capture_root
    if stage_completion_root is not None:
        ctx["stage_completion_root"] = stage_completion_root
    if checkpoint_root is not None:
        ctx["checkpoint_root"] = checkpoint_root
    # M10: em TEST_FAKE/OFFLINE_REPLAY, injeta materializer TEST_FIXTURE.
    # A auto-criação do materializer só ocorre em LIVE_CAPTURED
    # (production_v2_3_8_adapter.py:45-48); sem injeção, require_materializer
    # levantaria V238_BASE_TRANSLATION_MATERIALIZER_REQUIRED.
    if execution_mode in {"TEST_FAKE", "OFFLINE_REPLAY"}:
        ctx["base_materializer"] = TestFixtureMaterializer()
    return ctx


__all__ = ["build_v238_execution_context", "TestFixtureMaterializer"]
