"""Conservative fallback gate shared by the web queue and child runner."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from v3_runtime import is_v3_transport_error


def safe_fallback_eligible(
    error: Exception, *, response_provider: Any, output_path: Path,
) -> bool:
    """Require proof that no primary physical call or artifact exists.

    A fresh process's zero counter does not erase persisted capture evidence.
    Missing telemetry, unreadable captures and partial output all fail closed.
    """
    if not is_v3_transport_error(error) or response_provider is None:
        return False
    metrics = getattr(response_provider, "metrics", None)
    if not isinstance(metrics, dict):
        return False
    calls = metrics.get("physical_client_calls")
    if type(calls) is not int or calls != 0:
        return False
    capture_root = getattr(response_provider, "capture_root", None)
    if capture_root is None:
        return False
    try:
        if output_path.is_symlink() or output_path.exists():
            return False
        root = Path(capture_root)
        if any(path.is_symlink() for path in (root, *root.parents)):
            return False
        # lstat distinguishes genuine absence from an unreadable directory.
        try:
            root.lstat()
        except FileNotFoundError:
            return True
        return root.is_dir() and next(root.iterdir(), None) is None
    except OSError:
        return False
