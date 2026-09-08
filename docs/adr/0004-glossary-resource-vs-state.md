# ADR 0004 — Glossary resource versus state

Status: ACCEPTED

## Context

Deployed glossary files and persistent glossary state have different lifetimes.

## Decision

`resources/glossaries/` is a versioned image resource. Persistent glossary
state remains external operational state.

## Consequences

Deploys não precisam montar o recurso versionado. Somente o estado persistente
do glossário pertence ao volume do usuário.
