"""Portable runtime configuration shared by web, CLI and Desktop modes."""

from __future__ import annotations

import os
import sys
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


APP_NAME = "Transass"


def execution_identity(transport_config: dict | None = None) -> dict[str, str]:
    """Build LIVE_CAPTURED identity, falling back safely for local runs."""
    config = transport_config or {}
    primary = config.get("primary") or {}
    fallback = config.get("fallback") or {}
    version = "unknown"
    try:
        from _version import __version__
        version = str(__version__)
    except (ImportError, AttributeError):
        pass
    prompt_schema_hash = os.environ.get("PROMPT_SCHEMA_HASH") or hashlib.sha256(
        b"transass-v2_3_8-prompt-schema-v1"
    ).hexdigest()
    material = {
        "pipeline": str(config.get("pipeline") or os.environ.get("TRANSLATOR_PIPELINE") or "v2_3_8"),
        "primary": {key: primary.get(key) for key in ("provider", "model", "base_url")},
        "fallback": {key: fallback.get(key) for key in ("provider", "model", "base_url")} if fallback else None,
        "source_language": config.get("source_language") or os.environ.get("TRANSLATOR_SOURCE_LANGUAGE") or "inglês",
    }
    configuration_hash = os.environ.get("CONFIGURATION_HASH") or hashlib.sha256(
        json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "prompt_schema_hash": prompt_schema_hash,
        "configuration_hash": configuration_hash,
        "candidate_commit": os.environ.get("CANDIDATE_COMMIT") or f"release-{version}",
        "candidate_image_id": os.environ.get("CANDIDATE_IMAGE_ID") or f"transass-{version}-local",
    }


def load_project_env(project_root: Path | None = None) -> Path | None:
    """Load a simple project ``.env`` for local runs, without overwriting env.

    Compose uses ``MEDIA_ROOT`` and ``STATE_DIR`` as host-side aliases while
    the container receives paths such as ``/shows`` and ``/app/state``.  The
    Desktop launcher reads the same file but resolves those aliases below.
    No third-party dotenv dependency is needed for the installer.
    """
    root = Path(project_root or Path(__file__).resolve().parents[2])
    env_path = root / ".env"
    if not env_path.is_file():
        return None
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key, value = key.strip(), value.strip()
        if not key or key.startswith("export "):
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)
    return env_path


def _user_data_root() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / APP_NAME
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "transass"


def default_data_root() -> Path:
    return Path(os.environ.get("TRANSASS_DATA_DIR") or _user_data_root()).expanduser()


def default_state_dir() -> Path:
    configured = os.environ.get("TRANSLATOR_WEB_STATE_DIR")
    if configured and configured not in {"/app/state", "/app/state/"}:
        return Path(configured).expanduser()
    return Path(os.environ.get("STATE_DIR") or configured or default_data_root() / "state").expanduser()


def default_media_root() -> Path:
    configured = os.environ.get("TRANSLATOR_BASE_LIBRARY")
    if configured and configured not in {"/shows", "/app/shows"}:
        return Path(configured).expanduser()
    return Path(os.environ.get("MEDIA_ROOT") or configured or default_data_root() / "media").expanduser()


def default_library_root() -> Path:
    configured = os.environ.get("ANIME_SUBTITLE_LIBRARY_ROOT")
    if configured and not (os.environ.get("STATE_DIR") and configured.startswith("/app/")):
        return Path(configured).expanduser()
    return default_state_dir() / "anime-subtitle-library"


def default_failure_ledger_root() -> Path:
    configured = os.environ.get("TRANSLATOR_FAILURE_LEDGER_ROOT")
    if configured and not (os.environ.get("STATE_DIR") and configured.startswith("/app/")):
        return Path(configured).expanduser()
    return default_state_dir() / "failure-ledger"


def default_transport_config() -> Path:
    return Path(os.environ.get("TRANSPORT_CONFIG_PATH") or default_state_dir() / "transport_config.json").expanduser()


def default_glossary_path() -> Path:
    configured = os.environ.get("TRANSLATOR_GLOSSARY_PATH")
    if configured and not (os.environ.get("STATE_DIR") and configured.startswith("/app/")):
        return Path(configured).expanduser()
    return default_state_dir() / "glossary_v1.json"


@dataclass(frozen=True)
class RuntimeConfig:
    data_root: Path
    media_root: Path
    state_dir: Path
    library_root: Path
    failure_ledger_root: Path
    transport_config: Path
    glossary_path: Path

    @classmethod
    def from_environment(cls) -> "RuntimeConfig":
        return cls(
            data_root=default_data_root(),
            media_root=default_media_root(),
            state_dir=default_state_dir(),
            library_root=default_library_root(),
            failure_ledger_root=default_failure_ledger_root(),
            transport_config=default_transport_config(),
            glossary_path=default_glossary_path(),
        )

    def ensure_mutable_directories(self) -> None:
        for path in (self.data_root, self.media_root, self.state_dir, self.library_root, self.failure_ledger_root):
            path.mkdir(parents=True, exist_ok=True)

    def apply_environment(self) -> None:
        values = {
            "TRANSLATOR_BASE_LIBRARY": str(self.media_root),
            "ANIME_LIBRARY_ROOTS": str(self.media_root),
            "TRANSLATOR_WEB_STATE_DIR": str(self.state_dir),
            "ANIME_SUBTITLE_LIBRARY_ROOT": str(self.library_root),
            "TRANSLATOR_FAILURE_LEDGER_ROOT": str(self.failure_ledger_root),
            "TRANSPORT_CONFIG_PATH": str(self.transport_config),
            "TRANSLATOR_GLOSSARY_PATH": str(self.glossary_path),
        }
        # The canonical values win over compose-only aliases that may have
        # been read from a host .env (for example ``/shows`` and
        # ``/app/state``).  In a container they resolve to the same paths;
        # locally they resolve to MEDIA_ROOT/STATE_DIR.
        os.environ.update(values)
