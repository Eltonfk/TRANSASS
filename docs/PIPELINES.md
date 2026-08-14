# Pipelines

Supported plans and dispatch are defined by `pipeline_registry.py` and
`pipeline_orchestrator.py`. Versioned pipeline and adapter modules remain
available for explicit runtime, rollback and historical replay until a later
equivalence decision authorizes archival.

- `legacy` is explicit only.
- Unknown pipeline IDs fail closed.
- `v2_2_4`, `v2_2_5` and `v2_2_6` are single full-translation plans.
- `v2_3_0` is exactly:
  `FULL_TRANSLATION_V226 → KARAOKE_AUGMENTATION_V230`.

V2.3.0 is not an augmentation-only translator: the V2.2.6 full stage produces
the durable checkpoint used by the karaoke stage and final lineage.
