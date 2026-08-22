"""Hash-bound installation manifest helpers; no installation side effects."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
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
    "state_layout",
    "release_selector_policy",
    "public_key_policy",
    "release_id", "release_root", "current_selector_target",
    "executor_path", "durability_path", "service_unit_sha256",
    "socket_unit_sha256", "mediation_mount_unit_sha256", "sudoers_sha256",
    "max_claims", "max_applies", "max_retries", "auto_rearm",
})
PUBLIC_KEY_ID_RE = re.compile(r"^ed25519-sha256:[0-9a-f]{64}$")

class ManifestError(ValueError): pass
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# systemd escapes ``-`` as the two literal characters ``\\x2d`` in a path
# unit name.  This is the one installed component whose relative path is
# intentionally allowed to contain a backslash.  The exception is exact and
# role-scoped; every other manifest path remains subject to the normal
# backslash/traversal rejection below.
EXPECTED_MEDIATION_MOUNT_SOURCE_PATH = (
    "systemd/home-palhacinho-codex\\x2dprojects-anime\\x2dsubtitle\\x2dtranslator"
    "\\x2dreview-runtime\\x2devidence-V238_E07_R6C_B4_RECOVERY.mount"
)


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
    release_id = source_git[5:] if source_git.startswith("sha1:") else "UNRESOLVED"
    release_root = f"/usr/local/lib/subtranslate-guard/releases/{release_id}"
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "source_git": source_git, "source_tree": source_tree,
        "release_id": release_id,
        "release_root": release_root,
        "current_selector_target": release_root,
        "components": dict(components), "dependency_list": sorted(components),
        "component_roles": dict(component_roles), "executor_id": EXECUTOR_ID,
        "executor_sha256": components[EXECUTOR_RELATIVE],
        "durability_sha256": components[component_roles["durability"]],
        "interpreter": {"declared_path": "/usr/bin/python3.12", "resolved_path": "/usr/bin/python3.12", "sha256": interpreter_sha256},
        "public_key_id": public_key_id, "fixed_action_id": ACTION_ID,
        "public_key_policy": {
            "algorithm": "Ed25519",
            "encoding": "PEM SubjectPublicKeyInfo",
            "path": "/etc/subtranslate-guard/issuer.ed25519.pub",
            "id_algorithm": "ed25519-sha256-raw-public-key",
        },
        "fixed_argv_identity": hashlib.sha256(json.dumps(["/usr/bin/python3.12", "-I", "-B", EXECUTOR_RELATIVE, "--apply"], separators=(",", ":")).encode()).hexdigest(),
        "executor_path": f"{release_root}/{EXECUTOR_RELATIVE}",
        "durability_path": f"{release_root}/src/subtranslate/v238_per_call_durability.py",
        "max_claims": 1, "max_applies": 1, "max_retries": 0, "auto_rearm": False,
        "socket_policy": "/run/subtranslate-guard/guard.sock;uid-gated;fixed-frame",
        "state_root_policy": "/var/lib/subtranslate-guard;0700;guard-owned",
        "target_policy": "fixed-B4-runtime-target;preexec-revalidation",
        "backup_policy": "/var/lib/subtranslate-guard/backups;before-publish",
        "uid_gid_policy": "subtranslate-guard:subtranslate-guard",
        "unit_hashes": {"service": components[component_roles["systemd_service"]], "socket": components[component_roles["systemd_socket"]]},
        "service_unit_sha256": components[component_roles["systemd_service"]],
        "socket_unit_sha256": components[component_roles["systemd_socket"]],
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
    if "sudoers_policy" in component_roles:
        manifest["sudoers_sha256"] = components[component_roles["sudoers_policy"]]
    if "security_component_roles" not in manifest:
        manifest["security_component_roles"] = sorted(set(component_roles) - {"structured_tool"})
    if "non_security_component_roles" not in manifest:
        manifest["non_security_component_roles"] = ["structured_tool"]
    if "mediation_mount" in component_roles:
        manifest["unit_hashes"]["mount"] = components[component_roles["mediation_mount"]]
        manifest["mediation_mount_unit_sha256"] = components[component_roles["mediation_mount"]]
        manifest["mediation_policy"] = {
            "canonical_b4": "/home/palhacinho/codex-projects/anime-subtitle-translator-review/runtime-evidence/V238_E07_R6C_B4_RECOVERY",
            "backing_b4": "/var/lib/subtranslate-guard/recovery-targets/V238_E07_R6C_B4_RECOVERY",
            "host_view": "bind,ro",
            "service_view": "ProtectHome=tmpfs;BindPaths;ReadWritePaths",
            "mount_unit": EXPECTED_MEDIATION_MOUNT_SOURCE_PATH,
        }
        manifest["state_layout"] = {
            "root": "/var/lib/subtranslate-guard",
            "directories": ["armed", "claimed", "terminal", "journal", "locks", "backups", "recovery-targets"],
            "owner": "subtranslate-guard",
            "group": "subtranslate-guard",
            "mode": "0700",
        }
    if "probe_engine" in component_roles and "probe_entrypoint" in component_roles:
        manifest["authority_root"] = "/home/palhacinho/codex-projects/anime-subtitle-translator-review"
        manifest["runtime_parent"] = "/home/palhacinho/codex-projects/anime-subtitle-translator-review/runtime-evidence"
        manifest["probe_toolchain_components"] = [EXECUTOR_RELATIVE, "src/subtranslate/v238_per_call_durability.py"]
        manifest["probe_executor_id"] = EXECUTOR_ID
    manifest["manifest_fingerprint"] = manifest_fingerprint(manifest)
    return manifest

def canonical_manifest(manifest: dict[str, Any]) -> bytes:
    body = {key: value for key, value in manifest.items() if key != "manifest_fingerprint"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()

def manifest_fingerprint(manifest: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_manifest(manifest)).hexdigest()


def validate_current_release_selector(
    selector: Path,
    expected_release: Path,
    *,
    stat_provider=os.lstat,
    readlink_provider=os.readlink,
) -> Path:
    """Validate the fixed, root-controlled ``current`` release selector.

    The selector is a symlink, so checking its inode's mode bits is a false
    positive: symlink modes are conventionally ``0777`` and are not
    permission gates.  Security is instead established by validating the
    selector and every boundary in the release chain, then requiring an
    absolute, direct target inside the sibling ``releases`` directory.  The
    injectable providers are private test seams; production callers use the
    fixed filesystem APIs and fixed selector path from the manifest policy.
    """
    selector = Path(selector)
    if not selector.is_absolute():
        raise ManifestError("RELEASE_SELECTOR_UNSAFE")
    try:
        selector_info = stat_provider(selector)
    except OSError as exc:
        raise ManifestError("RELEASE_SELECTOR_UNAVAILABLE") from exc
    if not stat.S_ISLNK(selector_info.st_mode) or selector_info.st_uid != 0:
        raise ManifestError("RELEASE_SELECTOR_UNSAFE")

    # Validate the lexical parent chain.  A writable parent can replace the
    # selector even when the selector inode itself is root-owned.
    for parent in (selector.parent, *selector.parent.parents):
        try:
            parent_info = stat_provider(parent)
        except OSError as exc:
            raise ManifestError("RELEASE_SELECTOR_PARENT_UNSAFE") from exc
        if stat.S_ISLNK(parent_info.st_mode) or not stat.S_ISDIR(parent_info.st_mode):
            raise ManifestError("RELEASE_SELECTOR_PARENT_UNSAFE")
        if parent_info.st_uid != 0 or (parent_info.st_mode & 0o022):
            raise ManifestError("RELEASE_SELECTOR_PARENT_UNSAFE")

    try:
        raw_target = readlink_provider(selector)
    except OSError as exc:
        raise ManifestError("RELEASE_SELECTOR_TARGET_INVALID") from exc
    if not isinstance(raw_target, str) or not raw_target or "\x00" in raw_target:
        raise ManifestError("RELEASE_SELECTOR_TARGET_INVALID")
    target = Path(raw_target)
    # Relative links make the target depend on a replaceable parent and are
    # therefore rejected.  The installed selector policy uses an absolute
    # target under the fixed releases boundary.
    if not target.is_absolute() or ".." in target.parts:
        raise ManifestError("RELEASE_SELECTOR_ESCAPE")

    releases_lexical = selector.parent / "releases"
    try:
        releases_lexical_info = stat_provider(releases_lexical)
    except OSError as exc:
        raise ManifestError("RELEASE_SELECTOR_PARENT_UNSAFE") from exc
    if (stat.S_ISLNK(releases_lexical_info.st_mode)
            or not stat.S_ISDIR(releases_lexical_info.st_mode)
            or releases_lexical_info.st_uid != 0
            or (releases_lexical_info.st_mode & 0o022)):
        raise ManifestError("RELEASE_SELECTOR_PARENT_UNSAFE")
    try:
        expected_raw = Path(expected_release)
        if not expected_raw.is_absolute():
            raise ManifestError("RELEASE_SELECTOR_TARGET_INVALID")
        expected_info = stat_provider(expected_raw)
        if stat.S_ISLNK(expected_info.st_mode):
            raise ManifestError("RELEASE_SELECTOR_TARGET_SYMLINK")
        expected_release = expected_raw.resolve(strict=True)
        releases = releases_lexical.resolve(strict=True)
        resolved = target.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ManifestError("RELEASE_SELECTOR_TARGET_INVALID") from exc
    try:
        target.relative_to(releases_lexical)
    except ValueError as exc:
        raise ManifestError("RELEASE_SELECTOR_ESCAPE") from exc
    # Check every lexical component of the raw target, not only the final
    # resolved directory.  This rejects an intermediate symlink chain.
    for node in (target, *target.parents):
        try:
            node_info = stat_provider(node)
        except OSError as exc:
            raise ManifestError("RELEASE_SELECTOR_TARGET_INVALID") from exc
        if stat.S_ISLNK(node_info.st_mode):
            raise ManifestError("RELEASE_SELECTOR_SYMLINK_CHAIN")
        if node == releases_lexical:
            break
    try:
        resolved.relative_to(releases)
    except ValueError as exc:
        raise ManifestError("RELEASE_SELECTOR_ESCAPE") from exc
    if resolved == releases or resolved != expected_release:
        raise ManifestError("RELEASE_SELECTOR_TARGET_MISMATCH")

    # The target itself must be a real, non-writable release directory.  A
    # symlink chain is rejected by lstat rather than silently followed.
    try:
        release_info = stat_provider(resolved)
        releases_info = stat_provider(releases)
    except OSError as exc:
        raise ManifestError("RELEASE_SELECTOR_TARGET_INVALID") from exc
    if (stat.S_ISLNK(release_info.st_mode) or not stat.S_ISDIR(release_info.st_mode)
            or release_info.st_uid != 0 or (release_info.st_mode & 0o022)):
        raise ManifestError("RELEASE_SELECTOR_TARGET_UNSAFE")
    if (stat.S_ISLNK(releases_info.st_mode) or not stat.S_ISDIR(releases_info.st_mode)
            or releases_info.st_uid != 0 or (releases_info.st_mode & 0o022)):
        raise ManifestError("RELEASE_SELECTOR_PARENT_UNSAFE")
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
    expected_unit_keys = {"service", "socket"} | ({"mount"} if "mediation_mount" in roles else set())
    if not isinstance(unit_hashes, dict) or set(unit_hashes) != expected_unit_keys or any(not SHA256_RE.fullmatch(value) for value in unit_hashes.values()):
        raise ManifestError("MANIFEST_UNIT_HASHES_INVALID")
    root = Path(bundle_root)
    if root.is_symlink() or not root.is_dir():
        raise ManifestError("BUNDLE_ROOT_UNSAFE")
    role_by_path = {value: role for role, value in roles.items()}
    security_roles_for_hash = set(manifest.get("security_component_roles", roles))
    for relative, expected in components.items():
        if not isinstance(relative, str) or not isinstance(expected, str): raise ManifestError("MANIFEST_COMPONENTS_INVALID")
        relative_path = Path(relative)
        role = role_by_path.get(relative)
        mount_path_exception = role == "mediation_mount" and relative == EXPECTED_MEDIATION_MOUNT_SOURCE_PATH
        if relative_path.is_absolute() or str(relative_path) != relative or ".." in relative_path.parts or ("\\" in relative and not mount_path_exception) or not SHA256_RE.fullmatch(expected):
            raise ManifestError("MANIFEST_PATH_OR_HASH_INVALID")
        path = root / relative_path
        try: path.relative_to(root)
        except ValueError as exc: raise ManifestError("MANIFEST_PATH_TRAVERSAL") from exc
        if any(parent.is_symlink() for parent in (path.parent, *path.parents)):
            raise ManifestError("BUNDLE_PARENT_SYMLINK")
        if path.is_symlink() or not path.is_file():
            raise ManifestError("BUNDLE_COMPONENT_UNSAFE")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
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
    if mediation is not None:
        if (not isinstance(mediation, dict)
                or mediation.get("canonical_b4") != "/home/palhacinho/codex-projects/anime-subtitle-translator-review/runtime-evidence/V238_E07_R6C_B4_RECOVERY"
                or mediation.get("backing_b4") != "/var/lib/subtranslate-guard/recovery-targets/V238_E07_R6C_B4_RECOVERY"
                or mediation.get("host_view") != "bind,ro"
                or mediation.get("service_view") != "ProtectHome=tmpfs;BindPaths;ReadWritePaths"
                or mediation.get("mount_unit") != EXPECTED_MEDIATION_MOUNT_SOURCE_PATH):
            raise ManifestError("MEDIATION_POLICY_INVALID")
    selector = manifest.get("release_selector_policy")
    if selector is not None:
        if not isinstance(selector, dict) or selector.get("path") != "/usr/local/lib/subtranslate-guard/current" or selector.get("must_be_root_owned_symlink") is not True or selector.get("target_prefix") != "/usr/local/lib/subtranslate-guard/releases/":
            raise ManifestError("RELEASE_SELECTOR_POLICY_INVALID")
    for hash_field in ("service_unit_sha256", "socket_unit_sha256", "mediation_mount_unit_sha256", "sudoers_sha256"):
        if hash_field in manifest and not SHA256_RE.fullmatch(manifest[hash_field]):
            raise ManifestError("MANIFEST_COMPONENT_HASH_INVALID:" + hash_field)
    for numeric_field in ("max_claims", "max_applies", "max_retries"):
        if numeric_field in manifest and (not isinstance(manifest[numeric_field], int) or isinstance(manifest[numeric_field], bool) or manifest[numeric_field] < 0):
            raise ManifestError("MANIFEST_CAPABILITY_LIMIT_INVALID")
    if "auto_rearm" in manifest and not isinstance(manifest["auto_rearm"], bool):
        raise ManifestError("MANIFEST_CAPABILITY_LIMIT_INVALID")
    for path_field in ("release_root", "current_selector_target", "executor_path", "durability_path"):
        if path_field in manifest and (not isinstance(manifest[path_field], str) or not manifest[path_field].startswith("/")):
            raise ManifestError("MANIFEST_PATH_POLICY_INVALID")
    public_policy = manifest.get("public_key_policy")
    if public_policy is not None:
        if (not isinstance(public_policy, dict)
                or public_policy.get("algorithm") != "Ed25519"
                or public_policy.get("encoding") != "PEM SubjectPublicKeyInfo"
                or public_policy.get("path") != "/etc/subtranslate-guard/issuer.ed25519.pub"
                or public_policy.get("id_algorithm") != "ed25519-sha256-raw-public-key"):
            raise ManifestError("PUBLIC_KEY_POLICY_INVALID")
    layout = manifest.get("state_layout")
    if layout is not None:
        if (not isinstance(layout, dict)
                or layout.get("root") != "/var/lib/subtranslate-guard"
                or layout.get("directories") != ["armed", "claimed", "terminal", "journal", "locks", "backups", "recovery-targets"]
                or layout.get("owner") != "subtranslate-guard"
                or layout.get("group") != "subtranslate-guard"
                or layout.get("mode") != "0700"):
            raise ManifestError("STATE_LAYOUT_POLICY_INVALID")


def validate_final_manifest(manifest: dict[str, Any], bundle_root: Path) -> None:
    """Final installation validation rejects unresolved source/key placeholders."""
    validate_manifest(manifest, bundle_root)
    # A source-closure manifest opts into the complete installed contract by
    # carrying the external dependency role.  Legacy fixture manifests remain
    # valid for offline regression tests, but cannot be promoted as final
    # installation manifests without the complete R2 role set.
    roles = manifest.get("component_roles", {})
    components = manifest.get("components", {})
    if "system_external_dependency_set" in roles and not SOURCE_CLOSURE_COMPONENT_ROLES.issubset(roles):
        raise ManifestError("SOURCE_CLOSURE_COMPONENT_ROLES_INCOMPLETE")
    if not re.fullmatch(r"sha1:[0-9a-f]{40}", manifest["source_git"]):
        raise ManifestError("SOURCE_COMMIT_AUTHORITY_UNRESOLVED")
    if not re.fullmatch(r"sha1:[0-9a-f]{40}", manifest["source_tree"]):
        raise ManifestError("SOURCE_TREE_AUTHORITY_UNRESOLVED")
    if manifest["public_key_id"].endswith("0" * 64):
        raise ManifestError("PUBLIC_KEY_PLACEHOLDER")
    public_policy = manifest.get("public_key_policy")
    if not isinstance(public_policy, dict):
        raise ManifestError("PUBLIC_KEY_POLICY_REQUIRED")
    if (public_policy.get("algorithm") != "Ed25519"
            or public_policy.get("encoding") != "PEM SubjectPublicKeyInfo"
            or public_policy.get("path") != "/etc/subtranslate-guard/issuer.ed25519.pub"
            or public_policy.get("id_algorithm") != "ed25519-sha256-raw-public-key"):
            raise ManifestError("PUBLIC_KEY_POLICY_INVALID")
    if "mediation_mount" in roles:
        if not isinstance(manifest.get("mediation_policy"), dict) or not isinstance(manifest.get("state_layout"), dict):
            raise ManifestError("MEDIATION_POLICY_REQUIRED")
    if "system_external_dependency_set" in roles:
        release_id = manifest.get("release_id")
        expected_release_root = f"/usr/local/lib/subtranslate-guard/releases/{release_id}"
        if (not isinstance(release_id, str) or not re.fullmatch(r"[0-9a-f]{40}", release_id)
                or manifest.get("release_root") != expected_release_root
                or manifest.get("current_selector_target") != expected_release_root
                or manifest.get("executor_path") != f"{expected_release_root}/{EXECUTOR_RELATIVE}"
                or manifest.get("durability_path") != f"{expected_release_root}/src/subtranslate/v238_per_call_durability.py"
                or manifest.get("max_claims") != 1
                or manifest.get("max_applies") != 1
                or manifest.get("max_retries") != 0
                or manifest.get("auto_rearm") is not False):
            raise ManifestError("FINAL_RELEASE_BINDINGS_INVALID")
        if (manifest.get("service_unit_sha256") != components[roles["systemd_service"]]
                or manifest.get("socket_unit_sha256") != components[roles["systemd_socket"]]
                or manifest.get("sudoers_sha256") != components[roles["sudoers_policy"]]):
            raise ManifestError("FINAL_POLICY_HASH_BINDING_INVALID")
        if "mediation_mount" in roles and manifest.get("mediation_mount_unit_sha256") != components[roles["mediation_mount"]]:
            raise ManifestError("FINAL_MEDIATION_HASH_BINDING_INVALID")
    forbidden = {"TODO", "UNKNOWN", "UNRESOLVED", "current-untracked", "0000000000000000000000000000000000000000"}
    if any(any(token in str(value) for token in forbidden) for value in manifest.values()):
        raise ManifestError("MANIFEST_PLACEHOLDER_UNRESOLVED")
