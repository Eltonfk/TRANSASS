"""Source-only mediation installer design.

The foundation installer publishes the immutable release and identity
skeleton.  This module describes the later mediation installation phase.  A
plan is completely read-only and has no caller-controlled destinations.  The
apply implementation is intentionally restricted to a root-controlled copy
of this file and is not exercised by the source-closure gate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterable

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


class MediationInstallerError(RuntimeError):
    """Fail-closed mediation installer error."""


SOURCE_COMMIT_RE = __import__("re").compile(r"^[0-9a-fA-F]{40}$")
INSTALL_ROOT = Path("/usr/local/lib/subtranslate-guard")
RELEASES_ROOT = INSTALL_ROOT / "releases"
CURRENT_SELECTOR = INSTALL_ROOT / "current"
BOOTSTRAP_ROOT = INSTALL_ROOT / "bootstrap"
MEDIATION_BOOTSTRAP_PATH = BOOTSTRAP_ROOT / "subtranslate_guard_mediation_installer.py"
ETC_ROOT = Path("/etc/subtranslate-guard")
KEYS_ROOT = ETC_ROOT / "keys"
PRIVATE_KEY_PATH = KEYS_ROOT / "issuer.ed25519"
PUBLIC_KEY_PATH = ETC_ROOT / "issuer.ed25519.pub"
MANIFEST_PATH = ETC_ROOT / "manifest.json"
STATE_ROOT = Path("/var/lib/subtranslate-guard")
STATE_DIRECTORIES = ("armed", "claimed", "terminal", "journal", "locks", "backups", "recovery-targets")
SYSTEMD_ROOT = Path("/etc/systemd/system")
SERVICE_UNIT_PATH = SYSTEMD_ROOT / "subtranslate-guard.service"
SOCKET_UNIT_PATH = SYSTEMD_ROOT / "subtranslate-guard.socket"
MOUNT_UNIT_NAME = (
    "home-palhacinho-codex\\x2dprojects-anime\\x2dsubtitle\\x2dtranslator"
    "\\x2dreview-runtime\\x2devidence-V238_E07_R6C_B4_RECOVERY.mount"
)
MOUNT_UNIT_PATH = SYSTEMD_ROOT / MOUNT_UNIT_NAME
SUDOERS_PATH = Path("/etc/sudoers.d/subtranslate-guard-arm")
STRUCTURED_TOOL_PATH = INSTALL_ROOT / "opencode/subtranslate_recovery_apply_once.ts"
CLIENT_GROUP = "subtranslate-guard-client"
GUARD_USER = "subtranslate-guard"
GUARD_GROUP = "subtranslate-guard"
NOLOGIN = "/usr/sbin/nologin"
STATE_DIRECTORY_POLICY = {
    name: {
        "owner": GUARD_USER,
        "group": GUARD_GROUP,
        "mode": "0700",
        "creator_phase": "mediation_install",
        "allowed_writer": "subtranslate-guard" if name != "recovery-targets" else "subtranslate-guard;mediation_installer_pre_mount",
        "allowed_reader": "subtranslate-guard;root_issuer_read_only" if name != "recovery-targets" else "subtranslate-guard",
        "purpose": "capability lifecycle state" if name != "recovery-targets" else "guard-owned private B4 backing boundary",
    }
    for name in STATE_DIRECTORIES
}
SOCKET_PATH = Path("/run/subtranslate-guard/guard.sock")
B4_CANONICAL = Path(
    "/home/palhacinho/codex-projects/anime-subtitle-translator-review/"
    "runtime-evidence/V238_E07_R6C_B4_RECOVERY"
)
B4_BACKING = STATE_ROOT / "recovery-targets/V238_E07_R6C_B4_RECOVERY"
INTERPRETER = Path("/usr/bin/python3.12")
ACTION_ID = "RECOVERY_LEDGER_REPREPARATION"
REQUEST_LITERAL = "EXECUTE_CURRENT_ARMED_RECOVERY_CAPABILITY"
PUBLIC_KEY_ID_PREFIX = "ed25519-sha256:"
MAX_CLAIMS = 1
MAX_APPLIES = 1
MAX_RETRIES = 0
AUTO_REARM_ALLOWED = False
_TEST_RELEASE_TOKEN = object()
FAILURE_INJECTION_STAGES = (
    "before_first_write", "after_state_directory", "after_private_key_temp",
    "after_private_key_publication", "after_public_key_publication", "after_manifest_temp",
    "after_manifest_publication", "after_current_temp", "after_current_publication",
    "after_service_unit", "after_socket_unit", "after_mount_unit", "after_sudoers",
    "after_structured_tool", "after_client_membership", "before_final_validation",
)

# This is the release contract consumed by the later mediation installer.
# It is deliberately duplicated as data (rather than importing candidate
# code) so a root-controlled installer has a closed, immutable expectation
# for every byte it will publish.
RUNTIME_RELEASE_CONTRACT = {
    'src/subtranslate/recovery_guard/__init__.py': ('479ba0beda70001e0510d1ef37c175859abd61f8', 'ff20180e6d9509e06c15246ae51634f06f6ed67e81a4fcc16d2c8a117678a266'),
    'src/subtranslate/recovery_guard/core.py': ('d2d1d6b40bf99ae4ca394f990dcaaa4976c49dfb', '4d710c3a5ef3ede9e00e50d42c078cdb28d3e43adefcb267663dc42ffd764176'),
    'src/subtranslate/recovery_guard/production/__init__.py': ('4b88fef7fef5ffce86e3478fe56f2a44ac3d445a', '01338c4763e064cf6ae51302735a5bad29b4dbf93c9a95bfa1b9ab23d07db01e'),
    'src/subtranslate/recovery_guard/production/bindings.py': ('a88eaa63784bc7313e12f742772e070a3111dc19', '17e5cace5fd7673cd05f846b4b7fd554e1ff6bd4f412805b02497820af0f66d8'),
    'src/subtranslate/recovery_guard/production/broker.py': ('09ee59929479ca4708888fe8f226805abf5a154f', 'c6885c427fef1851762c3e6f1f4acbb134e1b603406ff9a8af800159c7725511'),
    'src/subtranslate/recovery_guard/production/crypto.py': ('710e4591cf0f57320414d43bcf303b7b2ef5e29f', 'eeac85802bc49b4607901c5bb034e4fb75702ce39b28fa18eee5b0e5e1c63c97'),
    'src/subtranslate/recovery_guard/production/issuer.py': ('0cb769a17029a15ad4d1ff9ea6b672e82c4b2f0f', 'd890aab2c1e1958c0730c39547e9c82b6e74a5014b779bf86c5abdc21c585d18'),
    'src/subtranslate/recovery_guard/production/issuer_cli.py': ('35c984768917d0cb4f69aa6ad82fa34eb5d0f45d', '696f205928d247a4cd5474eb4f286b0bf98313fa0caaca47a1176dfc302c0a98'),
    'src/subtranslate/recovery_guard/production/issuer_launcher.py': ('091978b6acce0886dec804c842fc9217a3f1361f', '36a60c8910bc16100f2ac51fa63c78ffb78af6287291be5d45633cf806b5719c'),
    'src/subtranslate/recovery_guard/production/journal.py': ('cff8f2f6f59d39cf51b222840fbe2fc680daacf9', '2855e8d8aadcc7c03ac0f3f38255e802b47693889d2b3f9fd54236b6b5c29254'),
    'src/subtranslate/recovery_guard/production/manifest.py': ('5038005370a6065e44e837ac40d3291ee86e2945', '99275dac848e3b79c03523862e75d024465b6c02eac4cd8174601c97448e077a'),
    'src/subtranslate/recovery_guard/production/probe_engine.py': ('4f013fc27c307a4112990cf640fac10ce021769f', '56ab2a465219c656c4ebb0fe64a45221abe04303342a7bb22a135c4eebae06e1'),
    'src/subtranslate/recovery_guard/production/protocol.py': ('e87fe3115227ed4b9b9019f5dcda73e6e60f769f', '79dc36d5ed0272cbc025472f0d4822c27aa542382cc895be631329a04f503f9b'),
    'src/subtranslate/recovery_guard/production/provider.py': ('7c9521557025ff29af82c7196b00e8af08aa0eb1', '973f478b5216c1160850923810e0ab821e87fe1b3fb26ae2d5d1320a8797bfa1'),
    'src/subtranslate/recovery_guard/production/runner.py': ('1bc43a4807b78adee553c617a11a1a002b3467b8', '5fef458996c4f04e0e465e87913960812ec4e24fd66ced15dbe2b6b6a037bf1a'),
    'src/subtranslate/recovery_guard/production/schema.py': ('ad1314629e6eed9283a3d9cd41396a2404afcb81', 'e1b133bdd297d6cd226bfca1bf1025138f5988baeff0947d90bac4c65881f144'),
    'src/subtranslate/recovery_guard/production/service.py': ('4854e5dc477f93af724fb385375f4679c71bc96f', 'bbb8d6e1e67ccb89ba31065c77f1ce3fc06efc89f3f9986baa27ad803d6ccb57'),
    'src/subtranslate/recovery_guard/production/service_launcher.py': ('4886804ffc36b4ac04d0a7fc1e9901983d425539', '621356774d8f5d3f31bf4116c6bca9a615baf4c4ecf746d80f10db7d19c68549'),
    'src/subtranslate/recovery_guard/production/service_main.py': ('2e748ae7d2b77af166a3d726ba3dd4ce2061097f', '83cd7bb363ccac7e380c91bf9b062726e7c32b87d45b27862804e1afa09a7b87'),
    'src/subtranslate/recovery_guard/production/state.py': ('1f7a8971a6007953a7f4f9edcd4af843faf42b57', '09de8c30ea2de6a34e83e2fe2fc6f9884dae9afb9cd0243d959bcae2be44e47b'),
    '.opencode/tools/subtranslate_readonly_probe.py': ('5176f8cf4bf9352995d9f5f1fde60af6aef9bef1', '45f37a97e67195a84033c694b867e46f599891215bad3b876334018de7d268d5'),
    '.opencode/tools/subtranslate_recovery_ledger_reprepare_v2.py': ('afa2555b35e178ad7429c978f7bd7f3263b458e4', 'ca95eac8680897d387878f69a87b089ff60e81e598fb051fcbb97606aeb408ad'),
    'src/subtranslate/v238_per_call_durability.py': ('656eb2e2b6c20bfec0ac7c5eca79e49fce131fc8', '5caeb33f1bb21fbc90b7195b791e061bc46a7bddedb49bb15f52908b09d23585'),
    'systemd/subtranslate-guard.service': ('a224ffd04100ffbd3d5ddcccf2debe703862ae1f', '14c878d315a5e6b7bacc5b392f257f1a568c26597ee3723ce7b55aab1d69d006'),
    'systemd/subtranslate-guard.socket': ('77e5b396debbb6b7a5f6b8c32d6e211662cc6053', 'fd3a17bb19a9a0039f82e3984bbebf12f835c3e483bdc642bdc12b04ecc68255'),
    'systemd/home-palhacinho-codex\\x2dprojects-anime\\x2dsubtitle\\x2dtranslator\\x2dreview-runtime\\x2devidence-V238_E07_R6C_B4_RECOVERY.mount': ('cbceb95963299c844eebbea8fdc363a1fa67896d', 'fc477c093d8bdf052e35132b50fd0a2b22c628374ca06d1f38b4bff54ed97a88'),
    'sudoers/subtranslate-guard-arm': ('386e75710d66342515c0f11f9ca1a6b761a8a9fb', '7685fc71d5ce5384ec9bf0abbb3ef0112f6f78f4e37ec4874a8a5a6934c0548d'),
    'opencode/subtranslate_recovery_apply_once.ts': ('92776a0e10b61d33021ee910a114651175a9cf21', 'ddfdb24b1047522bf750174d9e70364ab8d34494608d4f10c791a6c7dbc89d7c'),
    'manifests/system-external-dependencies.json': ('094cb740a54d05eb8a9e8cabe708681b14814265', '3393b7fbde1e0132cc48bf7e740d645121919b86ad84321bd59f2e2ed611b8c8'),
    'manifests/interpreter.identity': ('a01d81c2222ba729918be5bf8261ac5d1d885aae', '251ccfcd2e674f7179aece078f9d69c47f168905552bf747c2310c2a41b75fad'),
}

# These are the only runtime/policy objects that may be consumed from the
# protected release.  The foundation contract carries their immutable Git
# blob/SHA-256 identities; this installer additionally checks the release
# boundary and the observed bytes before planning a publication.
RUNTIME_RELEASE_PATHS = (
    "src/subtranslate/recovery_guard/__init__.py",
    "src/subtranslate/recovery_guard/core.py",
    "src/subtranslate/recovery_guard/production/__init__.py",
    "src/subtranslate/recovery_guard/production/bindings.py",
    "src/subtranslate/recovery_guard/production/broker.py",
    "src/subtranslate/recovery_guard/production/crypto.py",
    "src/subtranslate/recovery_guard/production/issuer.py",
    "src/subtranslate/recovery_guard/production/issuer_cli.py",
    "src/subtranslate/recovery_guard/production/issuer_launcher.py",
    "src/subtranslate/recovery_guard/production/journal.py",
    "src/subtranslate/recovery_guard/production/manifest.py",
    "src/subtranslate/recovery_guard/production/probe_engine.py",
    "src/subtranslate/recovery_guard/production/protocol.py",
    "src/subtranslate/recovery_guard/production/provider.py",
    "src/subtranslate/recovery_guard/production/runner.py",
    "src/subtranslate/recovery_guard/production/schema.py",
    "src/subtranslate/recovery_guard/production/service.py",
    "src/subtranslate/recovery_guard/production/service_launcher.py",
    "src/subtranslate/recovery_guard/production/service_main.py",
    "src/subtranslate/recovery_guard/production/state.py",
    ".opencode/tools/subtranslate_readonly_probe.py",
    ".opencode/tools/subtranslate_recovery_ledger_reprepare_v2.py",
    "src/subtranslate/v238_per_call_durability.py",
    "systemd/subtranslate-guard.service",
    "systemd/subtranslate-guard.socket",
    "systemd/" + MOUNT_UNIT_NAME,
    "sudoers/subtranslate-guard-arm",
    "opencode/subtranslate_recovery_apply_once.ts",
    "manifests/system-external-dependencies.json",
    "manifests/interpreter.identity",
)

ROLE_BY_RELEASE_PATH = {
    "systemd/subtranslate-guard.service": "systemd_service",
    "systemd/subtranslate-guard.socket": "systemd_socket",
    "systemd/" + MOUNT_UNIT_NAME: "mediation_mount",
    "sudoers/subtranslate-guard-arm": "sudoers_policy",
    "opencode/subtranslate_recovery_apply_once.ts": "structured_tool",
    "manifests/system-external-dependencies.json": "system_external_dependency_set",
    "manifests/interpreter.identity": "interpreter",
    "src/subtranslate/recovery_guard/production/probe_engine.py": "probe_engine",
    ".opencode/tools/subtranslate_readonly_probe.py": "probe_entrypoint",
    "src/subtranslate/recovery_guard/production/service_launcher.py": "service_launcher",
    "src/subtranslate/recovery_guard/production/issuer_launcher.py": "issuer_launcher",
    "src/subtranslate/recovery_guard/production/issuer_cli.py": "issuer_cli",
}


def _commit(value: str) -> str:
    if not isinstance(value, str) or SOURCE_COMMIT_RE.fullmatch(value) is None:
        raise MediationInstallerError("SOURCE_COMMIT_MUST_BE_EXACT_SHA1")
    return value.lower()


def public_key_id(public_key: Ed25519PublicKey) -> str:
    raw = public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    if len(raw) != 32:
        raise MediationInstallerError("ED25519_PUBLIC_KEY_RAW_LENGTH")
    return PUBLIC_KEY_ID_PREFIX + hashlib.sha256(raw).hexdigest()


def serialize_private_key(private_key: Ed25519PrivateKey) -> bytes:
    """Use an explicit unencrypted PKCS8 PEM representation."""
    return private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def serialize_public_key(public_key: Ed25519PublicKey) -> bytes:
    """Use an explicit SubjectPublicKeyInfo PEM representation."""
    return public_key.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def load_private_key_bytes(data: bytes) -> Ed25519PrivateKey:
    try:
        key = serialization.load_pem_private_key(data, password=None)
    except Exception as exc:  # cryptography deliberately has many error types
        raise MediationInstallerError("PRIVATE_KEY_FORMAT_INVALID") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise MediationInstallerError("PRIVATE_KEY_ALGORITHM_INVALID")
    return key


def load_public_key_bytes(data: bytes) -> Ed25519PublicKey:
    try:
        key = serialization.load_pem_public_key(data)
    except Exception as exc:
        raise MediationInstallerError("PUBLIC_KEY_FORMAT_INVALID") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise MediationInstallerError("PUBLIC_KEY_ALGORITHM_INVALID")
    return key


def validate_key_pair(private_bytes: bytes, public_bytes: bytes) -> str:
    private = load_private_key_bytes(private_bytes)
    public = load_public_key_bytes(public_bytes)
    derived = private.public_key()
    if derived.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw) != public.public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    ):
        raise MediationInstallerError("PUBLIC_PRIVATE_KEY_MISMATCH")
    return public_key_id(public)


def generate_key_material() -> tuple[bytes, bytes, str]:
    """Generate local Ed25519 material without publishing or logging it."""
    private = Ed25519PrivateKey.generate()
    private_bytes = serialize_private_key(private)
    public_bytes = serialize_public_key(private.public_key())
    return private_bytes, public_bytes, public_key_id(private.public_key())


def classify_key_state(private_bytes: bytes | None, public_bytes: bytes | None) -> str:
    """Return the only permitted idempotence outcomes for key material."""
    if private_bytes is None and public_bytes is None:
        return "GENERATE"
    if private_bytes is None or public_bytes is None:
        raise MediationInstallerError("KEY_PAIR_INCOMPLETE")
    validate_key_pair(private_bytes, public_bytes)
    return "REUSE"


def _safe_fixed(path: Path, *, absolute: bool = True) -> Path:
    if absolute and not path.is_absolute():
        raise MediationInstallerError("FIXED_PATH_NOT_ABSOLUTE")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise MediationInstallerError("FIXED_PATH_INVALID")
    return path


def _prestate(path: Path) -> dict[str, Any]:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {"path": str(path), "exists": False}
    except OSError as exc:
        return {"path": str(path), "exists": False, "error": type(exc).__name__}
    private_material = path == PRIVATE_KEY_PATH
    return {
        "path": str(path),
        "exists": True,
        "type": stat.S_IFMT(info.st_mode),
        "uid": info.st_uid,
        "gid": info.st_gid,
        "mode": format(stat.S_IMODE(info.st_mode), "04o"),
        "symlink": stat.S_ISLNK(info.st_mode),
        # A plan may inspect metadata of the private key, but it must never
        # read or serialize private-key bytes.  Apply validates the pair only
        # after entering the root-controlled key publication transaction.
        "sha256": None if private_material else (hashlib.sha256(path.read_bytes()).hexdigest() if stat.S_ISREG(info.st_mode) else None),
        "private_key_material": "redacted" if private_material else None,
    }


def _git_blob_oid(data: bytes) -> str:
    header = f"blob {len(data)}".encode("ascii") + b"\x00"
    return hashlib.sha1(header + data).hexdigest()


def _release_paths(release_root: Path, *, stat_provider=os.lstat) -> list[dict[str, Any]]:
    release_root = _safe_fixed(release_root)
    if set(RUNTIME_RELEASE_PATHS) != set(RUNTIME_RELEASE_CONTRACT):
        raise MediationInstallerError("RUNTIME_RELEASE_CONTRACT_CLOSURE_INVALID")
    result: list[dict[str, Any]] = []
    for relative in RUNTIME_RELEASE_PATHS:
        path = release_root / relative
        cursor = release_root
        for component in Path(relative).parts:
            cursor = cursor / component
            try:
                component_info = stat_provider(cursor)
            except OSError as exc:
                raise MediationInstallerError("PROTECTED_RELEASE_COMPONENT_MISSING") from exc
            if stat.S_ISLNK(component_info.st_mode):
                raise MediationInstallerError("PROTECTED_RELEASE_COMPONENT_SYMLINK")
            if cursor != path and (not stat.S_ISDIR(component_info.st_mode)
                                   or component_info.st_uid != 0
                                   or component_info.st_gid != 0
                                   or stat.S_IMODE(component_info.st_mode) != 0o755):
                raise MediationInstallerError("PROTECTED_RELEASE_DIRECTORY_UNSAFE")
        try:
            info = stat_provider(path)
        except OSError as exc:
            raise MediationInstallerError("PROTECTED_RELEASE_COMPONENT_MISSING") from exc
        if (stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode)
                or info.st_uid != 0 or info.st_gid != 0
                or stat.S_IMODE(info.st_mode) != 0o644):
            raise MediationInstallerError("PROTECTED_RELEASE_COMPONENT_UNSAFE")
        data = path.read_bytes()
        expected_oid, expected_sha = RUNTIME_RELEASE_CONTRACT[relative]
        observed_oid = _git_blob_oid(data)
        observed_sha = hashlib.sha256(data).hexdigest()
        if observed_oid != expected_oid or observed_sha != expected_sha:
            raise MediationInstallerError("PROTECTED_RELEASE_CONTRACT_MISMATCH")
        result.append({
            "path": relative,
            "role": ROLE_BY_RELEASE_PATH.get(relative, "runtime_component"),
            "git_blob_oid": observed_oid,
            "sha256": observed_sha,
            "size": len(data),
        })
    return result


def _validate_release_boundary(release_root: Path, *, stat_provider=os.lstat) -> None:
    root = _safe_fixed(release_root)
    try:
        info = stat_provider(root)
    except OSError as exc:
        raise MediationInstallerError("PROTECTED_RELEASE_UNAVAILABLE") from exc
    if (stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != 0 or info.st_gid != 0 or stat.S_IMODE(info.st_mode) != 0o755):
        raise MediationInstallerError("PROTECTED_RELEASE_BOUNDARY_UNSAFE")
    for parent in (root.parent, *root.parent.parents):
        try:
            parent_info = stat_provider(parent)
        except OSError as exc:
            raise MediationInstallerError("PROTECTED_RELEASE_PARENT_UNSAFE") from exc
        if (stat.S_ISLNK(parent_info.st_mode) or not stat.S_ISDIR(parent_info.st_mode)
                or parent_info.st_uid != 0 or (stat.S_IMODE(parent_info.st_mode) & 0o022)):
            raise MediationInstallerError("PROTECTED_RELEASE_PARENT_UNSAFE")


def _future_writeset(release_root: Path, release_id: str) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []

    def add(order: int, target: Path, action: str, owner: str, group: str, mode: str,
            source: str, poststate: str, rollback: str, effect: str = "NONE") -> None:
        actions.append({
            "order": order,
            "target": str(target),
            "action": action,
            "source_authority": source,
            "owner": owner,
            "group": group,
            "mode": mode,
            "prestate_required": "fixed path; no unsafe symlink; no caller override",
            "poststate": poststate,
            "execution_surface_effect": effect,
            "rollback_action": rollback,
        })

    order = 1
    for name in (GUARD_GROUP, CLIENT_GROUP):
        add(order, Path("/etc/group"), "ensure_system_group", "root", "root", "system", "fixed identity policy",
            f"group {name} exists with approved gid", f"remove only if created_this_run and unused")
        order += 1
    add(order, Path("/etc/passwd"), "ensure_system_user", "root", "root", "system", "fixed identity policy",
        f"{GUARD_USER} exists with primary group {GUARD_GROUP} and shell {NOLOGIN}", "remove only if created_this_run and unused"); order += 1
    for name in STATE_DIRECTORIES:
        add(order, STATE_ROOT / name, "ensure_state_directory", GUARD_USER, GUARD_GROUP, "0700", "fixed state policy",
            "directory exists, non-symlink, owner/group/mode exact", "remove only if created_this_run and empty"); order += 1
    add(order, PRIVATE_KEY_PATH, "create_or_reuse_ed25519_private_key", "root", "root", "0600", "cryptography Ed25519 policy",
        "valid private/public pair or atomically published new pair", "remove only new pair with exact identity"); order += 1
    add(order, PUBLIC_KEY_PATH, "publish_public_verification_key", "root", "root", "0644", "derived from private key bytes",
        "public PEM matches private key and public_key_id", "restore root-controlled prior bytes or remove new file"); order += 1
    add(order, MANIFEST_PATH, "publish_canonical_manifest", "root", "root", "0644", "protected release + key + dependency contracts",
        "canonical JSON validates with real public_key_id", "restore prior hash or remove new file"); order += 1
    add(order, CURRENT_SELECTOR, "publish_current_release_selector", "root", "root", "symlink", str(release_root),
        f"absolute symlink resolves exactly to {release_root}", "remove only if created_this_run and still exact"); order += 1
    add(order, SERVICE_UNIT_PATH, "publish_systemd_service_unit", "root", "root", "0644", str(release_root / "systemd/subtranslate-guard.service"),
        "unit hash matches release", "restore prior hash or remove new file"); order += 1
    add(order, SOCKET_UNIT_PATH, "publish_systemd_socket_unit", "root", "root", "0644", str(release_root / "systemd/subtranslate-guard.socket"),
        "unit hash matches release", "restore prior hash or remove new file"); order += 1
    add(order, MOUNT_UNIT_PATH, "publish_b4_mount_unit", "root", "root", "0644", str(release_root / ("systemd/" + MOUNT_UNIT_NAME)),
        "mount source hash and literal What/Where/options match", "restore prior hash or remove new file"); order += 1
    add(order, B4_BACKING, "populate_private_b4_backing", GUARD_USER, GUARD_GROUP, "0700", str(B4_CANONICAL),
        "pre-hash canonical source; copy byte-preserving; fsync; post-hash and membership validation; canonical remains untouched",
        "remove only created_this_run backing content; never remove or restore canonical B4"); order += 1
    add(order, SUDOERS_PATH, "publish_human_arm_policy", "root", "root", "0440", str(release_root / "sudoers/subtranslate-guard-arm"),
        "visudo syntax and fixed command validate", "restore prior hash or remove new file"); order += 1
    add(order, STRUCTURED_TOOL_PATH, "publish_untrusted_fixed_client", "root", "root", "0644", str(release_root / "opencode/subtranslate_recovery_apply_once.ts"),
        "source mapping remains pending physical OpenCode behavior test", "restore prior hash or remove new file"); order += 1
    add(order, Path("/etc/group"), "ensure_client_group_membership", "root", "root", "system", "explicit human-approved membership policy",
        "palhacinho is a member only of subtranslate-guard-client; no guard/admin membership", "remove only membership added_this_run"); order += 1
    return actions


def build_mediation_plan(source_commit: str, *, release_root: Path | None = None,
                         prestate_provider: Callable[[Path], dict[str, Any]] = _prestate,
                         release_stat_provider=os.lstat,
                         release_file_stat_provider=os.lstat,
                         _test_token=None) -> dict[str, Any]:
    """Build a deterministic zero-write plan against a fixed release root.

    ``release_root`` and ``prestate_provider`` are private test seams.  The
    public CLI always derives the release root from the fixed releases path.
    """
    commit = _commit(source_commit)
    if release_root is not None and _test_token is not _TEST_RELEASE_TOKEN:
        raise MediationInstallerError("RELEASE_ROOT_POLICY_INVALID")
    root = _safe_fixed(release_root or (RELEASES_ROOT / commit))
    _validate_release_boundary(root, stat_provider=release_stat_provider)
    files = _release_paths(root, stat_provider=release_file_stat_provider)
    external_manifest_path = root / "manifests/system-external-dependencies.json"
    try:
        external_dependencies = json.loads(external_manifest_path.read_bytes())
    except Exception as exc:
        raise MediationInstallerError("SYSTEM_EXTERNAL_DEPENDENCY_SET_INVALID") from exc
    if (not isinstance(external_dependencies, dict)
            or external_dependencies.get("schema_version") != "1.0.0"
            or not isinstance(external_dependencies.get("dependencies"), list)):
        raise MediationInstallerError("SYSTEM_EXTERNAL_DEPENDENCY_SET_INVALID")
    prestate_paths = [
        INSTALL_ROOT, CURRENT_SELECTOR, ETC_ROOT, KEYS_ROOT, PRIVATE_KEY_PATH,
        PUBLIC_KEY_PATH, MANIFEST_PATH, STATE_ROOT, *[STATE_ROOT / name for name in STATE_DIRECTORIES],
        SERVICE_UNIT_PATH, SOCKET_UNIT_PATH, MOUNT_UNIT_PATH, SUDOERS_PATH,
        STRUCTURED_TOOL_PATH, SOCKET_PATH, B4_CANONICAL, B4_BACKING,
        Path("/etc/passwd"), Path("/etc/group"),
    ]
    prestate = [prestate_provider(path) for path in sorted({_safe_fixed(path) for path in prestate_paths}, key=str)]
    writeset = _future_writeset(root, commit)
    return {
        "schema_version": "1.0.0",
        "source_commit": commit,
        "release_id": commit,
        "release_root": str(root),
        "protected_release_components": files,
        "external_dependencies": external_dependencies,
        "prestate": prestate,
        "ordered_writeset": writeset,
        "rollback_policy": {
            "created_this_run_only": True,
            "broad_delete": False,
            "canonical_b4_mutation": False,
            "rearm_or_retry": False,
        },
        "failure_injection_stages": list(FAILURE_INJECTION_STAGES),
        "apply_source_status": "DEFERRED_EXPLICIT_PRIVILEGED_GATE",
        "activation_boundary": {
            "install_can_remain_unarmed": True,
            "initial_capability_state": "NOT_ISSUED",
            "service_running": False,
            "socket_active": False,
            "mount_active": False,
            "daemon_reload": False,
        },
        "capability_policy": {
            "max_claims": MAX_CLAIMS,
            "max_applies": MAX_APPLIES,
            "max_retries": MAX_RETRIES,
            "auto_rearm": AUTO_REARM_ALLOWED,
        },
        "state_directory_policy": STATE_DIRECTORY_POLICY,
        "key_policy": {
            "private_path": str(PRIVATE_KEY_PATH),
            "public_path": str(PUBLIC_KEY_PATH),
            "algorithm": "Ed25519",
            "private_encoding": "PEM PKCS8 unencrypted",
            "public_encoding": "PEM SubjectPublicKeyInfo",
            "id_algorithm": "ed25519-sha256-raw-public-key",
            "idempotence": "reuse-valid-pair; one-sided/mismatch/unsafe-fail; no-auto-rotation",
        },
        "fixed_bindings": {
            "current_selector": str(CURRENT_SELECTOR),
            "state_root": str(STATE_ROOT),
            "socket": str(SOCKET_PATH),
            "b4_canonical": str(B4_CANONICAL),
            "b4_backing": str(B4_BACKING),
            "mount_unit": MOUNT_UNIT_NAME,
            "structured_tool": str(STRUCTURED_TOOL_PATH),
            "structured_tool_request": REQUEST_LITERAL,
            "executor_argv": [str(INTERPRETER), "-I", "-B", str(root / ".opencode/tools/subtranslate_recovery_ledger_reprepare_v2.py"), "--apply"],
        },
        "current_selector_policy": {
            "path": str(CURRENT_SELECTOR),
            "target": str(root),
            "root_owned": True,
            "absolute_direct_target": True,
            "atomic_temp_symlink": True,
            "fsync_parent": True,
            "rollback": "remove only created_this_run exact selector",
        },
        "b4_mount_policy": {
            "unit": MOUNT_UNIT_NAME,
            "what": str(B4_BACKING),
            "where": str(B4_CANONICAL),
            "options": "bind,ro,nosuid,nodev,noexec",
            "activation": "separate_gate",
        },
        "bypass_policy": {
            "opencode_direct_b4_write": False,
            "palhacinho_direct_mediated_target_write": False,
            "guard_writes_only_approved_path": True,
            "direct_v2_apply_bypass_broker": False,
            "candidate_executor_is_production_authority": False,
            "protected_executor_only": True,
        },
        "structured_tool_mapping": {
            "source_ready": True,
            "physical_ready": False,
            "physical_status": "NO_NOT_INSTALLED",
            "install_path": str(STRUCTURED_TOOL_PATH),
        },
        "b4_population_policy": {
            "canonical_source": str(B4_CANONICAL),
            "private_backing": str(B4_BACKING),
            "source_is_read_only_authority": True,
            "byte_preserving_copy": True,
            "pre_hash": True,
            "post_hash": True,
            "membership_validation": True,
            "fsync_files_and_directories": True,
            "mount_activation_separate": True,
            "canonical_mutation": False,
        },
        "execution_surface_effect": "INERT_UNTIL_EXPLICIT_ACTIVATION",
    }


def _assert_apply_self_boundary() -> None:
    current = Path(__file__).resolve(strict=True)
    if current != MEDIATION_BOOTSTRAP_PATH:
        raise MediationInstallerError("MEDIATION_INSTALLER_BOOTSTRAP_UNTRUSTED")
    try:
        info = current.lstat()
    except OSError as exc:
        raise MediationInstallerError("MEDIATION_INSTALLER_BOOTSTRAP_UNTRUSTED") from exc
    if (stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode)
            or info.st_uid != 0 or info.st_gid != 0 or (stat.S_IMODE(info.st_mode) & 0o022)):
        raise MediationInstallerError("MEDIATION_INSTALLER_BOOTSTRAP_UNSAFE")
    for parent in (current.parent, *current.parent.parents):
        try:
            parent_info = parent.lstat()
        except OSError as exc:
            raise MediationInstallerError("MEDIATION_INSTALLER_BOOTSTRAP_UNSAFE") from exc
        if (stat.S_ISLNK(parent_info.st_mode) or not stat.S_ISDIR(parent_info.st_mode)
                or parent_info.st_uid != 0 or (stat.S_IMODE(parent_info.st_mode) & 0o022)):
            raise MediationInstallerError("MEDIATION_INSTALLER_BOOTSTRAP_UNSAFE")


def simulate_failure_injection(stage: str) -> dict[str, Any]:
    """Exercise conservative created-this-run rollback in a temp fixture.

    This is a deterministic proof harness, not the privileged apply path.  It
    never touches an installed root and deliberately removes only objects it
    created in this invocation.
    """
    if stage not in FAILURE_INJECTION_STAGES:
        raise MediationInstallerError("FAILURE_INJECTION_STAGE_INVALID")
    with tempfile.TemporaryDirectory(prefix="subtranslate-mediation-") as tmp:
        root = Path(tmp)
        created: list[Path] = []
        try:
            for index, name in enumerate(FAILURE_INJECTION_STAGES[:FAILURE_INJECTION_STAGES.index(stage) + 1]):
                target = root / f"{index:02d}-{name}"
                if name.endswith("directory") or name.endswith("publication"):
                    target.mkdir()
                else:
                    target.write_bytes(name.encode("ascii"))
                created.append(target)
            raise MediationInstallerError("INJECTED_FAILURE:" + stage)
        except MediationInstallerError:
            for target in reversed(created):
                if target.is_symlink() or not target.exists():
                    continue
                if target.is_dir():
                    if any(target.iterdir()):
                        raise MediationInstallerError("ROLLBACK_RESIDUE")
                    target.rmdir()
                else:
                    target.unlink()
            residue = sorted(path.name for path in root.iterdir())
            return {"stage": stage, "rollback_complete": not residue, "residue": residue}


def _future_apply(source_commit: str) -> dict[str, Any]:
    _assert_apply_self_boundary()
    if os.geteuid() != 0:
        raise MediationInstallerError("MEDIATION_APPLY_REQUIRES_ROOT")
    # The source-only repair deliberately leaves publication behind an
    # explicit privileged installation gate.  No caller can turn this into a
    # write merely by selecting --apply from the candidate checkout.
    raise MediationInstallerError("MEDIATION_APPLY_REQUIRES_EXPLICIT_INSTALL_GATE")


def _parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--source-commit")
    args, unknown = parser.parse_known_args(list(argv))
    if unknown or args.plan == args.apply or not args.source_commit:
        raise MediationInstallerError("MEDIATION_CLI_CONTRACT_INVALID")
    _commit(args.source_commit)
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if args.plan:
        plan = build_mediation_plan(args.source_commit)
        print(json.dumps(plan, sort_keys=True, separators=(",", ":")))
        return 0
    result = _future_apply(args.source_commit)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":  # pragma: no cover - future privileged entrypoint
    raise SystemExit(main())
