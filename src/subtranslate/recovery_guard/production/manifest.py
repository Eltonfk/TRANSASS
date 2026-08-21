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
# Roles introduced by source-closure R2.  They are recognized explicitly and
# never accepted as arbitrary strings.  Older synthetic manifests remain
# readable for fixture/regression tests; a future final manifest must include
# every role required by its installed entrypoints.
SOURCE_CLOSURE_COMPONENT_ROLES = frozenset({
    "probe_engine", "probe_entrypoint", "service_launcher", "issuer_launcher",
    "issuer_cli", "sudoers_policy", "mediation_mount",
    "system_external_dependency_set", "guard_core", "guard_package",
})
KNOWN_COMPONENT_ROLES = REQUIRED_COMPONENT_ROLES | SOURCE_CLOSURE_COMPONENT_ROLES
OPTIONAL_MANIFEST_FIELDS = frozenset({
    "security_component_roles", "non_security_component_roles",
    "structured_tool_trust_model", "system_external_dependency_set",
    "socket_peer_uid", "authority_root", "runtime_parent",
    "probe_toolchain_components", "probe_executor_id",
    "mediation_policy",
    "release_selector_policy",
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
    if not REQUIRED_COMPONENT_ROLES.issubset(component_roles) or not set(component_roles).issubset(KNOWN_COMPONENT_ROLES):
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
        "structured_tool_trust_model": "UNTRUSTED_FIXED_CLIENT",
        "release_selector_policy": {
            "path": "/usr/local/lib/subtranslate-guard/current",
            "must_be_root_owned_symlink": True,
            "target_prefix": "/usr/local/lib/subtranslate-guard/releases/",
        },
    }
    if "system_external_dependency_set" in component_roles:
        manifest["system_external_dependency_set"] = component_roles["system_external_dependency_set"]
    if "security_component_roles" not in manifest:
        manifest["security_component_roles"] = sorted(set(component_roles) - {"structured_tool"})
    if "non_security_component_roles" not in manifest:
        manifest["non_security_component_roles"] = ["structured_tool"]
    if "mediation_mount" in component_roles:
        manifest["mediation_policy"] = {
            "canonical_b4": "/home/palhacinho/codex-projects/anime-subtitle-translator-review/runtime-evidence/V238_E07_R6C_B4_RECOVERY",
            "backing_b4": "/var/lib/subtranslate-guard/recovery-targets/V238_E07_R6C_B4_RECOVERY",
            "host_view": "bind,ro",
            "service_view": "BindPaths;ReadWritePaths",
        }
    manifest["manifest_fingerprint"] = manifest_fingerprint(manifest)
    return manifest

def canonical_manifest(manifest: dict[str, Any]) -> bytes:
    body = {key: value for key, value in manifest.items() if key != "manifest_fingerprint"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()

def manifest_fingerprint(manifest: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_manifest(manifest)).hexdigest()


def validate_current_release_selector(selector: Path, expected_release: Path) -> Path:
    """Validate the root-controlled ``current`` release selector read-only."""
    selector = Path(selector)
    expected_release = Path(expected_release).resolve(strict=True)
    try:
        info = selector.lstat()
    except OSError as exc:
        raise ManifestError("RELEASE_SELECTOR_UNAVAILABLE") from exc
    if not selector.is_symlink() or info.st_uid != 0 or (info.st_mode & 0o022):
        raise ManifestError("RELEASE_SELECTOR_UNSAFE")
    resolved = selector.resolve(strict=True)
    releases = selector.parent / "releases"
    try:
        resolved.relative_to(releases.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise ManifestError("RELEASE_SELECTOR_ESCAPE") from exc
    if resolved != expected_release:
        raise ManifestError("RELEASE_SELECTOR_TARGET_MISMATCH")
    return resolved

def validate_manifest(manifest: dict[str, Any], bundle_root: Path) -> None:
    required = {"schema_version", "source_git", "source_tree", "components", "dependency_list", "executor_id", "executor_sha256", "durability_sha256",
                "interpreter", "public_key_id", "fixed_action_id", "fixed_argv_identity", "socket_policy", "state_root_policy",
                "target_policy", "backup_policy", "uid_gid_policy", "unit_hashes", "component_roles", "broker_sha256", "issuer_sha256", "structured_tool_sha256", "manifest_fingerprint"}
    if not required.issubset(manifest) or set(manifest) - required - OPTIONAL_MANIFEST_FIELDS or manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
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
    if not isinstance(roles, dict) or not REQUIRED_COMPONENT_ROLES.issubset(roles) or not set(roles).issubset(KNOWN_COMPONENT_ROLES) or any(not isinstance(value, str) or not value for value in roles.values()):
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
    role_by_path = {value: role for role, value in roles.items()}
    security_roles_for_hash = set(manifest.get("security_component_roles", roles))
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
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            role = role_by_path.get(relative)
            if role not in security_roles_for_hash and role == "structured_tool" and manifest.get("structured_tool_trust_model") == "UNTRUSTED_FIXED_CLIENT":
                continue
            raise ManifestError("BUNDLE_COMPONENT_HASH_MISMATCH")
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
    trust = manifest.get("structured_tool_trust_model", "UNTRUSTED_FIXED_CLIENT")
    if trust != "UNTRUSTED_FIXED_CLIENT":
        raise ManifestError("STRUCTURED_TOOL_TRUST_MODEL_INVALID")
    security_roles = manifest.get("security_component_roles")
    if security_roles is not None:
        if not isinstance(security_roles, list) or len(security_roles) != len(set(security_roles)) or any(role not in roles or role == "structured_tool" for role in security_roles):
            raise ManifestError("SECURITY_COMPONENT_ROLES_INVALID")
    non_security_roles = manifest.get("non_security_component_roles")
    if non_security_roles is not None:
        if not isinstance(non_security_roles, list) or len(non_security_roles) != len(set(non_security_roles)) or any(role not in roles for role in non_security_roles):
            raise ManifestError("NON_SECURITY_COMPONENT_ROLES_INVALID")
    external_role = roles.get("system_external_dependency_set")
    external_path = manifest.get("system_external_dependency_set")
    if external_role is not None:
        if external_path != external_role:
            raise ManifestError("SYSTEM_EXTERNAL_DEPENDENCY_ROLE_INVALID")
        external_file = root / external_role
        try:
            external = json.loads(external_file.read_bytes())
        except Exception as exc:
            raise ManifestError("SYSTEM_EXTERNAL_DEPENDENCY_SET_INVALID") from exc
        if not isinstance(external, dict) or external.get("schema_version") != "1.0.0" or not isinstance(external.get("dependencies"), list):
            raise ManifestError("SYSTEM_EXTERNAL_DEPENDENCY_SET_INVALID")
        for dependency in external["dependencies"]:
            if not isinstance(dependency, dict) or not isinstance(dependency.get("package_name"), str) or not isinstance(dependency.get("package_version"), str):
                raise ManifestError("SYSTEM_EXTERNAL_DEPENDENCY_SET_INVALID")
            paths = dependency.get("critical_resolved_paths")
            hashes = dependency.get("critical_sha256")
            if not isinstance(paths, list) or not isinstance(hashes, dict) or set(paths) != set(hashes):
                raise ManifestError("SYSTEM_EXTERNAL_DEPENDENCY_SET_INVALID")
            if any(not SHA256_RE.fullmatch(value) for value in hashes.values()):
                raise ManifestError("SYSTEM_EXTERNAL_DEPENDENCY_SET_INVALID")
            for resolved_path, expected_hash in hashes.items():
                physical = Path(resolved_path)
                try:
                    info = physical.lstat()
                except OSError as exc:
                    raise ManifestError("SYSTEM_EXTERNAL_DEPENDENCY_UNAVAILABLE") from exc
                if physical.is_symlink() or not physical.is_file() or info.st_uid != 0 or (info.st_mode & 0o022):
                    raise ManifestError("SYSTEM_EXTERNAL_DEPENDENCY_UNSAFE")
                if hashlib.sha256(physical.read_bytes()).hexdigest() != expected_hash:
                    raise ManifestError("SYSTEM_EXTERNAL_DEPENDENCY_HASH_MISMATCH")
    elif external_path is not None:
        raise ManifestError("SYSTEM_EXTERNAL_DEPENDENCY_ROLE_INVALID")
    peer_uid = manifest.get("socket_peer_uid")
    if peer_uid is not None and (not isinstance(peer_uid, int) or peer_uid < 0):
        raise ManifestError("SOCKET_PEER_UID_POLICY_INVALID")
    for key in ("authority_root", "runtime_parent", "probe_executor_id"):
        if key in manifest and (not isinstance(manifest[key], str) or not manifest[key]):
            raise ManifestError("PROBE_POLICY_INVALID")
    if "probe_toolchain_components" in manifest:
        values = manifest["probe_toolchain_components"]
        if not isinstance(values, list) or any(not isinstance(value, str) or Path(value).is_absolute() or ".." in Path(value).parts for value in values):
            raise ManifestError("PROBE_POLICY_INVALID")
    mediation = manifest.get("mediation_policy")
    if mediation is not None and (not isinstance(mediation, dict) or mediation.get("host_view") != "bind,ro"):
        raise ManifestError("MEDIATION_POLICY_INVALID")
    selector = manifest.get("release_selector_policy")
    if selector is not None:
        if not isinstance(selector, dict) or selector.get("path") != "/usr/local/lib/subtranslate-guard/current" or selector.get("must_be_root_owned_symlink") is not True or selector.get("target_prefix") != "/usr/local/lib/subtranslate-guard/releases/":
            raise ManifestError("RELEASE_SELECTOR_POLICY_INVALID")


def validate_final_manifest(manifest: dict[str, Any], bundle_root: Path) -> None:
    """Final installation validation rejects unresolved source/key placeholders."""
    validate_manifest(manifest, bundle_root)
    # A source-closure manifest opts into the complete installed contract by
    # carrying the external dependency role.  Legacy fixture manifests remain
    # valid for offline regression tests, but cannot be promoted as final
    # installation manifests without the complete R2 role set.
    roles = manifest.get("component_roles", {})
    if "system_external_dependency_set" in roles and not SOURCE_CLOSURE_COMPONENT_ROLES.issubset(roles):
        raise ManifestError("SOURCE_CLOSURE_COMPONENT_ROLES_INCOMPLETE")
    if not re.fullmatch(r"sha1:[0-9a-f]{40}", manifest["source_git"]):
        raise ManifestError("SOURCE_COMMIT_AUTHORITY_UNRESOLVED")
    if not re.fullmatch(r"sha1:[0-9a-f]{40}", manifest["source_tree"]):
        raise ManifestError("SOURCE_TREE_AUTHORITY_UNRESOLVED")
    if manifest["public_key_id"].endswith("0" * 64):
        raise ManifestError("PUBLIC_KEY_PLACEHOLDER")
    forbidden = {"TODO", "UNKNOWN", "UNRESOLVED", "current-untracked", "0000000000000000000000000000000000000000"}
    if any(any(token in str(value) for token in forbidden) for value in manifest.values()):
        raise ManifestError("MANIFEST_PLACEHOLDER_UNRESOLVED")
