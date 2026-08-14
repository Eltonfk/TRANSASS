# Subtranslate

Subtranslate is an anime subtitle translation service with a canonical
pipeline registry, orchestrator, validation boundaries, Library provenance and
fail-closed control-plane behavior.

This repository is the consolidated candidate source of truth created from the
proven P2B3B runtime baseline. It is **pre-controlled-deploy**: the live
production container still runs its existing image and this repository has not
been pushed to GitHub.

## Current scope

The candidate covers anime subtitle processing, including full translation
plans through V2.2.6 and the V2.3.0 two-stage plan:

`FULL_TRANSLATION_V226 → KARAOKE_AUGMENTATION_V230`

No episode media, real Library database, persistent state, production jobs,
sidecars, real secrets or model output belongs in this repository.

## Development

The source layout is intentionally flat under `src/subtranslate/` so existing
flat imports remain equivalent. Use `PYTHONPATH=src/subtranslate` for local
imports and tests. The dependency authority is the root `requirements.lock`.

```sh
PYTHONPATH=src/subtranslate pytest -c pytest.ini tests/offline
docker build --pull=false --network=none -f deploy/Dockerfile .
```

Model-required probes are isolated under `tests/model/` and are never part of
the default offline command. See `tests/README.md` and `docs/TESTING.md`.

## Documentation

Architecture, pipelines, Library/lineage, configuration, operations,
recovery, security and the roadmap are in `docs/`. The current master roadmap
is `docs/ROADMAP_MASTER_v1.1.md`; it is indexed by `docs/ROADMAP.md`.

## Authority boundaries

- Git repository: consolidated candidate source and current documentation.
- Versioned Docker image: executable deployment artifact after later gates.
- External persistent state: operational Library, TM, glossary state and jobs.
- External production configuration: secrets and effective runtime settings.
- Historical workspace/archive: archaeology, not runtime authority.
