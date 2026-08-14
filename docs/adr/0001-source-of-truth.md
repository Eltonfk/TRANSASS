# ADR 0001 — Source of truth

Status: ACCEPTED

## Context

The review workspace and production source copies were previously mixed.

## Decision

This local Git repository is the consolidated candidate source of truth. The
deployed image remains the executable authority until a later controlled deploy.

## Consequences

Production is not changed by repository creation; history and persistent state
remain external.
