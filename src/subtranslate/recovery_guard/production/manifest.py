"""Hash-bound installation manifest helpers; no installation side effects."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

MANIFEST_SCHEMA_VERSION = "1.0.0"
EXECUTOR_RELATIVE = ".opencode/tools/subtranslate_recovery_ledger_reprepare_v2.py"
EXECUTOR_ID = "RECOVERY_LEDGER_REPREPARATION_V2"
ACTION_ID = "RECOVERY_LEDGER_REPREPARATION"
REQUIRED_COMPONENT_ROLES = frozenset({
    "broker", "capability_schema", "crypto_verifier", "state", "journal", "binding_provider",
    "runner", "protocol", "executor", "durability", "structured_tool", "systemd_service",
    "systemd_socket", "service_entrypoint", "interpreter", "issuer",
})
PUBLIC_KEY_ID_RE = re.compile(r"^ed25519-sha256:[0-9a-f]{64}$")

class ManifestError(ValueError): pass
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def build_manifest_template(*, components: dict[str, str], component_roles: dict[str, str],
                            public_key_id: str, interpreter_sha256: str,
                            source_git: str = "UNRESOLVED", source_tree: str = "UNRESOLVED") -> dict[str, Any]:
    """Build a deterministic, non-writing template for a future installation.

    ``source_git``/``source_tree`` may remain unresolved in a template, but
    ``validate_final_manifest`` deliberately rejects them until promotion.
    """
    if set(component_roles) != REQUIRED_COMPONENT_ROLES:
        raise ManifestError("MANIFEST_COMPONENT_ROLES_INVALID")
    if set(component_roles.values()) != set(components):
        raise ManifestError("MANIFEST_COMPONENT_CLOSURE_INVALID")
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "source_git": source_git, "source_tree": source_tree,
        "components": dict(components), "dependency_list": sorted(components),
        "component_roles": dict(component_roles), "executor_id": EXECUTOR_ID,
        "executor_sha256": components[EXECUTOR_RELATIVE],
        "durability_sha256": components[component_roles["durability"]],
        "interpreter": {"declared_path": "/usr/bin/python3.12", "resolved_path": "/usr/bin/python3.12", "sha256": interpreter_sha256},
        "public_key_id": public_key_id, "fixed_action_id": ACTION_ID,
        "fixed_argv_identity": hashlib.sha256(json.dumps(["/usr/bin/python3.12", "-I", "-B", EXECUTOR_RELATIVE, "--apply"], separators=(",", ":")).encode()).hexdigest(),
        "socket_policy": "/run/subtranslate-guard/guard.sock;uid-gated;fixed-frame",
        "state_root_policy": "/var/lib/subtranslate-guard;0700;guard-owned",
        "target_policy": "fixed-B4-runtime-target;preexec-revalidation",
        "backup_policy": "/var/lib/subtranslate-guard/backups;before-publish",
        "uid_gid_policy": "subtranslate-guard:subtranslate-guard",
        "unit_hashes": {"service": components[component_roles["systemd_service"]], "socket": components[component_roles["systemd_socket"]]},
        "broker_sha256": components[component_roles["broker"]],
        "issuer_sha256": components[component_roles["issuer"]],
        "structured_tool_sha256": components[component_roles["structured_tool"]],
    }
    manifest["manifest_fingerprint"] = manifest_fingerprint(manifest)
    return manifest

def canonical_manifest(manifest: dict[str, Any]) -> bytes:
    body = {key: value for key, value in manifest.items() if key != "manifest_fingerprint"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()

def manifest_fingerprint(manifest: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_manifest(manifest)).hexdigest()

def validate_manifest(manifest: dict[str, Any], bundle_root: Path) -> None:
    required = {"schema_version", "source_git", "source_tree", "components", "dependency_list", "executor_id", "executor_sha256", "durability_sha256",
                "interpreter", "public_key_id", "fixed_action_id", "fixed_argv_identity", "socket_policy", "state_root_policy",
                "target_policy", "backup_policy", "uid_gid_policy", "unit_hashes", "component_roles", "broker_sha256", "issuer_sha256", "structured_tool_sha256", "manifest_fingerprint"}
    if set(manifest) != required or manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ManifestError("MANIFEST_SCHEMA_INVALID")
    if manifest_fingerprint(manifest) != manifest["manifest_fingerprint"]: raise ManifestError("MANIFEST_FINGERPRINT_INVALID")
    if not all(isinstance(manifest.get(key), str) and manifest[key] for key in ("source_git", "source_tree", "fixed_action_id", "fixed_argv_identity", "socket_policy", "state_root_policy", "target_policy", "backup_policy", "uid_gid_policy")):
        raise ManifestError("MANIFEST_METADATA_INVALID")
    expected_argv = hashlib.sha256(json.dumps(["/usr/bin/python3.12", "-I", "-B", EXECUTOR_RELATIVE, "--apply"], separators=(",", ":")).encode()).hexdigest()
    if manifest.get("fixed_action_id") != ACTION_ID or manifest.get("executor_id") != EXECUTOR_ID or manifest.get("fixed_argv_identity") != expected_argv:
        raise ManifestError("MANIFEST_ACTION_POLICY_INVALID")
    if not PUBLIC_KEY_ID_RE.fullmatch(manifest.get("public_key_id", "")):
        raise ManifestError("MANIFEST_PUBLIC_KEY_ID_INVALID")
    components = manifest.get("components")
    if not isinstance(components, dict) or not components: raise ManifestError("MANIFEST_COMPONENTS_INVALID")
    dependencies = manifest.get("dependency_list")
    if not isinstance(dependencies, list) or len(dependencies) != len(set(dependencies)) or sorted(dependencies) != sorted(components): raise ManifestError("MANIFEST_DEPENDENCIES_MISMATCH")
    roles = manifest.get("component_roles")
    if not isinstance(roles, dict) or set(roles) != REQUIRED_COMPONENT_ROLES or any(not isinstance(value, str) or not value for value in roles.values()):
        raise ManifestError("MANIFEST_COMPONENT_ROLES_INVALID")
    if len(set(roles.values())) != len(roles) or any(value not in components for value in roles.values()):
        raise ManifestError("MANIFEST_COMPONENT_ROLE_PATH_INVALID")
    if roles.get("executor") != EXECUTOR_RELATIVE:
        raise ManifestError("MANIFEST_EXECUTOR_ROLE_INVALID")
    for key in ("executor_sha256", "durability_sha256", "broker_sha256", "issuer_sha256", "structured_tool_sha256"):
        if not SHA256_RE.fullmatch(manifest.get(key, "")):
            raise ManifestError("MANIFEST_COMPONENT_HASH_INVALID:" + key)
    unit_hashes = manifest.get("unit_hashes")
    if not isinstance(unit_hashes, dict) or set(unit_hashes) != {"service", "socket"} or any(not SHA256_RE.fullmatch(value) for value in unit_hashes.values()):
        raise ManifestError("MANIFEST_UNIT_HASHES_INVALID")
    root = Path(bundle_root)
    if root.is_symlink() or not root.is_dir():
        raise ManifestError("BUNDLE_ROOT_UNSAFE")
    for relative, expected in components.items():
        if not isinstance(relative, str) or not isinstance(expected, str): raise ManifestError("MANIFEST_COMPONENTS_INVALID")
        relative_path = Path(relative)
        if relative_path.is_absolute() or str(relative_path) != relative or ".." in relative_path.parts or "\\" in relative or not SHA256_RE.fullmatch(expected):
            raise ManifestError("MANIFEST_PATH_OR_HASH_INVALID")
        path = root / relative_path
        try: path.relative_to(root)
        except ValueError as exc: raise ManifestError("MANIFEST_PATH_TRAVERSAL") from exc
        if any(parent.is_symlink() for parent in (path.parent, *path.parents)):
            raise ManifestError("BUNDLE_PARENT_SYMLINK")
        if path.is_symlink() or not path.is_file():
            raise ManifestError("BUNDLE_COMPONENT_UNSAFE")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected: raise ManifestError("BUNDLE_COMPONENT_HASH_MISMATCH")
    interpreter = manifest["interpreter"]
    if not isinstance(interpreter, dict) or set(interpreter) != {"declared_path", "resolved_path", "sha256"}:
        raise ManifestError("INTERPRETER_IDENTITY_INVALID")
    if interpreter.get("declared_path") != "/usr/bin/python3.12" or interpreter.get("resolved_path") != "/usr/bin/python3.12" or not SHA256_RE.fullmatch(interpreter.get("sha256", "")):
        raise ManifestError("INTERPRETER_IDENTITY_INVALID")
    if manifest.get("executor_sha256") != components.get(EXECUTOR_RELATIVE): raise ManifestError("EXECUTOR_HASH_BINDING_MISSING")
    if manifest.get("durability_sha256") != components.get(roles["durability"]): raise ManifestError("DURABILITY_HASH_BINDING_MISSING")
    if roles["durability"] != "src/subtranslate/v238_per_call_durability.py": raise ManifestError("DURABILITY_ROLE_INVALID")
    if manifest.get("broker_sha256") != components.get(roles["broker"]): raise ManifestError("BROKER_HASH_BINDING_MISSING")
    if manifest.get("issuer_sha256") != components.get(roles["issuer"]): raise ManifestError("ISSUER_HASH_BINDING_MISSING")
    if manifest.get("structured_tool_sha256") != components.get(roles["structured_tool"]): raise ManifestError("STRUCTURED_TOOL_HASH_BINDING_MISSING")


def validate_final_manifest(manifest: dict[str, Any], bundle_root: Path) -> None:
    """Final installation validation rejects unresolved source/key placeholders."""
    validate_manifest(manifest, bundle_root)
    if not re.fullmatch(r"sha1:[0-9a-f]{40}", manifest["source_git"]):
        raise ManifestError("SOURCE_COMMIT_AUTHORITY_UNRESOLVED")
    if not re.fullmatch(r"sha1:[0-9a-f]{40}", manifest["source_tree"]):
        raise ManifestError("SOURCE_TREE_AUTHORITY_UNRESOLVED")
    if manifest["public_key_id"].endswith("0" * 64):
        raise ManifestError("PUBLIC_KEY_PLACEHOLDER")
    forbidden = {"TODO", "UNKNOWN", "UNRESOLVED", "current-untracked", "0000000000000000000000000000000000000000"}
    if any(any(token in str(value) for token in forbidden) for value in manifest.values()):
        raise ManifestError("MANIFEST_PLACEHOLDER_UNRESOLVED")
