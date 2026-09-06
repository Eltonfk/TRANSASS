"""Runtime resource and executable discovery for source and frozen builds."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def resource_root() -> Path:
    """Return the read-only resource root for source or PyInstaller builds."""

    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root)
    source_root = Path(__file__).resolve().parent
    if (source_root / "templates").is_dir():
        return source_root
    installed_root = Path(sys.prefix) / "share" / "transass"
    return installed_root if (installed_root / "templates").is_dir() else source_root


def bundled_bin_dir() -> Path:
    """Return the optional directory containing bundled ffmpeg binaries."""

    override = os.environ.get("TRANSASS_BIN_DIR")
    if override:
        return Path(override).expanduser()
    return resource_root() / "bin"


def configure_binary_path() -> None:
    """Prefer bundled executables while preserving the system PATH."""

    directory = bundled_bin_dir()
    if directory.is_dir():
        current = os.environ.get("PATH", "")
        entries = [str(item) for item in current.split(os.pathsep) if item]
        if str(directory) not in entries:
            os.environ["PATH"] = os.pathsep.join([str(directory), *entries])


def resolve_binary(name: str) -> str:
    """Resolve a bundled or system executable, failing with an actionable error."""

    configure_binary_path()
    resolved = shutil.which(name)
    if resolved:
        return resolved
    raise FileNotFoundError(
        f"Executável '{name}' não encontrado. Instale-o ou inclua-o no bundle do Transass."
    )


def external_media_environment(binary: str) -> dict[str, str]:
    """Return an environment suitable for host ffmpeg/ffprobe tools.

    PyInstaller/AppImage may prepend its private shared-library directory to
    ``LD_LIBRARY_PATH``.  That is correct for the frozen Qt process but can
    make a host ``ffprobe`` load incompatible bundled libraries and exit with
    an empty JSON response.  Media tools are external dependencies here, so
    remove the frozen loader overrides only for their subprocesses.  Source
    and Windows launches retain the caller environment unchanged.
    """
    environment = dict(os.environ)
    if not sys.platform.startswith("linux") or not getattr(sys, "frozen", False):
        return environment
    resolved = shutil.which(binary)
    frozen_root = getattr(sys, "_MEIPASS", None)
    if not resolved or not frozen_root:
        return environment
    try:
        Path(resolved).resolve().relative_to(Path(frozen_root).resolve())
    except ValueError:
        # This is a host executable; let it use the system loader search path.
        environment.pop("LD_PRELOAD", None)
        environment.pop("LD_LIBRARY_PATH", None)
    except OSError:
        pass
    return environment
