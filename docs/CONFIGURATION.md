# Configuration

Runtime authorities are documented by name only; no secret values are stored.
The deploy environment is authoritative for `TRANSLATOR_PIPELINE`,
`TRANSLATOR_OLLAMA_MODEL`, fallback/reviewer model names, Ollama URL and
state/media paths. The registry validates the pipeline plan but is not model
authority.

The Llama fallback/reviewer dependency remains active and is not removed in
P2C. Safe placeholders are in `.env.example`. Persistent state and real
credentials remain outside Git.

## Source language

`TRANSLATOR_SOURCE_LANGUAGE` selects the subtitle source language translated to
Brazilian Portuguese (the target is always PT-BR). Default: `inglês`. The web UI
⚙ Motor dialog exposes the same field as the global default, and each episode row
in the queue has a per-episode language selector populated from
`GET /source-options` (which discovers every sidecar and embedded track
language, including signs/songs-only tracks). Karaokê/signs/songs preservation
is independent of this setting. The chosen language drives both source
resolution and the translation prompt.
