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
from urllib.parse import urlsplit

from credential_store import CredentialStore
from transport_providers import (
    DEEPSEEK_MIN_DELAY_SECONDS,
    GEMINI_MIN_DELAY_SECONDS,
    GROQ_MIN_DELAY_SECONDS,
    api_key_from_env,
    migrate_gemini_model,
    nvidia_model_is_supported,
)

ALLOWED_PROVIDERS = {"ollama", "openai_compat", "groq", "gemini", "nvidia", "deepseek"}
ALLOWED_PIPELINES = {"legacy", "v2_3_0", "v2_3_8"}
DEFAULT_PIPELINE = "v2_3_8"
DEFAULT_CONFIG = {
    "primary": {"provider": "ollama", "model": "qwen3.5:9b"},
    "fallback": None,
    "keys": {},
    "credential_refs": {},
    "source_language": "inglês",
    "pipeline": DEFAULT_PIPELINE,
    "authorized_primary_models": ["qwen", "gemini", "nvidia", "meta", "openai", "llama", "deepseek"],
    "model_digest": None,
    "primary_model_digest": None,
    "fallback_model_digest": None,
    # Gemini profile: otimizações automáticas quando provider=gemini
    "gemini_profile": {
        "enabled": True,           # Aplica otimizações automaticamente
        "batch_size": 16,          # Mais unidades por chamada = menos chamadas
        "retry_budget": 32,        # Budget suficiente para temporadas completas (evita PRIMARY_RETRIES_EXHAUSTED)
        "delay_between_calls": GEMINI_MIN_DELAY_SECONDS, # Perfil conservador para limites por projeto
    },
    # Groq profile: limites conservadores para o plano gratuito, com lotes
    # menores para respeitar TPM e retries suficientes para erros transitórios.
    "groq_profile": {
        "enabled": True,
        "batch_size": 4,
        "retry_budget": 16,
        "delay_between_calls": GROQ_MIN_DELAY_SECONDS,
    },
    "deepseek_profile": {
        "enabled": True,
        "batch_size": 16,
        "retry_budget": 32,
        "delay_between_calls": max(DEEPSEEK_MIN_DELAY_SECONDS, 2.5),
    },
    "updated_at": None,
}


def _model_digest(engine: dict[str, Any] | None) -> str | None:
    """Gera identidade estável para provider, modelo e endpoint."""
    if not engine:
        return None
    provider = str(engine.get("provider") or "").strip().lower()
    model = str(engine.get("model") or "").strip()
    base_url = str(engine.get("base_url") or "").strip()
    if not provider or not model:
        return None
    material = "|".join((provider, model, base_url))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


class TransportConfigError(RuntimeError):
    pass


_PUBLIC_ENGINE_FIELDS = ("provider", "model", "base_url")
_PROFILE_FIELDS = ("enabled", "batch_size", "retry_budget", "delay_between_calls")


def _public_engine(engine: dict[str, Any] | None) -> dict[str, Any] | None:
    """Project an engine without allowing credentials or unknown fields out."""
    if not isinstance(engine, dict):
        return None
    return {key: engine[key] for key in _PUBLIC_ENGINE_FIELDS if key in engine}


def _public_profile(profile: dict[str, Any] | None, default: dict[str, Any]) -> dict[str, Any]:
    """Project only the non-secret, supported profile policy fields."""
    source = profile if isinstance(profile, dict) else {}
    return {
        key: source[key] if key in source else default[key]
        for key in _PROFILE_FIELDS
        if key in source or key in default
    }


def _environment_engine(
    provider_var: str,
    model_var: str,
    base_url_var: str,
) -> dict[str, Any] | None:
    """Read the optional first-run engine selection without reading secrets."""
    provider = str(os.environ.get(provider_var) or "").strip().lower()
    if not provider:
        return None
    if provider not in ALLOWED_PROVIDERS:
        raise TransportConfigError(f"{provider_var}: provider inválido: {provider}")
    default_models = {
        "ollama": str(os.environ.get("TRANSLATOR_OLLAMA_MODEL") or "qwen3.5:9b").strip(),
        "gemini": "gemini-3.5-flash-lite",
        "groq": "openai/gpt-oss-20b",
        "deepseek": "deepseek-chat",
        "nvidia": "meta/llama-3.1-8b-instruct",
    }
    model = str(os.environ.get(model_var) or default_models.get(provider) or "").strip()
    if not model:
        raise TransportConfigError(f"{model_var}: modelo obrigatório para {provider}")
    if provider == "gemini":
        model = migrate_gemini_model(model)
    engine: dict[str, Any] = {"provider": provider, "model": model}
    base_url = str(os.environ.get(base_url_var) or "").strip()
    if base_url:
        engine["base_url"] = base_url
    return engine


