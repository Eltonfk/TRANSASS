"""Versioned, parameter-free AF_UNIX request contract."""
from __future__ import annotations

REQUEST = b"EXECUTE_CURRENT_ARMED_RECOVERY_CAPABILITY\n"
MAX_REQUEST_BYTES = len(REQUEST)


def validate_request(frame: bytes) -> None:
    if not isinstance(frame, bytes) or len(frame) > MAX_REQUEST_BYTES or frame != REQUEST:
        raise ValueError("REQUEST_REJECTED")
