"""External issuer source. It is not imported by broker/service modules."""
from __future__ import annotations

import os
import secrets
import time
import json
from pathlib import Path
import stat
from typing import Any, Callable
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .crypto import public_key_id
from .schema import ACTION_ID, EXECUTOR_ID, CAPABILITY_SCHEMA_VERSION, canonical_payload, signed_document, validate_payload
from .state import ProductionStateStore, StateError
from .manifest import validate_final_manifest

FUTURE_PRIVATE_KEY_PATH = "/etc/subtranslate-guard/keys/issuer.ed25519"
FUTURE_MANIFEST_PATH = "/etc/subtranslate-guard/manifest.json"

class IssuerError(RuntimeError): pass

def load_private_key(path: str = FUTURE_PRIVATE_KEY_PATH, *, require_root: bool = True):
    from cryptography.hazmat.primitives import serialization
    p = Path(path)
    try: info = p.lstat()
    except OSError as exc: raise IssuerError("ISSUER_KEY_UNAVAILABLE") from exc
    if p.is_symlink() or not p.is_file() or (info.st_mode & 0o077) != 0 or (require_root and info.st_uid != 0):
        raise IssuerError("ISSUER_KEY_FILE_UNSAFE")
    try:
        key = serialization.load_pem_private_key(p.read_bytes(), password=None)
        if not isinstance(key, Ed25519PrivateKey): raise IssuerError("ISSUER_KEY_TYPE_INVALID")
        return key
    except IssuerError: raise
    except Exception as exc: raise IssuerError("ISSUER_KEY_INVALID") from exc

def load_manifest(path: str = FUTURE_MANIFEST_PATH, *, bundle_root: Path, require_root: bool = True):
    p = Path(path)
    try: info = p.lstat()
    except OSError as exc: raise IssuerError("ISSUER_MANIFEST_FILE_UNSAFE") from exc
    if p.is_symlink() or not p.is_file() or not p.is_absolute() or (info.st_mode & 0o077) != 0 or (require_root and info.st_uid != 0):
        raise IssuerError("ISSUER_MANIFEST_FILE_UNSAFE")
    try:
        manifest = json.loads(p.read_bytes())
        validate_final_manifest(manifest, bundle_root)
        return manifest
    except IssuerError: raise
    except Exception as exc: raise IssuerError("ISSUER_MANIFEST_INVALID") from exc

class ExternalIssuer:
    def __init__(self, store: ProductionStateStore, private_key: Ed25519PrivateKey, provider: Callable[[], dict[str, Any]], authority: str = "root-external-issuer"):
        self.store, self.private_key, self.provider, self.authority = store, private_key, provider, authority

    def issue_fixed_action(self) -> dict[str, Any]:
        with self.store.lock_issuer():
            if self.store.armed_paths(): raise IssuerError("DUPLICATE_LIVE_CAPABILITY")
            if self.store.has_pending_issuance(): raise IssuerError("ISSUANCE_STATE_AMBIGUOUS")
            measured = dict(self.provider())
            if measured.get("public_key_id") != public_key_id(self.private_key.public_key()):
                raise IssuerError("ISSUER_PUBLIC_KEY_ID_MISMATCH")
            if measured.get("executor_id") != EXECUTOR_ID:
                raise IssuerError("ISSUER_EXECUTOR_ID_MISMATCH")
            if not measured.get("executor_sha256") or not measured.get("durability_sha256"):
                raise IssuerError("ISSUER_EXECUTOR_BINDING_INCOMPLETE")
            payload = {**measured, "schema_version": CAPABILITY_SCHEMA_VERSION, "capability_id": secrets.token_hex(32),
                       "nonce": secrets.token_urlsafe(32), "action_id": ACTION_ID, "max_uses": 1,
                       "executor_id": EXECUTOR_ID,
                       "issued_at": time.time_ns(), "expires_at": measured.get("expires_at"),
                       "public_key_id": public_key_id(self.private_key.public_key()), "arming_authority": self.authority}
            try:
                validate_payload(payload)
            except Exception as exc:
                raise IssuerError("ISSUER_PAYLOAD_INVALID") from exc
            signature = self.private_key.sign(canonical_payload(payload))
            document = signed_document(payload, signature)
            self.store.append_event("ISSUED_PENDING", payload, "ARMED")
            self.store.write_atomic(self.store.path("armed", payload["capability_id"]), json.dumps(document, sort_keys=True, separators=(",", ":")).encode())
            self.store.append_event("ISSUED", payload, "ARMED")
            return document

def issuer_main(argv: list[str], geteuid: Callable[[], int] = os.geteuid, *, store=None, private_key=None, provider=None) -> int:
    if argv: raise IssuerError("ISSUER_ACCEPTS_NO_ARGUMENTS")
    if geteuid() != 0: raise IssuerError("ISSUER_REQUIRES_ROOT")
    if store is None or private_key is None or provider is None:
        raise IssuerError("ISSUER_INSTALLATION_CONFIGURATION_REQUIRED")
    ExternalIssuer(store, private_key, provider).issue_fixed_action()
    return 0
