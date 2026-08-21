"""Root-only, zero-argument capability issuer entrypoint."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _bootstrap() -> None:
    entry = Path(__file__)
    path = entry.resolve(strict=True)
    root = path.parents[4]
    src = (root / "src").resolve(strict=True)
    src.relative_to(root)
    info = root.lstat()
    if entry.is_symlink() or root.is_symlink() or src.is_symlink() or not src.is_dir() or info.st_uid != 0 or (info.st_mode & 0o022):
        raise RuntimeError("PROTECTED_RELEASE_ROOT_UNSAFE")
    sys.path.insert(0, str(src))


def main(argv: list[str] | None = None) -> int:
    _bootstrap()
    from subtranslate.recovery_guard.production.issuer_cli import main as issuer_cli_main
    from subtranslate.recovery_guard.production.provider import PhysicalBindingProvider
    from subtranslate.recovery_guard.production.state import open_installed_production_state

    args = sys.argv[1:] if argv is None else list(argv)
    return issuer_cli_main(
        args,
        geteuid=os.geteuid,
        # The CLI's legacy injection point is ignored operationally: this
        # closure always opens the fixed, validated production root.
        state_store_factory=lambda _ignored: open_installed_production_state(),
        provider_factory=lambda manifest: PhysicalBindingProvider(
            bundle_manifest_fingerprint=manifest["manifest_fingerprint"],
            public_key_id=manifest["public_key_id"],
        ).measure,
    )


if __name__ == "__main__":  # pragma: no cover - installed entrypoint
    raise SystemExit(main())
