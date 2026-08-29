"""Install pinned static ffmpeg/ffprobe into /usr/local/bin.

Downloads the release artifact, verifies its SHA-256 (fail-closed on any
upstream change) and extracts only the two binaries the subtitle pipeline
uses.  Replaces the Debian ffmpeg package, which drags ~450MB of linked
GPU/LLVM/voice-synthesis libraries that the extraction path never touches.
"""
from __future__ import annotations

import hashlib
import io
import os
import sys
import tarfile
from urllib.request import urlopen

URL = "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz"
EXPECTED_SHA256 = "abda8d77ce8309141f83ab8edf0596834087c52467f6badf376a6a2a4c87cf67"
WANTED = {"ffmpeg", "ffprobe"}
DEST = "/usr/local/bin"


def main() -> int:
    data = urlopen(URL, timeout=300).read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != EXPECTED_SHA256:
        print(
            f"static ffmpeg sha256 mismatch: expected {EXPECTED_SHA256}, got {digest}",
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
        os.makedirs(DEST, exist_ok=True)
        for member in picked:
            target = os.path.join(DEST, member.name.rsplit("/", 1)[-1])
            with open(target, "wb") as dst:
                dst.write(archive.extractfile(member).read())
            os.chmod(target, 0o755)
            print(f"installed {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
