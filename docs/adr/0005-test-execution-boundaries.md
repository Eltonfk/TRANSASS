# ADR 0005 — Test execution boundaries

Status: ACCEPTED

## Context

Historical probes and model-required tests must not make offline validation
ambiguous.

## Decision

Offline tests are the default; model probes are explicit and isolated; exact
superseded expectations remain preserved but are deselected from the canonical
offline command.

## Consequences

No test may silently call a model, external HTTP or production state.