def _apply_environment_transport_defaults(merged: dict[str, Any]) -> dict[str, Any]:
    """Seed a missing config from non-secret ``TRANSPORT_*`` variables."""
    primary = _environment_engine(
        "TRANSPORT_PROVIDER", "TRANSPORT_MODEL", "TRANSPORT_BASE_URL",
    )
    if primary is not None:
        primary["base_url"] = _effective_ollama_base_url(primary)
        merged["primary"] = primary
    fallback = _environment_engine(
        "TRANSPORT_FALLBACK_PROVIDER",
        "TRANSPORT_FALLBACK_MODEL",
        "TRANSPORT_FALLBACK_BASE_URL",
    )
    if fallback is not None:
        fallback["base_url"] = _effective_ollama_base_url(fallback)
        merged["fallback"] = fallback
    pipeline = str(os.environ.get("TRANSLATOR_PIPELINE") or "").strip().lower()
    if pipeline in ALLOWED_PIPELINES:
        merged["pipeline"] = pipeline
    return merged


def _effective_ollama_base_url(engine: dict[str, Any] | None) -> str | None:
    """Resolve the Ollama endpoint for the current runtime.

    Older local configurations may persist the Docker-only hostname
    ``ollama``.  When the runtime explicitly supplies ``TRANSLATOR_OLLAMA_URL``
    (the normal host-published Ollama setup), use that endpoint instead.  An
    explicitly configured non-Docker hostname/IP is left untouched.
    """
    if not isinstance(engine, dict) or str(engine.get("provider") or "").lower() != "ollama":
        return None
    configured = str(engine.get("base_url") or "").strip()
    runtime_url = str(os.environ.get("TRANSLATOR_OLLAMA_URL") or "").strip()
    if not runtime_url:
        try:
            hostname = (urlsplit(configured).hostname or "").lower()
        except ValueError:
            hostname = ""
        if hostname == "ollama":
            # A portable Desktop/AppImage run has no Docker DNS entry.
            # Compose supplies TRANSLATOR_OLLAMA_URL explicitly, so this
            # fallback is only used for a stale container alias on the host.
            return "http://127.0.0.1:11434"
        return configured or None
    if not configured:
        return runtime_url.rsplit("/api/chat", 1)[0].rstrip("/")
    try:
        hostname = (urlsplit(configured).hostname or "").lower()
    except ValueError:
        hostname = ""
    if hostname == "ollama":
        return runtime_url.rsplit("/api/chat", 1)[0].rstrip("/")
    return configured


