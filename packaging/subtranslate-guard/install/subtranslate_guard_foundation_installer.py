"""Inert, hash-bound privileged foundation installer.

This module is the source-controlled boundary for the future foundation
install.  ``--plan`` is read-only.  ``--apply`` is deliberately separate and
requires a real root process; it never accepts caller-selected destinations,
identities, units, keys, or operational targets.  The foundation creates only
the protected release/state skeleton.  It does not create ``current``, enable
execution, install units or policies, create keys/capabilities, or migrate
B4.
"""
from __future__ import annotations

import argparse
import grp
import hashlib
import json
import os
import pwd
import re
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Protocol


REPOSITORY_AUTHORITY = Path("/home/palhacinho/codex-projects/subtranslate-v238-candidate")
GIT = Path("/usr/bin/git")
INSTALL_ROOT = Path("/usr/local/lib/subtranslate-guard")
RELEASES_ROOT = INSTALL_ROOT / "releases"
CURRENT_SELECTOR = INSTALL_ROOT / "current"
ETC_ROOT = Path("/etc/subtranslate-guard")
KEYS_ROOT = ETC_ROOT / "keys"
STATE_ROOT = Path("/var/lib/subtranslate-guard")
BACKUPS_ROOT = STATE_ROOT / "backups"
RECOVERY_TARGETS_ROOT = STATE_ROOT / "recovery-targets"
GUARD_USER = "subtranslate-guard"
GUARD_GROUP = "subtranslate-guard"
CLIENT_GROUP = "subtranslate-guard-client"
NOLOGIN = "/usr/sbin/nologin"
SCHEMA_VERSION = "1.0.0"
SOURCE_COMMIT_RE = re.compile(r"^[0-9a-fA-F]{40}$")
ROOT_CONTROLLED_BOOTSTRAP_ROOT = Path("/usr/local/lib/subtranslate-guard/bootstrap")
MEDIATION_MOUNT_SOURCE_PATH = (
    "packaging/subtranslate-guard/systemd/home-palhacinho-codex\\x2dprojects-anime"
    "\\x2dsubtitle\\x2dtranslator\\x2dreview-runtime\\x2devidence-V238_E07_R6C_B4_RECOVERY.mount"
)
MEDIATION_MOUNT_DESTINATION_PATH = (
    "systemd/home-palhacinho-codex\\x2dprojects-anime\\x2dsubtitle\\x2dtranslator"
    "\\x2dreview-runtime\\x2devidence-V238_E07_R6C_B4_RECOVERY.mount"
)


def git_object_oid(object_type: str, payload: bytes) -> str:
    """Return the canonical SHA-1 Git object id without invoking Git."""
    if object_type not in {"commit", "tree", "blob"}:
        raise FoundationError("GIT_OBJECT_TYPE_UNSUPPORTED")
    header = f"{object_type} {len(payload)}".encode("ascii") + b"\x00"
    return hashlib.sha1(header + payload).hexdigest()


def _oid(value: str) -> str:
    if not isinstance(value, str) or SOURCE_COMMIT_RE.fullmatch(value) is None:
        raise FoundationError("GIT_OBJECT_OID_INVALID")
    return value.lower()


def _parse_commit_tree(raw: bytes) -> str:
    """Parse one strict commit header and return its tree object id."""
    trees: list[str] = []
    header = raw.split(b"\n\n", 1)[0]
    for line in header.split(b"\n"):
        if line.startswith(b"tree "):
            value = line[5:]
            if len(value) != 40 or re.fullmatch(rb"[0-9a-fA-F]{40}", value) is None:
                raise FoundationError("SOURCE_TREE_INVALID")
            trees.append(value.decode("ascii").lower())
    if len(trees) != 1:
        raise FoundationError("SOURCE_COMMIT_TREE_HEADER_INVALID")
    return trees[0]


def _parse_tree(raw: bytes) -> dict[str, tuple[int, str]]:
    """Parse the minimal Git tree format with strict path validation."""
    entries: dict[str, tuple[int, str]] = {}
    offset = 0
    while offset < len(raw):
        mode_end = raw.find(b" ", offset)
        if mode_end < 0:
            raise FoundationError("GIT_TREE_TRUNCATED")
        mode_raw = raw[offset:mode_end]
        if mode_raw not in {b"40000", b"100644", b"100755"}:
            raise FoundationError("GIT_TREE_MODE_INVALID")
        mode = int(mode_raw, 8)
        if mode not in {0o40000, 0o100644, 0o100755}:
            raise FoundationError("GIT_TREE_MODE_UNSUPPORTED")
        name_end = raw.find(b"\x00", mode_end + 1)
        if name_end < 0:
            raise FoundationError("GIT_TREE_TRUNCATED")
        name = raw[mode_end + 1:name_end]
        if not name or b"/" in name or name in {b".", b".."}:
            raise FoundationError("GIT_TREE_NAME_INVALID")
        try:
            name_text = name.decode("utf-8", "strict")
        except UnicodeDecodeError as exc:
            raise FoundationError("GIT_TREE_NAME_INVALID") from exc
        if name_text in entries:
            raise FoundationError("GIT_TREE_DUPLICATE_ENTRY")
        oid_start = name_end + 1
        oid_end = oid_start + 20
        if oid_end > len(raw):
            raise FoundationError("GIT_TREE_TRUNCATED")
        entries[name_text] = (mode, raw[oid_start:oid_end].hex())
        offset = oid_end
    return entries

