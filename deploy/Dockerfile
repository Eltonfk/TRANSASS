FROM python:3.11-slim@sha256:94c50be2dc994b873b55bc123e95e6dbade08095b3dfd790f51c34de3f08cbb7

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock

COPY src/subtranslate/app.py src/subtranslate/human_feedback.py src/subtranslate/translation_memory.py src/subtranslate/glossary.py src/subtranslate/web_audit_retranslation.py src/subtranslate/web_retranslation_runner.py src/subtranslate/failure_ledger.py src/subtranslate/pipeline_registry.py src/subtranslate/pipeline_orchestrator.py src/subtranslate/pipeline_lineage.py src/subtranslate/queue_helpers.py src/subtranslate/production_v2_2_0_adapter.py src/subtranslate/production_v2_2_1_adapter.py src/subtranslate/production_v2_2_2_adapter.py src/subtranslate/production_v2_2_3_adapter.py src/subtranslate/production_v2_2_4_adapter.py src/subtranslate/production_v2_2_5_adapter.py src/subtranslate/production_v2_2_6_adapter.py src/subtranslate/production_v2_3_0_adapter.py src/subtranslate/anime_subtitle_translator.py src/subtranslate/anime_subtitle_library.py src/subtranslate/anime_library_hooks.py src/subtranslate/pipeline_v2_1_2.py src/subtranslate/production_v2_1_2_adapter.py src/subtranslate/pipeline_v2_1_3.py src/subtranslate/production_v2_1_3_adapter.py src/subtranslate/pipeline_v2_2_1.py src/subtranslate/pipeline_v2_2_2.py src/subtranslate/pipeline_v2_2_3.py src/subtranslate/pipeline_v2_2_4.py src/subtranslate/pipeline_v2_2_5.py src/subtranslate/pipeline_v2_2_6.py src/subtranslate/pipeline_v2_3_0.py ./
COPY scripts/anime_library_backfill.py ./scripts/anime_library_backfill.py
COPY src/subtranslate/production_v2_3_8_adapter.py src/subtranslate/v238_full_translation_stage.py src/subtranslate/v238_response_provider.py src/subtranslate/v238_source_payload.py src/subtranslate/v236_durable_response_capture.py src/subtranslate/v233_styled_spans.py src/subtranslate/v235_visual_glyph_program.py src/subtranslate/v237_temporal_transform.py src/subtranslate/v238_semantic_style_ownership.py src/subtranslate/v238_rc3_atom_owner_vector.py src/subtranslate/v238_rc10_anchor_solver.py ./
COPY resources/glossaries ./glossaries

EXPOSE 5000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python3", "-c", "from urllib.request import urlopen; urlopen('http://127.0.0.1:5000/health', timeout=2).read()"]

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "4", "--timeout", "300", "--access-logfile", "-", "--error-logfile", "-", "app:app"]
