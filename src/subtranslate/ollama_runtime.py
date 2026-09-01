"""Shared Ollama runtime knobs for the Subtranslate candidate.

A single place for keep-alive so the web app, legacy runner and V2.3.x
adapters agree on how long a model stays resident after the last call.

The Ollama server default (OLLAMA_KEEP_ALIVE on the host) is 5m, but the
app sends an explicit ``keep_alive`` per request, which overrides the server
default.  Hardcoding 30m in a dozen places kept the model and its prompt
cache resident for half an hour after the last call, which on a 16 GiB host
drained RAM/swap during and after translation runs.  Reading one knob keeps
every live path aligned with the server default.
"""

from __future__ import annotations

import os

DEFAULT_KEEP_ALIVE = "5m"


def ollama_keep_alive() -> str:
    """Return the configured keep-alive for Ollama model residency."""
    return os.environ.get("OLLAMA_KEEP_ALIVE", DEFAULT_KEEP_ALIVE)