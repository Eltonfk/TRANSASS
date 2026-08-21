"""AF_UNIX transport with an injected socket; no service starts on import."""
from __future__ import annotations

import socket
import struct
import os
from typing import Callable

from .broker import ProductionBroker
from .protocol import REQUEST, MAX_REQUEST_BYTES, validate_request
REQUEST_TIMEOUT_SECONDS = 10.0

class ServiceError(RuntimeError): pass

def peer_uid(connection: socket.socket) -> int:
    if not hasattr(socket, "SO_PEERCRED"): raise ServiceError("SO_PEERCRED_UNAVAILABLE")
    raw = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    return struct.unpack("3i", raw)[1]

class UnixBrokerService:
    def __init__(self, broker: ProductionBroker, allowed_uid: int): self.broker, self.allowed_uid = broker, allowed_uid
    def serve_once(self, listener: socket.socket) -> None:
        connection, _ = listener.accept()
        with connection:
            connection.settimeout(REQUEST_TIMEOUT_SECONDS)
            if peer_uid(connection) != self.allowed_uid:
                connection.sendall(b'{"status":"DENY","reason":"PEER_UID"}\n'); return
            chunks = []
            while True:
                chunk = connection.recv(4096)
                if not chunk: break
                chunks.append(chunk)
                if sum(map(len, chunks)) > MAX_REQUEST_BYTES:
                    connection.sendall(b'{"status":"DENY","reason":"OVERSIZE"}\n'); return
            request = b"".join(chunks)
            try:
                validate_request(request)
            except ValueError:
                connection.sendall(b'{"status":"DENY","reason":"REQUEST"}\n'); return
            try: status = self.broker.execute_fixed_request(request); response = '{"status":"' + status + '"}\n'
            except Exception as exc: response = '{"status":"DENY","reason":"' + type(exc).__name__ + '"}\n'
            connection.sendall(response.encode())

def activated_listener(env: dict[str, str] | None = None, *, fromfd=socket.fromfd) -> socket.socket:
    values = os.environ if env is None else env
    try:
        pid = int(values.get("LISTEN_PID", "")); count = int(values.get("LISTEN_FDS", ""))
    except ValueError as exc:
        raise ServiceError("SOCKET_ACTIVATION_ENV_INVALID") from exc
    if pid != os.getpid() or count != 1: raise ServiceError("SOCKET_ACTIVATION_FD_COUNT_INVALID")
    try:
        candidate = fromfd(3, socket.AF_UNIX, socket.SOCK_STREAM)
        if candidate.family != socket.AF_UNIX or (candidate.type & 0xF) != socket.SOCK_STREAM:
            candidate.close(); raise ServiceError("SOCKET_ACTIVATION_SOCKET_INVALID")
        if candidate.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) != 1:
            candidate.close(); raise ServiceError("SOCKET_ACTIVATION_NOT_LISTENER")
        return candidate
    except ServiceError: raise
    except Exception as exc: raise ServiceError("SOCKET_ACTIVATION_FD_INVALID") from exc
