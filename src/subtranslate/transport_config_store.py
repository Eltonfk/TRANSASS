#!/usr/bin/env python3
"""Transport configuration store for the web app.

Persists the user's chosen translation engine (primary + optional fallback)
and API keys in a host-local JSON file under the web state dir.  Keys are
never exposed by the API: only ``keys_configured`` booleans are returned.
Writes are atomic with a timestamped backup of the previous file.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ALLOWED_PROVIDERS = {"ollama", "openai_compat", "gemini"}
DEFAULT_CONFIG = {
    "primary": {"provider": "ollama", "model": "qwen3.5:9b"},
    "fallback": None,
    "keys": {},
    "updated_at": None,
}


class TransportConfigError(RuntimeError):
    pass


def _fsync_dir(path: Path) -> None:
    fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def load_transport_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TransportConfigError(f"transport config ilegível: {exc}") from exc
    if not isinstance(value, dict):
        raise TransportConfigError("transport config inválido")
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    merged.update({k: v for k, v in value.items() if k in merged})
    return merged


def public_transport_config(path: Path) -> dict[str, Any]:
    """API-safe view: providers/models plus key-presence booleans only."""
    config = load_transport_config(path)
    keys_configured = {provider: bool(config.get("keys", {}).get(provider))
                       for provider in ALLOWED_PROVIDERS}
    return {
        "primary": config.get("primary"),
        "fallback": config.get("fallback"),
        "keys_configured": keys_configured,
        "updated_at": config.get("updated_at"),
    }


def save_transport_config(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and persist a transport config.  ``keys`` entries are stored
    only for providers present in primary/fallback; empty strings remove them."""
    primary = payload.get("primary") or {}
    fallback = payload.get("fallback")
    keys = payload.get("keys") or {}

    def _validate_engine(engine: dict[str, Any] | None, label: str) -> dict[str, Any] | None:
        if engine is None:
            return None
        provider = str(engine.get("provider", "")).lower()
        model = str(engine.get("model", "")).strip()
        if provider not in ALLOWED_PROVIDERS:
            raise TransportConfigError(f"{label}: provider inválido: {provider}")
        if not model:
            raise TransportConfigError(f"{label}: modelo obrigatório")
        if provider == "openai_compat" and not str(engine.get("base_url", "")).strip():
            raise TransportConfigError(f"{label}: openai_compat exige base_url")
        return {"provider": provider, "model": model,
                "base_url": str(engine.get("base_url", "")).strip() or None}

    primary_clean = _validate_engine(primary, "primary")
    if primary_clean is None:
        raise TransportConfigError("primary é obrigatório")
    fallback_clean = _validate_engine(fallback, "fallback")

    active_providers = {primary_clean["provider"]}
    if fallback_clean:
        active_providers.add(fallback_clean["provider"])
        if fallback_clean["provider"] == primary_clean["provider"] \
                and fallback_clean["model"] == primary_clean["model"]:
            raise TransportConfigError("fallback idêntico ao primary")

    keys_clean: dict[str, str] = {}
    for provider, value in keys.items():
        if provider not in active_providers:
            continue
        text = str(value or "").strip()
        if text:
            keys_clean[provider] = text

    config = {
        "primary": primary_clean,
        "fallback": fallback_clean,
        "keys": keys_clean,
        "updated_at": datetime.now(UTC).isoformat(),
    }

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_file():
        backup = path.with_name(f"{path.name}.bak-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}")
        backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        os.chmod(backup, 0o600)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=str(path.parent))
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        tmp_path.write_text(json.dumps(config, indent=2, sort_keys=True), encoding="utf-8")
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, path)
        _fsync_dir(path.parent)
    finally:
        tmp_path.unlink(missing_ok=True)
    return public_transport_config(path)