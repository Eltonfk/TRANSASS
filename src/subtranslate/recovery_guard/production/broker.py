"""Execution-only one-shot broker; deliberately independent from issuer/private keys."""
from __future__ import annotations

import time
from typing import Any, Callable

from .crypto import Ed25519Verifier
from .bindings import REQUIRED_BINDINGS, validate_bindings
from .schema import SchemaError, validate_signed_document
from .state import ProductionStateStore, StateError, validate_capability_id

class BrokerError(RuntimeError): pass

class ProductionBroker:
    def __init__(self, store: ProductionStateStore, verifier: Ed25519Verifier, binding_provider: Callable[[], dict[str, Any]], runner: Callable[[], bool], manifest_check: Callable[[], None] | None = None):
        self.store, self.verifier, self.binding_provider, self.runner, self.manifest_check = store, verifier, binding_provider, runner, manifest_check

    def execute_fixed_request(self, request: bytes) -> str:
        if request != b"EXECUTE_CURRENT_ARMED_RECOVERY_CAPABILITY\n": raise BrokerError("REQUEST_REJECTED")
        # A prior process may have died after claim. It is terminalized, never retried.
        for stranded in list((self.store.root / "claimed").glob("*.json")):
            # A filename is untrusted input even though it came from a
            # protected directory.  Validate it before deriving a lock or
            # opening any document, and require the signed payload to agree.
            try:
                capability_id = validate_capability_id(stranded.stem)
                document = self.store.read_document(stranded)
                payload, signature = validate_signed_document(document)
                self.verifier.verify(payload, signature)
                if payload.get("capability_id") != capability_id:
                    raise BrokerError("CLAIMED_ID_PATH_MISMATCH")
            except (StateError, SchemaError, BrokerError) as exc:
                raise BrokerError("CLAIMED_STATE_INVALID") from exc
            with self.store.lock(capability_id):
                if stranded.exists(): self.store.terminalize(stranded, payload, "CLAIMED_EXECUTION_STATE_UNKNOWN", "UNKNOWN")
        armed = self.store.armed_paths()
        if not armed: raise BrokerError("CAPABILITY_NOT_ARMED")
        if len(armed) != 1: raise BrokerError("CAPABILITY_STATE_AMBIGUOUS")
        path = armed[0]; capability_id = path.stem
        with self.store.lock(capability_id):
            try:
                document = self.store.read_document(path)
            except StateError as exc:
                raise BrokerError("CAPABILITY_UNREADABLE") from exc
            try:
                payload, signature = validate_signed_document(document); self.verifier.verify(payload, signature)
                if payload.get("capability_id") != path.stem: raise BrokerError("CAPABILITY_ID_PATH_MISMATCH")
                if payload.get("expires_at") is not None and time.time_ns() >= payload["expires_at"]: raise BrokerError("CAPABILITY_EXPIRED")
                if self.manifest_check is not None:
                    try:
                        self.manifest_check()
                    except Exception as exc:
                        raise BrokerError("MANIFEST_INVALID") from exc
                measured = self.binding_provider(); validate_bindings(measured)
                for field in REQUIRED_BINDINGS:
                    value = measured[field]
                    if payload.get(field) != value: raise BrokerError("CAPABILITY_STALE_" + field.upper())
            except (SchemaError, BrokerError) as exc:
                candidate = document.get("payload") if isinstance(document, dict) else None
                if path.exists() and isinstance(candidate, dict) and candidate.get("capability_id") == capability_id and isinstance(candidate.get("action_id"), str):
                    self.store.terminalize(path, candidate, "STALE_INVALIDATED", "STALE_INVALIDATED")
                raise BrokerError(str(exc)) from exc
            claimed = self.store.move_claim(path, capability_id)
            try: self.store.append_event("CLAIMED", payload, "CLAIMED")
            except Exception as exc:
                self.store.terminalize(claimed, payload, "CLAIMED_EXECUTION_STATE_UNKNOWN", "UNKNOWN"); raise BrokerError("JOURNAL_FAILURE_AFTER_CLAIM") from exc
            try:
                self.store.append_event("EXECUTOR_STARTED", payload, "EXECUTOR_STARTED")
                result = self.runner()
                self.store.append_event("EXECUTOR_EXITED", payload, "EXECUTOR_EXITED")
            except Exception as exc:
                self.store.terminalize(claimed, payload, "CLAIMED_EXECUTION_STATE_UNKNOWN", "UNKNOWN"); raise BrokerError("EXECUTION_STATE_UNKNOWN") from exc
            state = "SUCCEEDED" if result else "FAILED"; self.store.terminalize(claimed, payload, state, state)
            return state