def _fsync_dir(path: Path) -> None:
    # Windows has no O_DIRECTORY and does not expose POSIX directory fsync.
    # The file itself is flushed before os.replace; directory durability is a
    # best-effort strengthening available only on platforms that support it.
    directory_flag = getattr(os, "O_DIRECTORY", None)
    if directory_flag is None:
        return
    fd = os.open(str(path), os.O_RDONLY | directory_flag)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def load_transport_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        # Defaults must carry the same model identity as a persisted config.
        # Without this, a first-run V2.3.8 web job enters LIVE_CAPTURED with
        # ``model_digest=None`` and fails before making its first call.
        merged = json.loads(json.dumps(DEFAULT_CONFIG))
        merged = _apply_environment_transport_defaults(merged)
        primary = dict(merged.get("primary") or {})
        if str(primary.get("provider") or "").lower() == "gemini":
            primary["model"] = migrate_gemini_model(primary.get("model") or "")
        primary["base_url"] = _effective_ollama_base_url(primary)
        merged["primary"] = primary
        merged["model_digest"] = _model_digest(merged.get("primary"))
        merged["primary_model_digest"] = merged["model_digest"]
        return merged
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TransportConfigError(f"transport config ilegível: {exc}") from exc
    if not isinstance(value, dict):
        raise TransportConfigError("transport config inválido")
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    merged.update({k: v for k, v in value.items() if k in merged})
    # Keep a persisted Docker-network alias from shadowing the endpoint that
    # the current runtime explicitly configured (for example, a host Ollama
    # published at ``host.docker.internal``).  Recompute the model identity
    # because the endpoint is part of its digest.
    primary = dict(merged.get("primary") or {})
    primary_model_before = str(primary.get("model") or "")
    if str(primary.get("provider") or "").lower() == "gemini":
        primary["model"] = migrate_gemini_model(primary.get("model") or "")
        merged["primary"] = primary
    effective_base_url = _effective_ollama_base_url(primary)
    primary_changed = primary["model"] != primary_model_before
    if primary_changed or effective_base_url != (str(primary.get("base_url") or "").strip() or None):
        primary["base_url"] = effective_base_url
        merged["primary"] = primary
        merged["model_digest"] = _model_digest(primary)
        merged["primary_model_digest"] = merged["model_digest"]
    fallback = dict(merged.get("fallback") or {})
    fallback_model_before = str(fallback.get("model") or "")
    if str(fallback.get("provider") or "").lower() == "gemini":
        fallback["model"] = migrate_gemini_model(fallback.get("model") or "")
        merged["fallback"] = fallback
    effective_fallback_url = _effective_ollama_base_url(fallback)
    fallback_changed = str(fallback.get("model") or "") != fallback_model_before
    if fallback and (fallback_changed or effective_fallback_url != (str(fallback.get("base_url") or "").strip() or None)):
        fallback["base_url"] = effective_fallback_url
        merged["fallback"] = fallback
        merged["fallback_model_digest"] = _model_digest(fallback)
    profile = dict(merged.get("gemini_profile") or {})
    # O modelo pertence exclusivamente ao motor primário/fallback. Perfis
    # antigos podiam duplicá-lo aqui; descartar o campo evita identidade
    # obsoleta e mantém o perfil limitado à política de chamadas.
    profile.pop("model", None)
    raw_profile_delay = float(profile.get("delay_between_calls", GEMINI_MIN_DELAY_SECONDS) or 0.0)
    profile_delay = max(GEMINI_MIN_DELAY_SECONDS, raw_profile_delay)
    if profile_delay != raw_profile_delay or "model" in (merged.get("gemini_profile") or {}):
        profile["delay_between_calls"] = profile_delay
        merged["gemini_profile"] = profile
    groq_profile = dict(merged.get("groq_profile") or {})
    raw_groq_batch = int(groq_profile.get("batch_size", 4) or 4)
    safe_groq_batch = min(4, max(1, raw_groq_batch))
    if safe_groq_batch != raw_groq_batch:
        groq_profile["batch_size"] = safe_groq_batch
        merged["groq_profile"] = groq_profile
    if not merged.get("model_digest"):
        merged["model_digest"] = _model_digest(merged.get("primary"))
    if not merged.get("primary_model_digest"):
        merged["primary_model_digest"] = merged.get("model_digest")
    if merged.get("fallback") and not merged.get("fallback_model_digest"):
        merged["fallback_model_digest"] = _model_digest(merged.get("fallback"))
    # Keyring-backed credentials are hydrated only in memory. Legacy file
    # credentials remain supported for headless/container deployments.
    refs = merged.get("credential_refs") or {}
    if isinstance(refs, dict):
        keys = dict(merged.get("keys") or {})
        store = CredentialStore()
        for provider in refs:
            if provider not in keys:
                secret = store.get(str(provider))
                if secret:
                    keys[str(provider)] = secret
        merged["keys"] = keys
    return merged


