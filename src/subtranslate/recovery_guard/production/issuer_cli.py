"""Uninstalled, fixed-action issuer entrypoint.

The entrypoint has no operational arguments.  Installation supplies the
protected state-store/provider factories; neither is discoverable through the
OpenCode surface.  Keeping those dependencies explicit also prevents a test
or an unconfigured checkout from touching ``/var/lib/subtranslate-guard``.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Any

from .issuer import (
    ExternalIssuer,
    FUTURE_MANIFEST_PATH,
    FUTURE_PRIVATE_KEY_PATH,
    IssuerError,
    load_manifest,
    load_private_key,
)
from .crypto import public_key_id

BUNDLE_ROOT = Path("/usr/local/lib/subtranslate-guard")


def main(
    argv: list[str] | None = None,
    *,
    geteuid: Callable[[], int] = os.geteuid,
    state_store_factory: Callable[[Path], Any] | None = None,
    provider_factory: Callable[[dict[str, Any]], Callable[[], dict[str, str]]] | None = None,
    key_loader=load_private_key,
    manifest_loader=load_manifest,
) -> int:
    args = [] if argv is None else list(argv)
    if args:
        raise IssuerError("ISSUER_ACCEPTS_NO_ARGUMENTS")
    if geteuid() != 0:
        raise IssuerError("ISSUER_REQUIRES_ROOT")
    if state_store_factory is None or provider_factory is None:
        raise IssuerError("ISSUER_INSTALLATION_CONFIGURATION_REQUIRED")
    manifest = manifest_loader(FUTURE_MANIFEST_PATH, bundle_root=BUNDLE_ROOT)
    key = key_loader(FUTURE_PRIVATE_KEY_PATH, require_root=True)
    if public_key_id(key.public_key()) != manifest.get("public_key_id"):
        raise IssuerError("ISSUER_PUBLIC_KEY_ID_MISMATCH")
    store = state_store_factory(Path("/var/lib/subtranslate-guard"))
    issuer = ExternalIssuer(store, key, provider_factory(manifest))
    issuer.issue_fixed_action()
    return 0


if __name__ == "__main__":  # pragma: no cover - installation-only entrypoint
    raise SystemExit(main(__import__("sys").argv[1:]))
