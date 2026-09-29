from http.client import IncompleteRead

from deploy.install_static_ffmpeg import _download_release


class FakeResponse:
    def __init__(self, *, status, headers, chunks=(), error=None):
        self.status = status
        self.headers = headers
        self.chunks = list(chunks)
        self.error = error

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _size):
        if self.error is not None:
            error, self.error = self.error, None
            raise error
        return self.chunks.pop(0) if self.chunks else b""


def test_static_ffmpeg_download_resumes_after_incomplete_response():
    requests = []
    responses = [
        FakeResponse(
            status=200,
            headers={"Content-Length": "8"},
            error=IncompleteRead(b"abcd", 4),
        ),
        FakeResponse(
            status=206,
            headers={"Content-Range": "bytes 4-7/8", "Content-Length": "4"},
            chunks=[b"efgh"],
        ),
    ]

    def fake_open(request, *, timeout):
        requests.append((request, timeout))
        return responses.pop(0)

    result = _download_release(opener=fake_open, sleeper=lambda _seconds: None)

    assert result == b"abcdefgh"
    assert requests[0][0].get_header("Range") is None
    assert requests[1][0].get_header("Range") == "bytes=4-"
    assert all(timeout == 300 for _request, timeout in requests)


def test_static_ffmpeg_download_fails_closed_on_wrong_range_offset():
    def fake_open(_request, *, timeout):
        return FakeResponse(
            status=206,
            headers={"Content-Range": "bytes 3-7/8", "Content-Length": "5"},
            chunks=[b"abcde"],
        )

    try:
        _download_release(opener=fake_open, sleeper=lambda _seconds: None)
    except RuntimeError as exc:
        assert str(exc) == "STATIC_FFMPEG_RANGE_RESPONSE_INVALID"
    else:
        raise AssertionError("invalid Content-Range must fail closed")
