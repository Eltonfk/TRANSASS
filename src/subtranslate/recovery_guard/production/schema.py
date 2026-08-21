"""Strict, immutable Ed25519 capability payload schema."""
from __future__ import annotations

import base64
import hashlib
import json
import re
from typing import Any

CAPABILITY_SCHEMA_VERSION = "1.0.0"
ACTION_ID = "RECOVERY_LEDGER_REPREPARATION"
EXECUTOR_ID = "RECOVERY_LEDGER_REPREPARATION_V2"
PARENT_EXECUTOR_ID = "RECOVERY_LEDGER_REPREPARATION_V1"
PAYLOAD_FIELDS = frozenset({
    "schema_version", "capability_id", "nonce", "action_id", "operation_id", "family_id", "episode_id",
    "target_path", "target_prewrite_sha256", "snapshot_fingerprint", "execution_toolchain_fingerprint",
    "executor_id", "executor_sha256", "durability_sha256", "bundle_manifest_fingerprint", "expected_blocker", "fixed_argv_identity",
    "python_interpreter_identity", "public_key_id", "max_uses", "issued_at", "expires_at",
    "authorization_policy_version", "arming_authority",
})
REQUIRED_STRING_FIELDS = PAYLOAD_FIELDS - {"max_uses", "issued_at", "expires_at"}
CAPABILITY_ID_RE = re.compile(r"^[0-9a-f]{64}$")


class SchemaError(ValueError):
    pass


def canonical_payload(payload: dict[str, Any]) -> bytes:
    """The only signing format: UTF-8 JSON, sorted keys, compact, finite."""
    validate_payload(payload)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")


def payload_fingerprint(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_payload(payload)).hexdigest()


def encode_signature(signature: bytes) -> str:
    return base64.b64encode(signature).decode("ascii")


def decode_signature(value: Any) -> bytes:
    if not isinstance(value, str):
        raise SchemaError("SIGNATURE_ENCODING_INVALID")
    try:
        raw = base64.b64decode(value.encode("ascii"), validate=True)
    except Exception as exc:
        raise SchemaError("SIGNATURE_ENCODING_INVALID") from exc
    if len(raw) != 64:
        raise SchemaError("SIGNATURE_LENGTH_INVALID")
    return raw


def validate_payload(payload: Any) -> None:
    if not isinstance(payload, dict) or set(payload) != PAYLOAD_FIELDS:
        raise SchemaError("CAPABILITY_SCHEMA_FIELDS_INVALID")
    if payload.get("schema_version") != CAPABILITY_SCHEMA_VERSION:
        raise SchemaError("CAPABILITY_SCHEMA_VERSION_INVALID")
    if payload.get("action_id") != ACTION_ID or payload.get("executor_id") != EXECUTOR_ID:
        raise SchemaError("CAPABILITY_ACTION_ID_INVALID")
    for field in REQUIRED_STRING_FIELDS:
        if not isinstance(payload.get(field), str) or not payload[field]:
            raise SchemaError("CAPABILITY_FIELD_INVALID:" + field)
    if CAPABILITY_ID_RE.fullmatch(payload["capability_id"]) is None:
        raise SchemaError("CAPABILITY_ID_INVALID")
    if any(char.isspace() for char in payload["nonce"]):
        raise SchemaError("CAPABILITY_NONCE_INVALID")
    if payload.get("max_uses") != 1 or not isinstance(payload.get("issued_at"), int):
        raise SchemaError("CAPABILITY_ONE_SHOT_INVALID")
    if payload.get("expires_at") is not None and not isinstance(payload["expires_at"], int):
        raise SchemaError("CAPABILITY_EXPIRY_INVALID")


def signed_document(payload: dict[str, Any], signature: bytes) -> dict[str, Any]:
    canonical_payload(payload)
    return {"payload": payload, "signature": encode_signature(signature)}


def validate_signed_document(document: Any) -> tuple[dict[str, Any], bytes]:
    if not isinstance(document, dict) or set(document) != {"payload", "signature"}:
        raise SchemaError("SIGNED_DOCUMENT_FIELDS_INVALID")
    payload = document["payload"]
    validate_payload(payload)
    return payload, decode_signature(document["signature"])
