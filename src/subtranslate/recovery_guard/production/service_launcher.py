"""Concrete installed service assembly.

The launcher is intentionally boring: zero arguments, fixed release-relative
imports, fixed manifest/key/state paths, and no issuer/private-key capability.
"""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path


def _release_root() -> Path:
    entry = Path(__file__)
    path = entry.resolve(strict=True)
    root = path.parents[4]
    info = root.lstat()
    if entry.is_symlink() or root.is_symlink() or not root.is_dir() or info.st_uid != 0 or (info.st_mode & 0o022):
        raise RuntimeError("PROTECTED_RELEASE_ROOT_UNSAFE")
    return root


def _bootstrap() -> Path:
    root = _release_root()
    src_entry = root / "src"
    src = src_entry.resolve(strict=True)
    src.relative_to(root)
    if src_entry.is_symlink() or not src.is_dir():
        raise RuntimeError("PROTECTED_SOURCE_ROOT_UNSAFE")
    sys.path.insert(0, str(src))
    return root


def _read_manifest(path: Path, bundle_root: Path) -> dict:
    from subtranslate.recovery_guard.production.manifest import validate_final_manifest
    info = path.lstat()
    if path.is_symlink() or not path.is_file() or info.st_uid != 0 or stat.S_ISLNK(info.st_mode) or (info.st_mode & 0o022):
        raise RuntimeError("INSTALLED_MANIFEST_UNSAFE")
    manifest = json.loads(path.read_bytes())
    validate_final_manifest(manifest, bundle_root)
    return manifest


def _load_public_key(path: Path):
    from cryptography.hazmat.primitives import serialization
    info = path.lstat()
    if (path.is_symlink() or not path.is_file() or info.st_uid != 0 or info.st_gid != 0
            or stat.S_IMODE(info.st_mode) != 0o644):
        raise RuntimeError("INSTALLED_PUBLIC_KEY_UNSAFE")
    parent = path.parent.lstat()
    if (path.parent.is_symlink() or not path.parent.is_dir() or parent.st_uid != 0
            or parent.st_gid != 0 or stat.S_IMODE(parent.st_mode) != 0o755):
        raise RuntimeError("INSTALLED_PUBLIC_KEY_PARENT_UNSAFE")
    key = serialization.load_pem_public_key(path.read_bytes())
    return key


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    if args:
        raise RuntimeError("SERVICE_ACCEPTS_NO_ARGUMENTS")
    release = _bootstrap()
    from subtranslate.recovery_guard.production.broker import ProductionBroker
    from subtranslate.recovery_guard.production.crypto import Ed25519Verifier, public_key_id
    from subtranslate.recovery_guard.production.provider import PhysicalBindingProvider
    from subtranslate.recovery_guard.production.runner import FixedRunner
    from subtranslate.recovery_guard.production.service import UnixBrokerService
    from subtranslate.recovery_guard.production.service_main import main as service_main
    from subtranslate.recovery_guard.production.state import open_installed_production_state

    manifest = _read_manifest(Path("/etc/subtranslate-guard/manifest.json"), release)
    # The private-key directory is intentionally root-only (0700).  The
    # service receives only the public verification material through the
    # separate, root-owned 0644 path; it must never need to traverse the
    # private-key directory.
    public = _load_public_key(Path("/etc/subtranslate-guard/issuer.ed25519.pub"))
    key_id = public_key_id(public)
    if key_id != manifest.get("public_key_id"):
        raise RuntimeError("PUBLIC_KEY_ID_MISMATCH")
    peer_uid = manifest.get("socket_peer_uid")
    if not isinstance(peer_uid, int) or peer_uid < 0:
        raise RuntimeError("SOCKET_PEER_UID_POLICY_INVALID")
    store = open_installed_production_state()
    provider = PhysicalBindingProvider(bundle_manifest_fingerprint=manifest["manifest_fingerprint"], public_key_id=key_id)
    runner = FixedRunner(release, manifest)
    broker = ProductionBroker(store, Ed25519Verifier(public, key_id), provider.measure, runner.run,
                               manifest_check=lambda: _read_manifest(Path("/etc/subtranslate-guard/manifest.json"), release))
    service = UnixBrokerService(broker, peer_uid)
    return service_main([], service_factory=lambda: service)


if __name__ == "__main__":  # pragma: no cover - installed entrypoint
    raise SystemExit(main())
