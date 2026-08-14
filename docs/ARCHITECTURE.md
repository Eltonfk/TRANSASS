# Architecture

The candidate flow is:

`Input → Classification → Linguistic Translation → Structural Reconstruction → Validation → Review/Archive/Publish`

The pipeline registry is the single plan authority. The canonical orchestrator
executes the selected plan and its explicit stages. The lineage authority
records provenance and enforces the episode boundary invariant. Unknown plans
fail closed; legacy is executable only when explicitly selected.

A pipeline plan is not a stage. V2.3.0 is a full V2.2.6 translation followed by
the V2.3.0 karaoke augmentation stage. Public summaries use safe basenames and
do not expose temporary host paths.
