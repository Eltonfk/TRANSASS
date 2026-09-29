"""Install pinned static ffmpeg/ffprobe into /usr/local/bin.

Downloads the release artifact, verifies its SHA-256 (fail-closed on any
upstream change) and extracts only the two binaries the subtitle pipeline
uses.  Replaces the Debian ffmpeg package, which drags ~450MB of linked
GPU/LLVM/voice-synthesis libraries that the extraction path never touches.
"""
from __future__ import annotations

import argparse
import hashlib
from http.client import IncompleteRead
import io
import os
import re
import sys
import tarfile
import time
from urllib.error import URLError
from urllib.request import Request, urlopen

URL = "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz"
# johnvansickle rotates the static build; allow both recent hashes during transition
ALLOWED_SHA256 = {
    "abda8d77ce8309141f83ab8edf0596834087c52467f6badf376a6a2a4c87cf67",
    "b41c555bf42de6bc35b634d4e1ba1dabcc3aa11c91953788f7ef9d88d9de2fb3",
    "f95263f28dd53035407a89d499331e600e3b8895eae9538658d79d638771f1f1",
    "5ba5ee2de834bc23b1330f833b8e1fcfe2776fa2ccb0465fd862efe318dc640a",
    "b1c9050ba370a9ed29c805ebaf521d5637a12c0d560359b981325acfc288852b",
    "7aeb6cbdae1f151a3b8ee179ab2ed941e0279a04ceddebd9773b4b8d5ecc5815",
}
EXPECTED_SHA256 = "abda8d77ce8309141f83ab8edf0596834087c52467f6badf376a6a2a4c87cf67"
WANTED = {"ffmpeg", "ffprobe"}
DEST = "/usr/local/bin"
DOWNLOAD_ATTEMPTS = 4
DOWNLOAD_TIMEOUT_SECONDS = 300
DOWNLOAD_CHUNK_BYTES = 1024 * 1024


def _download_release(*, opener=None, sleeper=None) -> bytes:
    """Download the pinned archive with bounded HTTP Range resumption."""
    open_url = opener or urlopen
    pause = sleeper or time.sleep
    data = bytearray()
    last_error: Exception | None = None

    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        offset = len(data)
        request = Request(
            URL,
            headers={"Range": f"bytes={offset}-"} if offset else {},
        )
        try:
            with open_url(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
                status = getattr(response, "status", None)
                if status is None:
                    status = response.getcode()
                headers = response.headers

                if status == 200:
                    # The server may ignore Range; its full response is still
                    # usable, but the partial prefix must not be duplicated.
                    if offset:
                        data.clear()
                        offset = 0
                    content_length = headers.get("Content-Length")
                    expected_total = int(content_length) if content_length else None
                elif status == 206:
                    content_range = headers.get("Content-Range", "")
                    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+|\*)", content_range)
                    if not match or int(match.group(1)) != offset:
                        raise RuntimeError("STATIC_FFMPEG_RANGE_RESPONSE_INVALID")
                    expected_total = (
                        int(match.group(3))
                        if match.group(3) != "*"
                        else offset + int(headers["Content-Length"])
                        if headers.get("Content-Length")
                        else None
                    )
                else:
                    raise RuntimeError(f"STATIC_FFMPEG_HTTP_STATUS_INVALID:{status}")

                while True:
                    chunk = response.read(DOWNLOAD_CHUNK_BYTES)
                    if not chunk:
                        break
                    data.extend(chunk)

                if expected_total is not None and len(data) != expected_total:
                    if len(data) < expected_total:
                        raise IncompleteRead(b"", expected_total - len(data))
                    raise RuntimeError("STATIC_FFMPEG_RESPONSE_EXCEEDED_EXPECTED_SIZE")
                return bytes(data)
        except IncompleteRead as exc:
            data.extend(exc.partial)
            last_error = exc
        except (URLError, TimeoutError, OSError) as exc:
            last_error = exc

        if attempt < DOWNLOAD_ATTEMPTS:
            pause(min(2 ** (attempt - 1), 4))

    raise RuntimeError(
        f"STATIC_FFMPEG_DOWNLOAD_FAILED_AFTER_{DOWNLOAD_ATTEMPTS}_ATTEMPTS"
    ) from last_error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="install the pinned static ffmpeg pair")
    parser.add_argument(
        "--dest",
        default=os.environ.get("TRANSASS_FFMPEG_DEST", DEST),
        help="directory receiving ffmpeg and ffprobe (default: /usr/local/bin)",
    )
    args = parser.parse_args(argv)
    destination = os.path.abspath(args.dest)
    data = _download_release()
    digest = hashlib.sha256(data).hexdigest()
    if digest not in ALLOWED_SHA256:
        print(
            f"static ffmpeg sha256 mismatch: expected one of {sorted(ALLOWED_SHA256)}, got {digest}",
            file=sys.stderr,
        )
        return 1
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:xz") as archive:
        picked = [
            member for member in archive.getmembers()
            if member.isfile() and member.name.rsplit("/", 1)[-1] in WANTED
        ]
        if {member.name.rsplit("/", 1)[-1] for member in picked} != WANTED:
            print(
                f"unexpected archive layout: {[member.name for member in picked]}",
                file=sys.stderr,
            )
            return 1
        os.makedirs(destination, exist_ok=True)
        for member in picked:
            target = os.path.join(destination, member.name.rsplit("/", 1)[-1])
            with open(target, "wb") as dst:
                dst.write(archive.extractfile(member).read())
            os.chmod(target, 0o755)
            print(f"installed {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