# (Git source path, release-relative destination).  This is intentionally an
# exact list: tests, .git data, the candidate's live .opencode tooling and
# runtime evidence are not release inputs.
FOUNDATION_RELEASE_ALLOWLIST = (
    ("src/subtranslate/recovery_guard/__init__.py", "src/subtranslate/recovery_guard/__init__.py"),
    ("src/subtranslate/recovery_guard/core.py", "src/subtranslate/recovery_guard/core.py"),
    ("src/subtranslate/recovery_guard/production/__init__.py", "src/subtranslate/recovery_guard/production/__init__.py"),
    ("src/subtranslate/recovery_guard/production/bindings.py", "src/subtranslate/recovery_guard/production/bindings.py"),
    ("src/subtranslate/recovery_guard/production/broker.py", "src/subtranslate/recovery_guard/production/broker.py"),
    ("src/subtranslate/recovery_guard/production/crypto.py", "src/subtranslate/recovery_guard/production/crypto.py"),
    ("src/subtranslate/recovery_guard/production/issuer.py", "src/subtranslate/recovery_guard/production/issuer.py"),
    ("src/subtranslate/recovery_guard/production/issuer_cli.py", "src/subtranslate/recovery_guard/production/issuer_cli.py"),
    ("src/subtranslate/recovery_guard/production/issuer_launcher.py", "src/subtranslate/recovery_guard/production/issuer_launcher.py"),
    ("src/subtranslate/recovery_guard/production/journal.py", "src/subtranslate/recovery_guard/production/journal.py"),
    ("src/subtranslate/recovery_guard/production/manifest.py", "src/subtranslate/recovery_guard/production/manifest.py"),
    ("src/subtranslate/recovery_guard/production/probe_engine.py", "src/subtranslate/recovery_guard/production/probe_engine.py"),
    ("src/subtranslate/recovery_guard/production/protocol.py", "src/subtranslate/recovery_guard/production/protocol.py"),
    ("src/subtranslate/recovery_guard/production/provider.py", "src/subtranslate/recovery_guard/production/provider.py"),
    ("src/subtranslate/recovery_guard/production/runner.py", "src/subtranslate/recovery_guard/production/runner.py"),
    ("src/subtranslate/recovery_guard/production/schema.py", "src/subtranslate/recovery_guard/production/schema.py"),
    ("src/subtranslate/recovery_guard/production/service.py", "src/subtranslate/recovery_guard/production/service.py"),
    ("src/subtranslate/recovery_guard/production/service_launcher.py", "src/subtranslate/recovery_guard/production/service_launcher.py"),
    ("src/subtranslate/recovery_guard/production/service_main.py", "src/subtranslate/recovery_guard/production/service_main.py"),
    ("src/subtranslate/recovery_guard/production/state.py", "src/subtranslate/recovery_guard/production/state.py"),
    ("packaging/subtranslate-guard/bundle-source/.opencode/tools/subtranslate_readonly_probe.py", ".opencode/tools/subtranslate_readonly_probe.py"),
    ("packaging/subtranslate-guard/bundle-source/.opencode/tools/subtranslate_recovery_ledger_reprepare_v2.py", ".opencode/tools/subtranslate_recovery_ledger_reprepare_v2.py"),
    ("packaging/subtranslate-guard/bundle-source/src/subtranslate/v238_per_call_durability.py", "src/subtranslate/v238_per_call_durability.py"),
    ("packaging/subtranslate-guard/systemd/subtranslate-guard.service", "systemd/subtranslate-guard.service"),
    ("packaging/subtranslate-guard/systemd/subtranslate-guard.socket", "systemd/subtranslate-guard.socket"),
    ("packaging/subtranslate-guard/systemd/home-palhacinho-codex\\x2dprojects-anime\\x2dsubtitle\\x2dtranslator\\x2dreview-runtime\\x2devidence-V238_E07_R6C_B4_RECOVERY.mount", "systemd/home-palhacinho-codex\\x2dprojects-anime\\x2dsubtitle\\x2dtranslator\\x2dreview-runtime\\x2devidence-V238_E07_R6C_B4_RECOVERY.mount"),
    ("packaging/subtranslate-guard/sudoers/subtranslate-guard-arm", "sudoers/subtranslate-guard-arm"),
    ("packaging/subtranslate-guard/opencode/subtranslate_recovery_apply_once.ts", "opencode/subtranslate_recovery_apply_once.ts"),
    ("packaging/subtranslate-guard/manifests/system-external-dependencies.json", "manifests/system-external-dependencies.json"),
    ("packaging/subtranslate-guard/manifests/interpreter.identity", "manifests/interpreter.identity"),
)
FOUNDATION_SOURCE_PATHS = frozenset(source for source, _ in FOUNDATION_RELEASE_ALLOWLIST)

