"""Write SHA-256 checksums for a release directory."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


root = Path(sys.argv[1] if len(sys.argv) > 1 else "dist")
files = sorted(path for path in root.rglob("*") if path.is_file() and path.name != "SHA256SUMS")
(root / "SHA256SUMS").write_text("".join(f"{digest(path)}  {path.relative_to(root).as_posix()}\n" for path in files), encoding="utf-8")
print(root / "SHA256SUMS")
