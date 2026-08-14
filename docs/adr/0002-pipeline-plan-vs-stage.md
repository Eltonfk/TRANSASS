# ADR 0002 — Pipeline plan versus stage

Status: ACCEPTED

## Context

V2.3.0 combines a full translation and a karaoke augmentation.

## Decision

Registry plans and orchestrator stages are distinct authorities. V2.3.0 is
`FULL_TRANSLATION_V226` followed by `KARAOKE_AUGMENTATION_V230`.

## Consequences

Eligibility, summaries and lineage must account for both stages.
