# ADR 0004 — Glossary resource versus state

Status: ACCEPTED

## Context

Deployed glossary files and persistent glossary state have different lifetimes.

## Decision

`resources/glossaries/` is a versioned image resource. Persistent glossary
state remains external operational state.

## Consequences

The future production layout need not bind-mount the resource, but P2C2 does
not alter the current production mount.
