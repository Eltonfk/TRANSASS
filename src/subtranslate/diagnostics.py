"""Sanitized diagnostics suitable for support bundles."""

from __future__ import annotations

import json
import platform
import sys
from datetime import UTC, datetime
from typing import Any


def sanitized_report(*, version: str, media: dict[str, Any], provider: dict[str, Any], onboarding_completed: bool) -> dict[str, Any]:
    primary = provider.get("primary") or {}
    fallback = provider.get("fallback") or None
    return {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "application": {"name": "Transass", "version": version},
        "runtime": {"python": platform.python_version(), "platform": platform.platform()},
        "library": {"available": bool(media.get("available")), "folders": int(media.get("folders") or 0), "label": media.get("label")},
        "onboarding": {"completed": bool(onboarding_completed)},
        "provider": {
            "primary": {"provider": primary.get("provider"), "model": primary.get("model"), "base_url": primary.get("base_url")},
            "fallback": ({"provider": fallback.get("provider"), "model": fallback.get("model"), "base_url": fallback.get("base_url")} if isinstance(fallback, dict) else None),
            "pipeline": provider.get("pipeline"),
            "credential_backend": provider.get("credential_backend"),
            "keys_configured": provider.get("keys_configured", {}),
        },
        "note": "Diagnóstico sanitizado: credenciais, caminhos absolutos e conteúdo de mídia não são incluídos.",
    }


def report_json(report: dict[str, Any]) -> bytes:
    return (json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")

