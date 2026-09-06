"""Platform-specific paths owned by one Transass installation."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


APP_NAME = "Transass"

_CONTAINER_MEDIA_ALIASES = {"/shows", "/app/shows", "/app/shows/"}
_CONTAINER_STATE_ALIASES = {"/app/state", "/app/state/"}


def _default_data_root() -> Path:
    explicit = os.environ.get("TRANSASS_DATA_DIR")
    if explicit:
        return Path(explicit).expanduser()
    host_state = os.environ.get("STATE_DIR")
    if host_state and not host_state.startswith("/app/"):
        return Path(host_state).expanduser().parent
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / APP_NAME
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "transass"


def _default_config_root() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / APP_NAME
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "transass"


@dataclass(frozen=True)
class DesktopPaths:
    data_root: Path
    config_root: Path
    state_root: Path | None = None
    media_root_override: Path | None = None

    @property
    def state_dir(self) -> Path:
        if self.state_root is not None:
            return Path(self.state_root).expanduser()
        configured = os.environ.get("TRANSLATOR_WEB_STATE_DIR")
        if configured and configured not in _CONTAINER_STATE_ALIASES:
            return Path(configured).expanduser()
        state_dir = os.environ.get("STATE_DIR")
        if state_dir and state_dir not in _CONTAINER_STATE_ALIASES:
            return Path(state_dir).expanduser()
        return self.data_root / "state"

    @property
    def selected_media_folder_file(self) -> Path:
        return self.config_root / "selected_media_folder.txt"

    @property
    def media_root(self) -> Path:
        """Configured media root, falling back to the app-owned directory."""
        if self.media_root_override is not None:
            return Path(self.media_root_override).expanduser()
        marker = self.selected_media_folder_file
        if marker.is_file():
            try:
                selected = Path(marker.read_text(encoding="utf-8").strip()).expanduser()
                if selected.is_dir():
                    return selected.resolve()
            except (OSError, ValueError):
                pass
        configured = os.environ.get("TRANSLATOR_BASE_LIBRARY")
        if configured and configured not in _CONTAINER_MEDIA_ALIASES:
            return Path(configured).expanduser()
        media_root = os.environ.get("MEDIA_ROOT")
        if media_root and media_root not in _CONTAINER_MEDIA_ALIASES:
            return Path(media_root).expanduser()
        return self.data_root / "media"

    @property
    def library_root(self) -> Path:
        return self.state_dir / "anime-subtitle-library"

    @property
    def failure_ledger_root(self) -> Path:
        return self.state_dir / "failure-ledger"

    @property
    def temp_root(self) -> Path:
        return self.data_root / "tmp"

    @property
    def log_root(self) -> Path:
        return self.data_root / "logs"

    @property
    def transport_config(self) -> Path:
        return self.state_dir / "transport_config.json"

    @property
    def instance_lock(self) -> Path:
        return self.config_root / "transass-desktop.lock"

    def ensure(self) -> "DesktopPaths":
        for path in (self.data_root, self.config_root, self.state_dir, self.media_root, self.library_root, self.failure_ledger_root, self.temp_root, self.log_root):
            path.mkdir(parents=True, exist_ok=True)
        return self


def default_paths() -> DesktopPaths:
    data_root = Path(os.environ.get("TRANSASS_DATA_DIR") or _default_data_root()).expanduser()
    config_root = Path(os.environ.get("TRANSASS_CONFIG_DIR") or _default_config_root()).expanduser()
    return DesktopPaths(data_root=data_root, config_root=config_root)