def public_transport_config(path: Path) -> dict[str, Any]:
    """API-safe view: providers/models plus key-presence booleans only."""
    config = load_transport_config(path)
    keys = config.get("keys") or {}
    keys_configured = {provider: bool(keys.get(provider) or api_key_from_env(provider))
                       for provider in ALLOWED_PROVIDERS if provider != "ollama"}
    return {
        "primary": _public_engine(config.get("primary")),
        "fallback": _public_engine(config.get("fallback")),
        "keys_configured": keys_configured,
        "credential_backend": CredentialStore().backend,
        "source_language": config.get("source_language") or "inglês",
        "pipeline": config.get("pipeline") or DEFAULT_PIPELINE,
        "authorized_primary_models": config.get("authorized_primary_models") or ["qwen", "openai", "deepseek"],
        "model_digest": config.get("model_digest"),
        "primary_model_digest": config.get("primary_model_digest") or config.get("model_digest"),
        "fallback_model_digest": config.get("fallback_model_digest"),
        "gemini_profile": _public_profile(config.get("gemini_profile"), DEFAULT_CONFIG["gemini_profile"]),
        "groq_profile": _public_profile(config.get("groq_profile"), DEFAULT_CONFIG["groq_profile"]),
        "deepseek_profile": _public_profile(config.get("deepseek_profile"), DEFAULT_CONFIG["deepseek_profile"]),
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
        if provider == "gemini":
            model = migrate_gemini_model(model)
        if provider == "nvidia" and not nvidia_model_is_supported(model):
            raise TransportConfigError(
                f"{label}: NVIDIA exige um modelo com namespace, por exemplo meta/llama-3.1-8b-instruct"
            )
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
    existing_keys: dict[str, str] = {}
    existing_credential_refs: dict[str, str] = {}
    if path.is_file():
        try:
            existing_config = load_transport_config(path)
            existing_keys = dict(existing_config.get("keys") or {})
            raw_credential_refs = existing_config.get("credential_refs") or {}
            existing_credential_refs = (
                dict(raw_credential_refs) if isinstance(raw_credential_refs, dict) else {}
            )
        except TransportConfigError:
            existing_keys = {}
    for provider, value in {**existing_keys, **keys}.items():
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
        authorized = ["qwen", "gemini", "nvidia", "meta", "openai", "llama", "deepseek"]
    if not isinstance(authorized, list) or not authorized or not all(isinstance(p, str) and p for p in authorized):
        raise TransportConfigError("authorized_primary_models inválido")
    model_digest = str(payload.get("model_digest") or "").strip() or _model_digest(primary_clean)
    primary_model_digest = str(payload.get("primary_model_digest") or model_digest or "").strip() or _model_digest(primary_clean)
    fallback_model_digest = str(payload.get("fallback_model_digest") or "").strip() or _model_digest(fallback_clean)
    # Gemini profile: merge com defaults quando provider=gemini
    gemini_profile = payload.get("gemini_profile") or {}
    default_gemini = DEFAULT_CONFIG.get("gemini_profile", {})
    gemini_profile_clean = {
        "enabled": bool(gemini_profile.get("enabled", default_gemini.get("enabled", True))),
        "batch_size": min(64, max(1, int(gemini_profile.get("batch_size", default_gemini.get("batch_size", 16))))),
        "retry_budget": max(0, int(gemini_profile.get("retry_budget", default_gemini.get("retry_budget", 8)))),
        "delay_between_calls": max(
            GEMINI_MIN_DELAY_SECONDS,
            float(gemini_profile.get("delay_between_calls", default_gemini.get("delay_between_calls", GEMINI_MIN_DELAY_SECONDS)) or 0.0),
        ),
    }
    groq_profile = payload.get("groq_profile") or {}
    default_groq = DEFAULT_CONFIG.get("groq_profile", {})
    groq_profile_clean = {
        "enabled": bool(groq_profile.get("enabled", default_groq.get("enabled", True))),
        "batch_size": min(4, max(1, int(groq_profile.get("batch_size", default_groq.get("batch_size", 4))))),
        "retry_budget": max(0, int(groq_profile.get("retry_budget", default_groq.get("retry_budget", 16)))),
        "delay_between_calls": max(
            GROQ_MIN_DELAY_SECONDS,
            float(groq_profile.get("delay_between_calls", default_groq.get("delay_between_calls", GROQ_MIN_DELAY_SECONDS)) or 0.0),
        ),
    }
    deepseek_profile = payload.get("deepseek_profile") or {}
    default_deepseek = DEFAULT_CONFIG.get("deepseek_profile", {})
    deepseek_profile_clean = {
        "enabled": bool(deepseek_profile.get("enabled", default_deepseek.get("enabled", True))),
        "batch_size": min(30, max(1, int(deepseek_profile.get("batch_size", default_deepseek.get("batch_size", 16))))),
        "retry_budget": min(64, max(0, int(deepseek_profile.get("retry_budget", default_deepseek.get("retry_budget", 32))))),
        "delay_between_calls": max(
            DEEPSEEK_MIN_DELAY_SECONDS,
            float(deepseek_profile.get("delay_between_calls", default_deepseek.get("delay_between_calls", 2.5)) or 0.0),
        ),
    }

    config = {
        "primary": primary_clean,
        "fallback": fallback_clean,
        "keys": keys_clean,
        "source_language": str(payload.get("source_language") or "inglês").strip() or "inglês",
        "pipeline": pipeline,
        "authorized_primary_models": list(authorized),
        "model_digest": model_digest,
        "primary_model_digest": primary_model_digest,
        "fallback_model_digest": fallback_model_digest,
        "gemini_profile": gemini_profile_clean,
        "groq_profile": groq_profile_clean,
        "deepseek_profile": deepseek_profile_clean,
        "updated_at": datetime.now(UTC).isoformat(),
    }

    # Prefer the OS vault when available.  Keep file mode as an explicit
    # fallback for Docker/headless hosts where no keyring daemon exists.
    store = CredentialStore()
    refs: dict[str, str] = {}
    if store.backend == "keyring":
        # A provider removed from the active configuration must not leave a
        # stale secret behind in Windows Credential Manager, Secret Service
        # or KWallet.  Only entries explicitly owned by this config are
        # deleted; unrelated credentials in the user's system vault remain
        # untouched.
        for provider in existing_credential_refs:
            if provider not in active_providers:
                store.delete(str(provider))
        for provider, secret in list(keys_clean.items()):
            if store.set(provider, secret):
                refs[provider] = f"keyring:{store.service}/{provider}"
                keys_clean.pop(provider, None)
        config["keys"] = keys_clean
        config["credential_refs"] = refs

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
