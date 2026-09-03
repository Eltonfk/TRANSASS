FROM python:3.11-slim@sha256:94c50be2dc994b873b55bc123e95e6dbade08095b3dfd790f51c34de3f08cbb7

# Static ffmpeg/ffprobe pinned by SHA-256 (fail-closed if upstream changes).
# Replaces the Debian ffmpeg package (~450MB of GPU/LLVM/voice libraries the
# subtitle extraction path never uses); binaries land in /usr/local/bin.
COPY deploy/install_static_ffmpeg.py /tmp/install_static_ffmpeg.py
RUN python3 /tmp/install_static_ffmpeg.py && rm /tmp/install_static_ffmpeg.py \
    && ffmpeg -version | head -1 && ffprobe -version | head -1

WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock

COPY src/subtranslate/app.py src/subtranslate/transass_logo.png src/subtranslate/_version.py src/subtranslate/transport_providers.py src/subtranslate/transport_config_store.py src/subtranslate/human_feedback.py src/subtranslate/translation_memory.py src/subtranslate/glossary.py src/subtranslate/web_audit_retranslation.py src/subtranslate/web_retranslation_runner.py src/subtranslate/failure_ledger.py src/subtranslate/pipeline_registry.py src/subtranslate/pipeline_orchestrator.py src/subtranslate/pipeline_lineage.py src/subtranslate/queue_helpers.py src/subtranslate/production_v2_2_0_adapter.py src/subtranslate/production_v2_2_1_adapter.py src/subtranslate/production_v2_2_2_adapter.py src/subtranslate/production_v2_2_3_adapter.py src/subtranslate/production_v2_2_4_adapter.py src/subtranslate/production_v2_2_5_adapter.py src/subtranslate/production_v2_2_6_adapter.py src/subtranslate/production_v2_3_0_adapter.py src/subtranslate/anime_subtitle_translator.py src/subtranslate/anime_subtitle_library.py src/subtranslate/anime_library_hooks.py src/subtranslate/pipeline_v2_1_2.py src/subtranslate/production_v2_1_2_adapter.py src/subtranslate/pipeline_v2_1_3.py src/subtranslate/production_v2_1_3_adapter.py src/subtranslate/pipeline_v2_2_1.py src/subtranslate/pipeline_v2_2_2.py src/subtranslate/pipeline_v2_2_3.py src/subtranslate/pipeline_v2_2_4.py src/subtranslate/pipeline_v2_2_5.py src/subtranslate/pipeline_v2_2_6.py src/subtranslate/pipeline_v2_3_0.py ./
COPY scripts/anime_library_backfill.py ./scripts/anime_library_backfill.py
COPY src/subtranslate/production_v2_3_8_adapter.py src/subtranslate/v238_base_materializer.py src/subtranslate/v238_full_translation_stage.py src/subtranslate/v238_response_provider.py src/subtranslate/v238_source_payload.py src/subtranslate/v236_durable_response_capture.py src/subtranslate/v238_per_call_durability.py src/subtranslate/v238_response_normalization.py src/subtranslate/v233_styled_spans.py src/subtranslate/v235_visual_glyph_program.py src/subtranslate/v237_temporal_transform.py src/subtranslate/v238_semantic_style_ownership.py src/subtranslate/v238_rc3_atom_owner_vector.py src/subtranslate/v238_rc4_sparse_visual_eligibility.py src/subtranslate/v238_rc5_contiguous_semantic_runs.py src/subtranslate/v238_rc6_finite_selector.py src/subtranslate/v238_rc7_factorized_selector.py src/subtranslate/v238_rc7a_prompt_contract.py src/subtranslate/v238_rc7b_prompt_contract.py src/subtranslate/v238_rc7b1_prompt_contract.py src/subtranslate/v238_rc8_pairwise_boundary.py src/subtranslate/v238_rc9_independent_span.py src/subtranslate/v238_rc10_anchor_solver.py src/subtranslate/v238_llama_policy.py src/subtranslate/web_execution_context.py src/subtranslate/web_durable_provider.py src/subtranslate/ollama_runtime.py ./
COPY src/subtranslate/templates ./templates
COPY src/subtranslate/static ./static
COPY resources/glossaries ./glossaries

EXPOSE 5000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python3", "-c", "from urllib.request import urlopen; urlopen('http://127.0.0.1:5000/health', timeout=2).read()"]

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "4", "--timeout", "300", "--access-logfile", "-", "--error-logfile", "-", "app:app"]
