"""Public-key verification only.  This module never loads a private key."""
from __future__ import annotations

import hashlib
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature

from .schema import SchemaError, canonical_payload


def public_key_id(public_key: Ed25519PublicKey) -> str:
    raw = public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return "ed25519-sha256:" + hashlib.sha256(raw).hexdigest()


class Ed25519Verifier:
    def __init__(self, public_key: Ed25519PublicKey, expected_key_id: str):
        if public_key_id(public_key) != expected_key_id:
            raise SchemaError("PUBLIC_KEY_ID_MISMATCH")
        self._public_key = public_key
        self.key_id = expected_key_id

    def verify(self, payload: dict, signature: bytes) -> None:
        if payload.get("public_key_id") != self.key_id:
            raise SchemaError("CAPABILITY_PUBLIC_KEY_ID_MISMATCH")
        try:
            self._public_key.verify(signature, canonical_payload(payload))
        except InvalidSignature as exc:
            raise SchemaError("CAPABILITY_SIGNATURE_INVALID") from exc
