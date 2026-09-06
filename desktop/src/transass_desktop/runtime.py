"""Lifecycle of the local Flask server used by the Desktop window."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from threading import Thread

from werkzeug.serving import BaseWSGIServer, make_server

from .paths import DesktopPaths, default_paths


class LocalRuntime:
    """Start the existing web application on localhost for one session."""

    def __init__(self, paths: DesktopPaths | None = None, core_root: Path | None = None) -> None:
        frozen_root = getattr(sys, "_MEIPASS", None)
        default_root = Path(frozen_root) if frozen_root else Path(__file__).resolve().parents[3]
        self.core_root = Path(core_root or os.environ.get("TRANSASS_CORE_ROOT") or default_root).resolve()
        # An injected path set is authoritative (tests, portable mode and
        # callers embedding the runtime).  Ensure it before loading a
        # repository .env, whose Compose aliases must not redirect it.
        if paths is not None:
            # Freeze the caller's roots before loading a repository .env.  A
            # Compose checkout may define /app/state or /docker/... aliases,
            # but an embedded Desktop runtime must keep its explicit roots.
            self.paths = DesktopPaths(
                paths.data_root,
                paths.config_root,
                state_root=paths.state_dir,
                media_root_override=paths.media_root,
            ).ensure()
        else:
            self.paths = None
        source_root = self.core_root / "src" / "subtranslate"
        if source_root.is_dir():
            if str(source_root) not in sys.path:
                sys.path.insert(0, str(source_root))
            from runtime_config import load_project_env  # type: ignore[import-not-found]

            load_project_env(self.core_root)
        self.paths = self.paths or default_paths().ensure()
        if source_root.is_dir():
            from state_migration import migrate_from_candidates  # type: ignore[import-not-found]

            migrate_from_candidates(self.core_root, self.paths.state_dir)
        self._server: BaseWSGIServer | None = None
        self._thread: Thread | None = None
        self._url: str | None = None

    def _configure_environment(self) -> None:
        source_root = self.core_root / "src" / "subtranslate"
        if not source_root.is_dir() and not getattr(sys, "frozen", False):
            raise RuntimeError(f"Núcleo do Transass não encontrado: {source_root}")
        if source_root.is_dir():
            source_text = str(source_root)
            if source_text not in sys.path:
                sys.path.insert(0, source_text)
        values = {
            "TRANSLATOR_BASE_LIBRARY": str(self.paths.media_root),
            "ANIME_LIBRARY_ROOTS": str(self.paths.media_root),
            "TRANSLATOR_WEB_STATE_DIR": str(self.paths.state_dir),
            "ANIME_SUBTITLE_LIBRARY_ROOT": str(self.paths.library_root),
            "TRANSLATOR_FAILURE_LEDGER_ROOT": str(self.paths.failure_ledger_root),
            "TRANSPORT_CONFIG_PATH": str(self.paths.transport_config),
            "TRANSLATOR_PIPELINE": "v2_3_8",
            "TMPDIR": str(self.paths.temp_root),
        }
        # These paths belong to the Desktop session.  Overriding stale
        # container aliases from a repository .env (``/shows``, ``/app/state``)
        # is essential when the same checkout is also used with Compose.
        os.environ.update(values)
        # The core continues to invoke the conventional names ffmpeg/ffprobe;
        # prepend the bundle's bin directory so the same command works on a
        # clean Windows or Linux machine.
        from runtime_paths import configure_binary_path  # type: ignore[import-not-found]
        configure_binary_path()

    @staticmethod
    def _port_value(port: int | str | None) -> int:
        raw = os.environ.get("TRANSASS_PORT") if port is None else port
        if raw in (None, ""):
            return 0
        try:
            value = int(raw)
        except (TypeError, ValueError) as error:
            raise ValueError("TRANSASS_PORT deve ser um número entre 0 e 65535") from error
        if not 0 <= value <= 65535:
            raise ValueError("a porta deve estar entre 0 e 65535")
        return value

    def start(self, port: int | str | None = None) -> str:
        if self._server is not None:
            raise RuntimeError("O runtime Desktop já está iniciado")
        self._configure_environment()
        from app import app  # type: ignore[import-not-found]

        selected_port = self._port_value(port)
        try:
            self._server = make_server("127.0.0.1", selected_port, app, threaded=True)
            self._thread = Thread(target=self._server.serve_forever, name="transass-web", daemon=True)
            self._thread.start()
            host, actual_port = self._server.server_address[:2]
            self._url = f"http://{host}:{actual_port}/"
            return self._url
        except Exception:
            self.stop()
            raise

    def stop(self) -> None:
        server, thread = self._server, self._thread
        self._server = None
        self._thread = None
        self._url = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None and thread.is_alive():
            thread.join(timeout=5)

    @property
    def running(self) -> bool:
        return self._server is not None

    @property
    def url(self) -> str | None:
        return self._url