# Source-controlled release contract.  The manifest entry intentionally uses
# the development bytes that will be included by the next source commit; the
# current HEAD still contains the pre-repair manifest and must therefore fail
# closed with a contract mismatch.
FOUNDATION_RELEASE_CONTRACT: dict[str, dict[str, str]] = {
    "src/subtranslate/recovery_guard/__init__.py": {"git_blob_oid": "479ba0beda70001e0510d1ef37c175859abd61f8", "sha256": "ff20180e6d9509e06c15246ae51634f06f6ed67e81a4fcc16d2c8a117678a266", "role": "guard_package"},
    "src/subtranslate/recovery_guard/core.py": {"git_blob_oid": "d2d1d6b40bf99ae4ca394f990dcaaa4976c49dfb", "sha256": "4d710c3a5ef3ede9e00e50d42c078cdb28d3e43adefcb267663dc42ffd764176", "role": "guard_core"},
    "src/subtranslate/recovery_guard/production/__init__.py": {"git_blob_oid": "4b88fef7fef5ffce86e3478fe56f2a44ac3d445a", "sha256": "01338c4763e064cf6ae51302735a5bad29b4dbf93c9a95bfa1b9ab23d07db01e", "role": "production_package"},
    "src/subtranslate/recovery_guard/production/bindings.py": {"git_blob_oid": "a88eaa63784bc7313e12f742772e070a3111dc19", "sha256": "17e5cace5fd7673cd05f846b4b7fd554e1ff6bd4f412805b02497820af0f66d8", "role": "binding_provider"},
    "src/subtranslate/recovery_guard/production/broker.py": {"git_blob_oid": "09ee59929479ca4708888fe8f226805abf5a154f", "sha256": "c6885c427fef1851762c3e6f1f4acbb134e1b603406ff9a8af800159c7725511", "role": "broker"},
    "src/subtranslate/recovery_guard/production/crypto.py": {"git_blob_oid": "710e4591cf0f57320414d43bcf303b7b2ef5e29f", "sha256": "eeac85802bc49b4607901c5bb034e4fb75702ce39b28fa18eee5b0e5e1c63c97", "role": "crypto_verifier"},
    "src/subtranslate/recovery_guard/production/issuer.py": {"git_blob_oid": "0cb769a17029a15ad4d1ff9ea6b672e82c4b2f0f", "sha256": "d890aab2c1e1958c0730c39547e9c82b6e74a5014b779bf86c5abdc21c585d18", "role": "issuer"},
    "src/subtranslate/recovery_guard/production/issuer_cli.py": {"git_blob_oid": "35c984768917d0cb4f69aa6ad82fa34eb5d0f45d", "sha256": "696f205928d247a4cd5474eb4f286b0bf98313fa0caaca47a1176dfc302c0a98", "role": "issuer_cli"},
    "src/subtranslate/recovery_guard/production/issuer_launcher.py": {"git_blob_oid": "091978b6acce0886dec804c842fc9217a3f1361f", "sha256": "36a60c8910bc16100f2ac51fa63c78ffb78af6287291be5d45633cf806b5719c", "role": "issuer_launcher"},
    "src/subtranslate/recovery_guard/production/journal.py": {"git_blob_oid": "cff8f2f6f59d39cf51b222840fbe2fc680daacf9", "sha256": "2855e8d8aadcc7c03ac0f3f38255e802b47693889d2b3f9fd54236b6b5c29254", "role": "journal"},
    "src/subtranslate/recovery_guard/production/manifest.py": {"git_blob_oid": "5038005370a6065e44e837ac40d3291ee86e2945", "sha256": "99275dac848e3b79c03523862e75d024465b6c02eac4cd8174601c97448e077a", "role": "manifest"},
    "src/subtranslate/recovery_guard/production/probe_engine.py": {"git_blob_oid": "2d64ef5116fef41e44c9ad027d15f635f80d97db", "sha256": "22de43e533d47cf700741da4eb72d5ca727305322c608bf894dea19e56fdd1bb", "role": "probe_engine"},
    "src/subtranslate/recovery_guard/production/protocol.py": {"git_blob_oid": "e87fe3115227ed4b9b9019f5dcda73e6e60f769f", "sha256": "79dc36d5ed0272cbc025472f0d4822c27aa542382cc895be631329a04f503f9b", "role": "protocol"},
    "src/subtranslate/recovery_guard/production/provider.py": {"git_blob_oid": "7c9521557025ff29af82c7196b00e8af08aa0eb1", "sha256": "973f478b5216c1160850923810e0ab821e87fe1b3fb26ae2d5d1320a8797bfa1", "role": "binding_provider"},
    "src/subtranslate/recovery_guard/production/runner.py": {"git_blob_oid": "1bc43a4807b78adee553c617a11a1a002b3467b8", "sha256": "5fef458996c4f04e0e465e87913960812ec4e24fd66ced15dbe2b6b6a037bf1a", "role": "runner"},
    "src/subtranslate/recovery_guard/production/schema.py": {"git_blob_oid": "ad1314629e6eed9283a3d9cd41396a2404afcb81", "sha256": "e1b133bdd297d6cd226bfca1bf1025138f5988baeff0947d90bac4c65881f144", "role": "capability_schema"},
    "src/subtranslate/recovery_guard/production/service.py": {"git_blob_oid": "4854e5dc477f93af724fb385375f4679c71bc96f", "sha256": "bbb8d6e1e67ccb89ba31065c77f1ce3fc06efc89f3f9986baa27ad803d6ccb57", "role": "service"},
    "src/subtranslate/recovery_guard/production/service_launcher.py": {"git_blob_oid": "4886804ffc36b4ac04d0a7fc1e9901983d425539", "sha256": "621356774d8f5d3f31bf4116c6bca9a615baf4c4ecf746d80f10db7d19c68549", "role": "service_launcher"},
    "src/subtranslate/recovery_guard/production/service_main.py": {"git_blob_oid": "2e748ae7d2b77af166a3d726ba3dd4ce2061097f", "sha256": "83cd7bb363ccac7e380c91bf9b062726e7c32b87d45b27862804e1afa09a7b87", "role": "service_entrypoint"},
    "src/subtranslate/recovery_guard/production/state.py": {"git_blob_oid": "1f7a8971a6007953a7f4f9edcd4af843faf42b57", "sha256": "09de8c30ea2de6a34e83e2fe2fc6f9884dae9afb9cd0243d959bcae2be44e47b", "role": "state"},
    "packaging/subtranslate-guard/bundle-source/.opencode/tools/subtranslate_readonly_probe.py": {"git_blob_oid": "5176f8cf4bf9352995d9f5f1fde60af6aef9bef1", "sha256": "45f37a97e67195a84033c694b867e46f599891215bad3b876334018de7d268d5", "role": "probe_entrypoint"},
    "packaging/subtranslate-guard/bundle-source/.opencode/tools/subtranslate_recovery_ledger_reprepare_v2.py": {"git_blob_oid": "afa2555b35e178ad7429c978f7bd7f3263b458e4", "sha256": "ca95eac8680897d387878f69a87b089ff60e81e598fb051fcbb97606aeb408ad", "role": "executor"},
    "packaging/subtranslate-guard/bundle-source/src/subtranslate/v238_per_call_durability.py": {"git_blob_oid": "656eb2e2b6c20bfec0ac7c5eca79e49fce131fc8", "sha256": "5caeb33f1bb21fbc90b7195b791e061bc46a7bddedb49bb15f52908b09d23585", "role": "durability"},
    "packaging/subtranslate-guard/systemd/subtranslate-guard.service": {"git_blob_oid": "a224ffd04100ffbd3d5ddcccf2debe703862ae1f", "sha256": "14c878d315a5e6b7bacc5b392f257f1a568c26597ee3723ce7b55aab1d69d006", "role": "systemd_service"},
    "packaging/subtranslate-guard/systemd/subtranslate-guard.socket": {"git_blob_oid": "77e5b396debbb6b7a5f6b8c32d6e211662cc6053", "sha256": "fd3a17bb19a9a0039f82e3984bbebf12f835c3e483bdc642bdc12b04ecc68255", "role": "systemd_socket"},
    "packaging/subtranslate-guard/systemd/home-palhacinho-codex\\x2dprojects-anime\\x2dsubtitle\\x2dtranslator\\x2dreview-runtime\\x2devidence-V238_E07_R6C_B4_RECOVERY.mount": {"git_blob_oid": "cbceb95963299c844eebbea8fdc363a1fa67896d", "sha256": "fc477c093d8bdf052e35132b50fd0a2b22c628374ca06d1f38b4bff54ed97a88", "role": "mediation_mount"},
    "packaging/subtranslate-guard/sudoers/subtranslate-guard-arm": {"git_blob_oid": "386e75710d66342515c0f11f9ca1a6b761a8a9fb", "sha256": "7685fc71d5ce5384ec9bf0abbb3ef0112f6f78f4e37ec4874a8a5a6934c0548d", "role": "sudoers_policy"},
    "packaging/subtranslate-guard/opencode/subtranslate_recovery_apply_once.ts": {"git_blob_oid": "92776a0e10b61d33021ee910a114651175a9cf21", "sha256": "ddfdb24b1047522bf750174d9e70364ab8d34494608d4f10c791a6c7dbc89d7c", "role": "structured_tool"},
    "packaging/subtranslate-guard/manifests/system-external-dependencies.json": {"git_blob_oid": "8a0cd37a8d1c05383839e51360460fc3b3a4b6a3", "sha256": "a081858971e9e9ab17e8d057bd15d7020ed2299b200003f00c30042d00ee9508", "role": "system_external_dependency_set"},
    "packaging/subtranslate-guard/manifests/interpreter.identity": {"git_blob_oid": "a01d81c2222ba729918be5bf8261ac5d1d885aae", "sha256": "251ccfcd2e674f7179aece078f9d69c47f168905552bf747c2310c2a41b75fad", "role": "interpreter"},
}


