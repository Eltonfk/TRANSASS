"""Uninstalled service entrypoint with fixed socket activation semantics."""
from __future__ import annotations

from typing import Callable, Any

from .service import UnixBrokerService, activated_listener, ServiceError


def main(
    argv: list[str] | None = None,
    *,
    service_factory: Callable[[], UnixBrokerService] | None = None,
    listener=None,
    max_accepts: int | None = None,
) -> int:
    args = [] if argv is None else list(argv)
    if args:
        raise ServiceError("SERVICE_ACCEPTS_NO_ARGUMENTS")
    if service_factory is None:
        raise ServiceError("SERVICE_INSTALLATION_CONFIGURATION_REQUIRED")
    active_listener = listener if listener is not None else activated_listener()
    service = service_factory()
    accepted = 0
    try:
        while max_accepts is None or accepted < max_accepts:
            service.serve_once(active_listener)
            accepted += 1
    finally:
        if listener is None:
            active_listener.close()
    return 0


if __name__ == "__main__":  # pragma: no cover - installation-only entrypoint
    raise SystemExit(main(__import__("sys").argv[1:]))
