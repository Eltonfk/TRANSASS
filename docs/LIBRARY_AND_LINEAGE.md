# Library and lineage

Lineage is provenance, not version ancestry. Every subtitle-record lineage edge
must stay inside one media episode (`LINEAGE_EPISODE_BOUNDARY_INVARIANT`).

For a successful V2.3.0 plan:

`SOURCE → V226 DURABLE STAGE → V230 FINAL`

The V2.2.6 stage has `TRANSLATED_FROM → SOURCE`. The final V2.3.0 record has
`TRANSLATED_FROM → SOURCE` and `KARAOKE_AUGMENTED_FROM → exact V226 stage`.
Retranslation adds `RETRANSLATED_FROM` as a separate provenance dimension.

Persistent Library state is external to this repository. The versioned
`resources/glossaries/` files are image resources; persistent glossary state is
operational state and must not be conflated with them.
