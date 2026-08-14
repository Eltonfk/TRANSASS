# ADR 0003 — Lineage is provenance

Status: ACCEPTED

## Context

Historical version numbers do not by themselves prove an input relationship.

## Decision

Lineage records the artifact actually used as input and never crosses an
episode boundary. V2.3.0 final records point to the exact durable V2.2.6 stage.

## Consequences

No synthetic V225 ancestry is created for a new full V2.3.0 execution.