class FoundationError(RuntimeError):
    """Fail-closed installer error."""


def _validate_commit_oid(value: str) -> str:
    if not isinstance(value, str) or SOURCE_COMMIT_RE.fullmatch(value) is None:
        raise FoundationError("SOURCE_COMMIT_MUST_BE_EXACT_SHA1")
    return value.lower()


def _safe_relative(value: str) -> Path:
    path = Path(value)
    if (not value or path == Path(".") or not path.parts or path.is_absolute()
            or str(path) != value or ".." in path.parts
            or ("\\" in value and value not in {MEDIATION_MOUNT_SOURCE_PATH, MEDIATION_MOUNT_DESTINATION_PATH})):
        raise FoundationError("RELEASE_PATH_INVALID")
    return path


class ObjectReader(Protocol):
    def source_tree(self, commit: str) -> str: ...
    def blob_for(self, commit: str, source_path: str) -> tuple[str, bytes]: ...


class GitObjectReader:
    """Read only the exact Git objects named by a pinned commit."""

    def __init__(self) -> None:
        if not GIT.is_file() or not os.access(GIT, os.X_OK):
            raise FoundationError("GIT_AUTHORITY_UNAVAILABLE")
        self.git_dir, self.common_dir, self.object_dir = self._resolve_metadata()
        self.metadata = {
            "git_dir": str(self.git_dir),
            "common_dir": str(self.common_dir),
            "object_directory": str(self.object_dir),
            "object_format": "sha1",
            "replace_refs": self._replace_refs(),
        }
        if self.metadata["replace_refs"]:
            raise FoundationError("GIT_REPLACE_REFS_PRESENT")
        self._metadata_before = self._metadata_fingerprint()
        self.metadata["metadata_fingerprint_before"] = self._metadata_before

    @staticmethod
    def _env() -> dict[str, str]:
        return {
            "PATH": "/usr/bin:/bin",
            "LANG": "C",
            "LC_ALL": "C",
            "HOME": "/nonexistent",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_OPTIONAL_LOCKS": "0",
        }

    @staticmethod
    def _strict_line_file(path: Path, label: str) -> str | None:
        try:
            info = path.lstat()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise FoundationError(f"GIT_{label}_UNAVAILABLE") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise FoundationError(f"GIT_{label}_INVALID")
        raw = path.read_bytes()
        if b"\x00" in raw or not raw.endswith(b"\n"):
            raise FoundationError(f"GIT_{label}_INVALID")
        lines = raw.splitlines()
        if len(lines) != 1 or not lines[0]:
            raise FoundationError(f"GIT_{label}_INVALID")
        return lines[0].decode("utf-8", "strict")

    def _resolve_metadata(self) -> tuple[Path, Path, Path]:
        repo = REPOSITORY_AUTHORITY
        try:
            repo_info = repo.lstat()
        except OSError as exc:
            raise FoundationError("GIT_REPOSITORY_UNAVAILABLE") from exc
        if not stat.S_ISDIR(repo_info.st_mode) or stat.S_ISLNK(repo_info.st_mode):
            raise FoundationError("GIT_REPOSITORY_INVALID")
        dot_git = repo / ".git"
        try:
            info = dot_git.lstat()
        except OSError as exc:
            raise FoundationError("GIT_DOT_GIT_UNAVAILABLE") from exc
        if stat.S_ISLNK(info.st_mode):
            raise FoundationError("GIT_DOT_GIT_SYMLINK")
        if stat.S_ISDIR(info.st_mode):
            git_dir = dot_git.resolve()
        elif stat.S_ISREG(info.st_mode):
            raw = dot_git.read_bytes()
            if b"\x00" in raw or not raw.endswith(b"\n"):
                raise FoundationError("GITFILE_INVALID")
            lines = raw.splitlines()
            if len(lines) != 1 or not lines[0].startswith(b"gitdir: "):
                raise FoundationError("GITFILE_INVALID")
            try:
                target = lines[0][8:].decode("utf-8", "strict")
            except UnicodeDecodeError as exc:
                raise FoundationError("GITFILE_INVALID") from exc
            if not target or "\n" in target or "\r" in target:
                raise FoundationError("GITFILE_INVALID")
            git_dir = (dot_git.parent / target).resolve() if not os.path.isabs(target) else Path(target).resolve()
        else:
            raise FoundationError("GIT_DOT_GIT_INVALID")
        git_info = git_dir.lstat()
        if stat.S_ISLNK(git_info.st_mode) or not stat.S_ISDIR(git_info.st_mode):
            raise FoundationError("GIT_DIR_INVALID")
        commondir_value = self._strict_line_file(git_dir / "commondir", "COMMONDIR")
        if commondir_value is None:
            common_dir = git_dir
        else:
            common_dir = (git_dir / commondir_value).resolve() if not os.path.isabs(commondir_value) else Path(commondir_value).resolve()
            common_info = common_dir.lstat()
            if stat.S_ISLNK(common_info.st_mode) or not stat.S_ISDIR(common_info.st_mode):
                raise FoundationError("GIT_COMMONDIR_INVALID")
        object_dir = common_dir / "objects"
        object_info = object_dir.lstat()
        if stat.S_ISLNK(object_info.st_mode) or not stat.S_ISDIR(object_info.st_mode):
            raise FoundationError("GIT_OBJECT_DIRECTORY_INVALID")
        for alternate_name in ("alternates", "http-alternates"):
            alternate = object_dir / "info" / alternate_name
            try:
                alternate_info = alternate.lstat()
            except FileNotFoundError:
                alternate_info = None
            if alternate_info is not None and stat.S_ISLNK(alternate_info.st_mode):
                raise FoundationError("GIT_ALTERNATES_INVALID")
            if alternate_info is not None and stat.S_ISREG(alternate_info.st_mode) and alternate.read_bytes().strip():
                raise FoundationError("GIT_ALTERNATES_PRESENT")
            if alternate_info is not None and not stat.S_ISREG(alternate_info.st_mode):
                raise FoundationError("GIT_ALTERNATES_INVALID")
        config = common_dir / "config"
        try:
            config_info = config.lstat()
        except FileNotFoundError:
            config_info = None
        if config_info is not None:
            if stat.S_ISLNK(config_info.st_mode) or not stat.S_ISREG(config_info.st_mode):
                raise FoundationError("GIT_CONFIG_INVALID")
            raw_config = config.read_text(encoding="utf-8", errors="strict")
            for line in raw_config.splitlines():
                if line.strip().lower().startswith("objectformat"):
                    _, _, value = line.partition("=")
                    if value.strip().lower() != "sha1":
                        raise FoundationError("GIT_OBJECT_FORMAT_UNSUPPORTED")
        return git_dir, common_dir, object_dir

    def _replace_refs(self) -> list[str]:
        refs: list[str] = []
        seen_dirs: set[Path] = set()
        for replace_dir in (self.common_dir / "refs" / "replace", self.git_dir / "refs" / "replace"):
            if replace_dir in seen_dirs:
                continue
            seen_dirs.add(replace_dir)
            try:
                info = replace_dir.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise FoundationError("GIT_REPLACE_REFS_INVALID")
            for item in sorted(replace_dir.iterdir()):
                if item.is_symlink() or not item.is_file():
                    raise FoundationError("GIT_REPLACE_REFS_INVALID")
                refs.append(str(item.relative_to(replace_dir)))
        return sorted(refs)

    def _metadata_fingerprint(self) -> str:
        records: list[dict[str, Any]] = []
        for path in (
            REPOSITORY_AUTHORITY / ".git", self.git_dir / "commondir", self.common_dir / "config",
            self.object_dir / "info" / "alternates", self.object_dir / "info" / "http-alternates",
        ):
            try:
                info = path.lstat()
            except FileNotFoundError:
                records.append({"path": str(path), "exists": False})
                continue
            if stat.S_ISLNK(info.st_mode):
                raise FoundationError("GIT_METADATA_SYMLINK")
            payload = path.read_bytes() if stat.S_ISREG(info.st_mode) else b""
            records.append({"path": str(path), "mode": info.st_mode, "uid": info.st_uid,
                            "gid": info.st_gid, "size": info.st_size, "mtime_ns": info.st_mtime_ns,
                            "sha256": hashlib.sha256(payload).hexdigest()})
        seen_replace_dirs: set[Path] = set()
        for replace_dir in (self.common_dir / "refs" / "replace", self.git_dir / "refs" / "replace"):
            if replace_dir in seen_replace_dirs:
                continue
            seen_replace_dirs.add(replace_dir)
            try:
                names = sorted(item.name for item in replace_dir.iterdir())
            except FileNotFoundError:
                names = []
            records.append({"path": str(replace_dir), "entries": names})
        return hashlib.sha256(json.dumps(records, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def verify_metadata_stable(self) -> None:
        current = self._metadata_fingerprint()
        if current != self._metadata_before:
            raise FoundationError("GIT_METADATA_CHANGED")
        self.metadata["metadata_fingerprint_after"] = current

    def _run(self, args: Iterable[str], *, binary: bool = False) -> bytes | str:
        # The candidate worktree is a fixed transport source.  Git may reject
        # it under a privileged UID because the worktree is owned by the
        # development user; this exact, command-line-only exception permits
        # access without persisting trust in any Git config file.  It is not a
        # content authority: every object is still independently hashed and
        # checked against the release contract below.
        argv = (
            str(GIT),
            "--no-replace-objects",
            "-c",
            f"safe.directory={REPOSITORY_AUTHORITY}",
            "-C",
            str(REPOSITORY_AUTHORITY),
            *tuple(args),
        )
        try:
            result = subprocess.run(argv, cwd=str(REPOSITORY_AUTHORITY), env=self._env(), shell=False,
                                    capture_output=True, check=False, timeout=10)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise FoundationError("GIT_OBJECT_READ_FAILED") from exc
        if result.returncode != 0:
            raise FoundationError("GIT_OBJECT_NOT_AVAILABLE")
        return result.stdout if binary else result.stdout.decode("ascii", "strict").strip()

    def _ensure_commit(self, commit: str) -> None:
        commit = _validate_commit_oid(commit)
        kind = self._run(("cat-file", "-t", commit))
        if kind != "commit":
            raise FoundationError("SOURCE_OBJECT_IS_NOT_COMMIT")
        raw = self._run(("cat-file", "commit", commit), binary=True)
        if not isinstance(raw, bytes) or git_object_oid("commit", raw) != commit.lower():
            raise FoundationError("SOURCE_COMMIT_SHA1_MISMATCH")
        _parse_commit_tree(raw)

    def source_tree(self, commit: str) -> str:
        self._ensure_commit(commit)
        raw = self._run(("cat-file", "commit", commit), binary=True)
        if not isinstance(raw, bytes):
            raise FoundationError("SOURCE_COMMIT_READ_FAILED")
        tree = _parse_commit_tree(raw)
        tree_raw = self._run(("cat-file", "tree", tree), binary=True)
        if not isinstance(tree_raw, bytes) or git_object_oid("tree", tree_raw) != tree:
            raise FoundationError("SOURCE_TREE_SHA1_MISMATCH")
        _parse_tree(tree_raw)
        return tree

    def _read_verified_object(self, object_type: str, oid: str) -> bytes:
        oid = _oid(oid)
        kind = self._run(("cat-file", "-t", oid))
        if kind != object_type:
            raise FoundationError("GIT_OBJECT_TYPE_MISMATCH")
        raw = self._run(("cat-file", object_type, oid), binary=True)
        if not isinstance(raw, bytes) or git_object_oid(object_type, raw) != oid:
            raise FoundationError("GIT_OBJECT_SHA1_MISMATCH")
        return raw

    def blob_for(self, commit: str, source_path: str) -> tuple[str, bytes]:
        if source_path not in FOUNDATION_SOURCE_PATHS:
            raise FoundationError("RELEASE_PATH_NOT_ALLOWLISTED")
        path = _safe_relative(source_path)
        self._ensure_commit(commit)
        tree_oid = self.source_tree(commit)
        parts = list(path.parts)
        for index, component in enumerate(parts):
            tree_raw = self._read_verified_object("tree", tree_oid)
            entries = _parse_tree(tree_raw)
            if component not in entries:
                raise FoundationError("REQUIRED_RELEASE_BLOB_MISSING")
            mode, child_oid = entries[component]
            final = index == len(parts) - 1
            if final:
                if mode not in {0o100644, 0o100755}:
                    raise FoundationError("REQUIRED_RELEASE_BLOB_MISSING")
                data = self._read_verified_object("blob", child_oid)
                return child_oid, data
            if mode != 0o40000:
                raise FoundationError("GIT_TREE_PATH_NOT_DIRECTORY")
            tree_oid = child_oid
        raise FoundationError("REQUIRED_RELEASE_BLOB_MISSING")


@dataclass(frozen=True)
class ReleaseFile:
    source_path: str
    destination: str
    blob_oid: str
    sha256: str
    mode: int = 0o644

    def as_dict(self, release_root: Path) -> dict[str, Any]:
        return {
            "path": self.destination,
            "source_path": self.source_path,
            "git_blob_oid": self.blob_oid,
            "sha256": self.sha256,
            "destination": str(release_root / self.destination),
            "owner": "root",
            "group": "root",
            "mode": format(self.mode, "04o"),
        }


def _prestate(path: Path) -> dict[str, Any]:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {"path": str(path), "exists": False}
    except OSError as exc:
        return {"path": str(path), "exists": False, "error": type(exc).__name__}
    return {"path": str(path), "exists": True, "mode": format(stat.S_IMODE(info.st_mode), "04o"),
            "uid": info.st_uid, "gid": info.st_gid, "symlink": stat.S_ISLNK(info.st_mode)}


def _foundation_directories() -> tuple[dict[str, Any], ...]:
    return (
        {"path": str(INSTALL_ROOT), "owner": "root", "group": "root", "mode": "0755"},
        {"path": str(RELEASES_ROOT), "owner": "root", "group": "root", "mode": "0755"},
        {"path": str(ETC_ROOT), "owner": "root", "group": "root", "mode": "0755"},
        {"path": str(KEYS_ROOT), "owner": "root", "group": "root", "mode": "0700"},
        {"path": str(STATE_ROOT), "owner": GUARD_USER, "group": GUARD_GROUP, "mode": "0700"},
        {"path": str(BACKUPS_ROOT), "owner": GUARD_USER, "group": GUARD_GROUP, "mode": "0700"},
        {"path": str(RECOVERY_TARGETS_ROOT), "owner": GUARD_USER, "group": GUARD_GROUP, "mode": "0700"},
    )


def build_foundation_plan(source_commit: str, *, reader: ObjectReader | None = None) -> dict[str, Any]:
    """Build deterministic JSON from Git objects without writing anything."""
    commit = _validate_commit_oid(source_commit)
    reader = reader or GitObjectReader()
    tree = reader.source_tree(commit)
    if set(FOUNDATION_RELEASE_CONTRACT) != FOUNDATION_SOURCE_PATHS:
        raise FoundationError("RELEASE_CONTRACT_ALLOWLIST_MISMATCH")
    release_root = RELEASES_ROOT / commit
    files: list[ReleaseFile] = []
    seen_destinations: set[str] = set()
    for source_path, destination in FOUNDATION_RELEASE_ALLOWLIST:
        _safe_relative(source_path)
        dest = _safe_relative(destination)
        if destination in seen_destinations:
            raise FoundationError("RELEASE_ALLOWLIST_DUPLICATE_DESTINATION")
        seen_destinations.add(destination)
        blob_oid, data = reader.blob_for(commit, source_path)
        expected = FOUNDATION_RELEASE_CONTRACT[source_path]
        actual_sha256 = hashlib.sha256(data).hexdigest()
        if blob_oid.lower() != expected["git_blob_oid"] or actual_sha256 != expected["sha256"]:
            raise FoundationError("RELEASE_CONTRACT_MISMATCH")
        files.append(ReleaseFile(source_path, destination, expected["git_blob_oid"], expected["sha256"]))
    verify_metadata = getattr(reader, "verify_metadata_stable", None)
    if callable(verify_metadata):
        verify_metadata()
    directory_prestate = [_prestate(Path(item["path"])) for item in _foundation_directories()]
    identity_prestate = {
        "user": _identity_prestate("user", GUARD_USER),
        "group": _identity_prestate("group", GUARD_GROUP),
        "client_group": _identity_prestate("group", CLIENT_GROUP),
    }
    writeset = [
        {"order": 1, "action": "ensure_system_group", "name": GUARD_GROUP, "effect": "NONE"},
        {"order": 2, "action": "ensure_system_group", "name": CLIENT_GROUP, "effect": "NONE"},
        {"order": 3, "action": "ensure_system_user", "name": GUARD_USER, "primary_group": GUARD_GROUP, "shell": NOLOGIN, "effect": "NONE"},
        {"order": 4, "action": "create_protected_directory_skeleton", "effect": "NONE"},
        {"order": 5, "action": "write_release_blobs_to_private_temp", "release": str(release_root), "effect": "NONE"},
        {"order": 6, "action": "fsync_release_files_and_directories", "effect": "NONE"},
        {"order": 7, "action": "validate_release_hashes_and_atomic_rename", "release": str(release_root), "effect": "NONE"},
    ]
    rollback = [
        {"order": 1, "action": "remove_only_created_release_temp_or_empty_release", "broad_delete": False},
        {"order": 2, "action": "remove_only_created_empty_directories", "broad_delete": False},
        {"order": 3, "action": "remove_only_identities_created_by_this_run", "broad_delete": False},
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "source_commit_algorithm": "SHA-1",
        "source_commit_oid": commit,
        "source_tree_oid": tree,
        "repo_authority": str(REPOSITORY_AUTHORITY),
        "git_object_format": "sha1",
        "git_metadata": getattr(reader, "metadata", {"replace_refs": [], "alternates_policy": "reject"}),
        "release_id": commit,
        "release_contract": {path: dict(FOUNDATION_RELEASE_CONTRACT[path]) for path in sorted(FOUNDATION_RELEASE_CONTRACT)},
        "release_root": str(release_root),
        "release_files": [item.as_dict(release_root) for item in files],
        "identity_prestate": identity_prestate,
        "identity_policy": {"user": GUARD_USER, "group": GUARD_GROUP, "client_group": CLIENT_GROUP,
                             "shell": NOLOGIN, "palhacinho_added_to_client_group": False},
        "directory_prestate": directory_prestate,
        "directory_policy": list(_foundation_directories()),
        "ordered_writeset": writeset,
        "rollback_actions": rollback,
        "execution_surface_effect": "NONE",
        "future_activation": {"current_selector": False, "service": False, "socket": False,
                               "mount": False, "sudoers": False, "structured_tool": False,
                               "private_key": False, "final_manifest": False, "capability": False,
                               "b4_migration": False},
    }


def _identity_prestate(kind: str, name: str) -> dict[str, Any]:
    try:
        if kind == "user":
            entry = pwd.getpwnam(name)
            return {"exists": True, "name": entry.pw_name, "uid": entry.pw_uid, "gid": entry.pw_gid, "shell": entry.pw_shell}
        entry = grp.getgrnam(name)
        return {"exists": True, "name": entry.gr_name, "gid": entry.gr_gid, "members": sorted(entry.gr_mem)}
    except KeyError:
        return {"exists": False, "name": name}


def _fixed_command(path: str, *args: str) -> None:
    executable = Path(path)
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise FoundationError("FOUNDATION_IDENTITY_BINARY_UNAVAILABLE")
    result = subprocess.run((path, *args), cwd="/", env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C"},
                            shell=False, capture_output=True, check=False)
    if result.returncode != 0:
        raise FoundationError("FOUNDATION_IDENTITY_COMMAND_FAILED")


def _ensure_group(name: str) -> bool:
    try:
        entry = grp.getgrnam(name)
        if name == CLIENT_GROUP and "palhacinho" in entry.gr_mem:
            raise FoundationError("FOUNDATION_CLIENT_GROUP_POLICY")
        return False
    except KeyError:
        pass
    if name not in {GUARD_GROUP, CLIENT_GROUP}:
        raise FoundationError("FOUNDATION_GROUP_POLICY_INVALID")
    _fixed_command("/usr/sbin/groupadd", "--system", name)
    return True


def _ensure_user() -> bool:
    try:
        entry = pwd.getpwnam(GUARD_USER)
        group = grp.getgrnam(GUARD_GROUP)
        if (entry.pw_gid != group.gr_gid or entry.pw_shell != NOLOGIN or entry.pw_uid == 0
                or entry.pw_dir not in {"/nonexistent", "/"}):
            raise FoundationError("FOUNDATION_USER_COLLISION")
        return False
    except KeyError:
        pass
    _fixed_command("/usr/sbin/useradd", "--system", "--no-create-home", "--home-dir", "/nonexistent",
                   "--shell", NOLOGIN, "--gid", GUARD_GROUP, GUARD_USER)
    return True


def _uid_gid(owner: str, group: str) -> tuple[int, int]:
    if owner == "root":
        uid = 0
    else:
        uid = pwd.getpwnam(owner).pw_uid
    if group == "root":
        gid = 0
    else:
        gid = grp.getgrnam(group).gr_gid
    return uid, gid


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise FoundationError("FOUNDATION_BLOB_WRITE_FAILED")
        view = view[written:]


def _assert_release_path_confined(path: Path, root: Path) -> None:
    current = path
    while True:
        if current.is_symlink() or (current.exists() and not current.is_dir() and current != path):
            raise FoundationError("FOUNDATION_RELEASE_PATH_UNSAFE")
        if current == root:
            return
        try:
            current.relative_to(root)
        except ValueError as exc:
            raise FoundationError("FOUNDATION_RELEASE_PATH_ESCAPE") from exc
        current = current.parent


def _remove_created_empty(path: Path) -> None:
    if path.is_symlink() or not path.exists():
        return
    if path.is_dir():
        children = list(path.iterdir())
        if children:
            raise FoundationError("FOUNDATION_ROLLBACK_NONEMPTY_PATH")
        path.rmdir()
    else:
        path.unlink()


def _remove_created_tree(path: Path) -> None:
    """Remove only a path created by this run, without a broad delete."""
    if path.is_symlink() or not path.exists():
        return
    if not path.is_dir():
        path.unlink()
        return
    for child in sorted(path.iterdir(), reverse=True):
        _remove_created_tree(child)
    path.rmdir()


def _rollback_created_state(
    created_dirs: list[Path],
    created_identities: list[tuple[str, str]],
    temporary: Path | None,
    release_root: Path | None,
    created_release: bool,
    *,
    command_runner=_fixed_command,
) -> None:
    """Conservative rollback restricted to objects created by this run."""
    if temporary is not None:
        _remove_created_tree(temporary)
    if created_release and release_root is not None:
        try:
            if CURRENT_SELECTOR.is_symlink() and CURRENT_SELECTOR.resolve(strict=False) == release_root:
                raise FoundationError("FOUNDATION_ROLLBACK_RELEASE_IS_SELECTED")
        except OSError as exc:
            raise FoundationError("FOUNDATION_ROLLBACK_SELECTOR_UNREADABLE") from exc
        _remove_created_tree(release_root)
    for path in reversed(created_dirs):
        if path.is_symlink() or not path.exists():
            continue
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()
        elif path.exists():
            raise FoundationError("FOUNDATION_ROLLBACK_NONEMPTY_PATH")
    for kind, name in reversed(created_identities):
        if kind == "user":
            command_runner("/usr/sbin/userdel", name)
        elif kind == "group":
            command_runner("/usr/sbin/groupdel", name)
        else:
            raise FoundationError("FOUNDATION_ROLLBACK_IDENTITY_INVALID")


def _assert_apply_self_boundary() -> None:
    """Do not run privileged apply from a user-writable worktree."""
    try:
        current = Path(__file__).resolve()
    except OSError as exc:
        raise FoundationError("FOUNDATION_INSTALLER_PATH_UNRESOLVED") from exc
    try:
        current.relative_to(ROOT_CONTROLLED_BOOTSTRAP_ROOT)
    except ValueError as exc:
        raise FoundationError("FOUNDATION_INSTALLER_BOOTSTRAP_UNTRUSTED") from exc


def apply_foundation(source_commit: str) -> dict[str, Any]:
    """Apply only the inert foundation; never called by this phase."""
    _assert_apply_self_boundary()
    if os.geteuid() != 0:
        raise FoundationError("FOUNDATION_APPLY_REQUIRES_ROOT")
    reader = GitObjectReader()
    plan = build_foundation_plan(source_commit, reader=reader)
    created_dirs: list[Path] = []
    created_identities: list[tuple[str, str]] = []
    created_release = False
    temporary: Path | None = None
    try:
        if _ensure_group(GUARD_GROUP):
            created_identities.append(("group", GUARD_GROUP))
        if _ensure_group(CLIENT_GROUP):
            created_identities.append(("group", CLIENT_GROUP))
        if _ensure_user():
            created_identities.append(("user", GUARD_USER))
        for directory in plan["directory_policy"]:
            path = Path(directory["path"])
            if path.is_symlink():
                raise FoundationError("FOUNDATION_DIRECTORY_SYMLINK")
            if not path.exists():
                path.mkdir(mode=int(directory["mode"], 8), parents=False)
                created_dirs.append(path)
                uid, gid = _uid_gid(directory["owner"], directory["group"])
                os.chown(path, uid, gid)
                os.chmod(path, int(directory["mode"], 8))
            if not path.is_dir():
                raise FoundationError("FOUNDATION_DIRECTORY_COLLISION")
            info = path.lstat()
            uid, gid = _uid_gid(directory["owner"], directory["group"])
            if info.st_uid != uid or info.st_gid != gid or stat.S_IMODE(info.st_mode) != int(directory["mode"], 8):
                raise FoundationError("FOUNDATION_DIRECTORY_POLICY_MISMATCH")
        release_root = Path(plan["release_root"])
        if release_root.exists():
            if release_root.is_symlink() or not release_root.is_dir():
                raise FoundationError("FOUNDATION_RELEASE_COLLISION")
            _validate_existing_release(release_root, plan["release_files"])
        else:
            temporary = Path(tempfile.mkdtemp(prefix=f".{source_commit}.tmp-", dir=str(RELEASES_ROOT)))
            for item in plan["release_files"]:
                destination = temporary / item["path"]
                pending_parents = []
                parent = destination.parent
                while parent != temporary and not parent.exists():
                    pending_parents.append(parent)
                    parent = parent.parent
                for directory in reversed(pending_parents):
                    directory.mkdir(mode=0o755)
                    os.chown(directory, 0, 0)
                    os.chmod(directory, 0o755)
                _assert_release_path_confined(destination.parent, temporary)
                blob = reader.blob_for(source_commit, item["source_path"])[1]
                fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
                try:
                    _write_all(fd, blob)
                    os.fsync(fd)
                finally:
                    os.close(fd)
                os.chown(destination, 0, 0)
                os.chmod(destination, 0o644)
            os.chown(temporary, 0, 0)
            os.chmod(temporary, 0o755)
            for directory in sorted((item for item in temporary.rglob("*") if item.is_dir()), key=lambda item: len(item.parts), reverse=True):
                _fsync_directory(directory)
            _fsync_directory(temporary)
            os.replace(temporary, release_root)
            temporary = None
            created_release = True
            _fsync_directory(RELEASES_ROOT)
        return {"source_commit_oid": source_commit, "release_root": str(release_root),
                "execution_surface_effect": "NONE", "current_selector_created": False}
    except Exception:
        _rollback_created_state(created_dirs, created_identities, temporary,
                                Path(plan["release_root"]), created_release)
        raise


def _validate_existing_release(root: Path, files: list[dict[str, Any]]) -> None:
    root_info = root.lstat()
    if (root_info.st_uid != 0 or root_info.st_gid != 0
            or stat.S_IMODE(root_info.st_mode) != 0o755 or stat.S_ISLNK(root_info.st_mode)):
        raise FoundationError("FOUNDATION_DIVERGENT_RELEASE")
    for item in files:
        path = root / item["path"]
        _assert_release_path_confined(path.parent, root)
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise FoundationError("FOUNDATION_DIVERGENT_RELEASE")
        info = path.lstat()
        if info.st_uid != 0 or info.st_gid != 0 or stat.S_IMODE(info.st_mode) != 0o644:
            raise FoundationError("FOUNDATION_DIVERGENT_RELEASE")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--source-commit")
    args, unknown = parser.parse_known_args(argv)
    if unknown or args.plan == args.apply or not args.source_commit:
        raise FoundationError("FOUNDATION_CLI_CONTRACT_INVALID")
    _validate_commit_oid(args.source_commit)
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else list(argv))
    if args.plan:
        print(json.dumps(build_foundation_plan(args.source_commit), sort_keys=True, separators=(",", ":")))
        return 0
    result = apply_foundation(args.source_commit)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":  # pragma: no cover - future privileged entrypoint
    raise SystemExit(main())
