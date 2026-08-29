#!/usr/bin/env python3
"""Transport configuration store for the web app.

Persists the user's chosen translation engine (primary + optional fallback)
and API keys in a host-local JSON file under the web state dir.  Keys are
never exposed by the API: only ``keys_configured`` booleans are returned.
Writes are atomic with a timestamped backup of the previous file.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ALLOWED_PROVIDERS = {"ollama", "openai_compat", "gemini"}
ALLOWED_PIPELINES = {"legacy", "v2_3_0", "v2_3_8"}
DEFAULT_PIPELINE = "legacy"
DEFAULT_CONFIG = {
    "primary": {"provider": "ollama", "model": "qwen3.5:9b"},
    "fallback": None,
    "keys": {},
    "source_language": "inglês",
    "pipeline": DEFAULT_PIPELINE,
    "authorized_primary_models": ["qwen", "gemini"],
    "model_digest": None,
    # Gemini profile: otimizações automáticas quando provider=gemini
    "gemini_profile": {
        "enabled": True,           # Aplica otimizações automaticamente
        "batch_size": 16,          # Mais unidades por chamada = menos chamadas
        "retry_budget": 8,         # Menos retries = economiza quota
        "delay_between_calls": 0.5, # Delay em segundos entre chamadas (respecta 15 RPM)
        "model": "gemini-1.5-flash", # Modelo mais barato e rápido
    },
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
                       for provider in ALLOWED_PROVIDERS if provider != "ollama"}
    return {
        "primary": config.get("primary"),
        "fallback": config.get("fallback"),
        "keys_configured": keys_configured,
        "source_language": config.get("source_language") or "inglês",
        "pipeline": config.get("pipeline") or DEFAULT_PIPELINE,
        "authorized_primary_models": config.get("authorized_primary_models") or ["qwen"],
        "model_digest": config.get("model_digest"),
        "gemini_profile": config.get("gemini_profile") or DEFAULT_CONFIG.get("gemini_profile"),
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
        raw_base_url = engine.get("base_url")
        base_url = str(raw_base_url).strip() if raw_base_url else None
        return {"provider": provider, "model": model, "base_url": base_url}

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

    pipeline = str(payload.get("pipeline") or DEFAULT_PIPELINE).strip().lower()
    if pipeline not in ALLOWED_PIPELINES:
        raise TransportConfigError(f"pipeline inválido: {pipeline}")
    authorized = payload.get("authorized_primary_models")
    if authorized is None:
        authorized = ["qwen", "gemini"]
    if not isinstance(authorized, list) or not authorized or not all(isinstance(p, str) and p for p in authorized):
        raise TransportConfigError("authorized_primary_models inválido")
    model_digest = str(payload.get("model_digest") or "").strip() or None
    # Auto-gera model_digest a partir de provider+model quando não fornecido.
    # Evita V238_LIVE_CHECKPOINT_IDENTITY_MISSING:model_digest no pipeline V238.
    if not model_digest:
        fingerprint = f"{primary_clean['provider']}|{primary_clean['model']}"
        model_digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:16]

    # Gemini profile: merge com defaults quando provider=gemini
    gemini_profile = payload.get("gemini_profile") or {}
    default_gemini = DEFAULT_CONFIG.get("gemini_profile", {})
    gemini_profile_clean = {
        "enabled": bool(gemini_profile.get("enabled", default_gemini.get("enabled", True))),
        "batch_size": max(1, int(gemini_profile.get("batch_size", default_gemini.get("batch_size", 16)))),
        "retry_budget": max(0, int(gemini_profile.get("retry_budget", default_gemini.get("retry_budget", 8)))),
        "delay_between_calls": max(0.0, float(gemini_profile.get("delay_between_calls", default_gemini.get("delay_between_calls", 0.5)))),
        "model": str(gemini_profile.get("model", default_gemini.get("model", "gemini-1.5-flash"))).strip(),
    }

    config = {
        "primary": primary_clean,
        "fallback": fallback_clean,
        "keys": keys_clean,
        "source_language": str(payload.get("source_language") or "inglês").strip() or "inglês",
        "pipeline": pipeline,
        "authorized_primary_models": list(authorized),
        "model_digest": model_digest,
        "gemini_profile": gemini_profile_clean,
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