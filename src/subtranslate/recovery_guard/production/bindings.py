"""Trusted binding-provider interface; no socket request supplies these values."""
from __future__ import annotations

from typing import Protocol

REQUIRED_BINDINGS = frozenset({
    "operation_id", "family_id", "episode_id", "target_path", "target_prewrite_sha256",
    "snapshot_fingerprint", "execution_toolchain_fingerprint", "executor_id", "executor_sha256", "durability_sha256",
    "bundle_manifest_fingerprint", "expected_blocker", "fixed_argv_identity", "python_interpreter_identity",
    "public_key_id", "authorization_policy_version", "arming_authority",
})

class BindingProvider(Protocol):
    def __call__(self) -> dict[str, str]: ...

def validate_bindings(bindings: dict[str, str]) -> None:
    if set(bindings) != REQUIRED_BINDINGS or any(not isinstance(value, str) or not value for value in bindings.values()):
        raise ValueError("BINDING_PROVIDER_INCOMPLETE")
