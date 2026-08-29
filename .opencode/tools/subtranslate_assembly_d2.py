#!/usr/bin/env python3
"""AUTO-03D D2 subtitle assembly — applies translations to the source .ass."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat as _stat
import sys
from pathlib import Path
from typing import Any

CANDIDATE_ROOT = Path("/home/palhacinho/codex-projects/subtranslate-v238-candidate")
AUTHORITY_ROOT = Path("/home/palhacinho/codex-projects/anime-subtitle-translator-review")
RUNTIME_EVIDENCE_ROOT = AUTHORITY_ROOT / "runtime-evidence"
HISTORY_ROOT = Path("/home/palhacinho/codex-projects/anime-subtitle-translator-review-history")

ACTION_ID = "ASSEMBLY_D2"
EXECUTOR_ID = "ASSEMBLY_D2_V1"
SOURCE_SHA256 = "0283291ca1ad212c27a3519a56a0a4dd89c706fa6d055a2b987bd9470a826bc0"
SOURCE_CANDIDATES = (
    AUTHORITY_ROOT / "runtime-evidence/V238_E07_R6_TEXT_RECOVERY_FINAL/SUBTRANSLATE_V238_E07_R6_PRIMARY_20260815T002716Z/source/e07.ass",
    AUTHORITY_ROOT / "runtime-evidence/V238_E07_R5_FINAL_CANDIDATE/source/e07.ass",
    AUTHORITY_ROOT / "runtime-evidence/V238_E07_R4_RECOVERY/source/e07.ass",
)


class Blocked(RuntimeError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def resolve_source() -> Path:
    for candidate in SOURCE_CANDIDATES:
        try:
            info = candidate.lstat()
        except OSError:
            continue
        if _stat.S_ISLNK(info.st_mode) or not _stat.S_ISREG(info.st_mode):
            continue
        if sha256_bytes(candidate.read_bytes()) == SOURCE_SHA256:
            return candidate
    raise Blocked("ASSEMBLY_SOURCE_NOT_FOUND")


def scan_translations(roots: list[Path]) -> dict[int, str]:
    """Scan all families for response.body files and extract translations."""
    translations: dict[str, str] = {}
    result: dict[int, str] = {}
    for root in roots:
        if not root.is_dir():
            continue
        # Collect all response.body files recursively under this root
        for response_path in sorted(root.rglob("response.body")):
            if not response_path.is_file():
                continue
            try:
                raw = response_path.read_bytes()
                envelope = json.loads(raw.decode("utf-8"))
                content = None
                message = envelope.get("message") if isinstance(envelope, dict) else None
                if isinstance(message, dict):
                    content = message.get("content")
                rows = []
                if isinstance(content, str):
                    parsed = json.loads(content)
                    rows = parsed.get("translations", []) if isinstance(parsed, dict) else parsed if isinstance(parsed, list) else []
                elif isinstance(envelope.get("translations"), list):
                    rows = envelope["translations"]
                for row in rows:
                    if isinstance(row, dict) and isinstance(row.get("id"), int) and isinstance(row.get("text"), str) and row["text"].strip():
                        translations[row["id"]] = row["text"]
            except Exception:
                continue
    return translations


def assemble(output_path: Path) -> dict[str, Any]:
    import pysubs2

    source_path = resolve_source()
    translations = scan_translations([RUNTIME_EVIDENCE_ROOT, HISTORY_ROOT])

    subs = pysubs2.load(str(source_path))
    applied = 0
    preserved = 0
    for i, event in enumerate(subs.events):
        if i in translations:
            translated_text = translations[i]
            if translated_text.strip():
                event.text = translated_text
                applied += 1
            else:
                preserved += 1
        else:
            preserved += 1

    output_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(str(output_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        subs.save(str(output_path))
        os.fsync(fd)
    finally:
        os.close(fd)

    return {
        "status": "READY",
        "mode": "ASSEMBLY_D2",
        "action_id": ACTION_ID,
        "executor_id": EXECUTOR_ID,
        "side_effects_performed": True,
        "source": str(source_path),
        "output": str(output_path),
        "total_events": len(subs.events),
        "translated_applied": applied,
        "preserved_no_translation": preserved,
        "translations_available": len(translations),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--assemble", action="store_true")
    parser.add_argument("--output", type=str, required=True)
    args = parser.parse_args(argv)
    try:
        result = assemble(Path(args.output))
        print(json.dumps(result, sort_keys=True, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "FAIL_STOP", "action_id": ACTION_ID,
                          "blocker": f"{type(exc).__name__}:{exc}"}, sort_keys=True, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
