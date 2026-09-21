import pytest

from transport_providers import TransportBlocked
from web_durable_provider import _read_limited_response


class Response:
    def __init__(self, chunks: list[bytes], content_length: str | None = None):
        self._chunks = chunks
        self.headers = {}
        if content_length is not None:
            self.headers["Content-Length"] = content_length

    def iter_content(self, chunk_size: int):
        assert chunk_size > 0
        yield from self._chunks


def test_response_reader_accepts_bounded_stream() -> None:
    assert _read_limited_response(Response([b"abc", b"def"]), 6) == b"abcdef"


def test_response_reader_rejects_declared_oversize() -> None:
    with pytest.raises(TransportBlocked, match="PROVIDER_RESPONSE_TOO_LARGE"):
        _read_limited_response(Response([], "7"), 6)


def test_response_reader_rejects_streamed_oversize() -> None:
    with pytest.raises(TransportBlocked, match="PROVIDER_RESPONSE_TOO_LARGE"):
        _read_limited_response(Response([b"abc", b"defg"]), 6)
