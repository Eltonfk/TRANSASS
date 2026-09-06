"""Small, lazy-loaded wrappers around native Qt dialogs."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def choose_directory(
    parent: Any = None,
    *,
    title: str = "Escolher pasta de mídia",
    initial: Path | None = None,
) -> Path | None:
    """Return a user-selected directory, or ``None`` when cancelled."""

    from PySide6.QtWidgets import QFileDialog

    selected = QFileDialog.getExistingDirectory(
        parent,
        title,
        str(initial.expanduser()) if initial else "",
        QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks,
    )
    if not selected:
        return None
    return Path(selected).expanduser().resolve()

