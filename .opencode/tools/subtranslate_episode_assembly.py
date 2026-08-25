#!/usr/bin/env python3
"""AUTO-03E episode subtitle assembly — parameterized by episode config JSON.

Applies the episode's durable translations back onto the original .ass source.
Only response.body files under THIS episode's family directories are scanned,
so cross-episode id collisions are impossible by construction.
"""

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

ACTION_ID = "EPISODE_ASSEMBLY"
EXECUTOR_ID = "EPISODE_ASSEMBLY_V1"


class Blocked(RuntimeError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_config(path: Path) -> dict[str, Any]:
    regular(path)
    config = json.loads(path.read_text(encoding="utf-8"))
    for key in ("source_sha256", "source_candidates", "episode_label", "family_id_template"):
        if key not in config:
            raise Blocked(f"CONFIG_MISSING_KEY:{key}")
    return config


def regular(path: Path) -> os.stat_result:
    info = path.lstat()
    if _stat.S_ISLNK(info.st_mode) or not _stat.S_ISREG(info.st_mode):
        raise Blocked(f"UNSAFE_FILE:{path}")
    return info


def resolve_source(config: dict[str, Any]) -> Path:
    for candidate in config["source_candidates"]:
        p = Path(candidate)
        try:
            info = p.lstat()
        except OSError:
            continue
        if _stat.S_ISLNK(info.st_mode) or not _stat.S_ISREG(info.st_mode):
            continue
        if sha256_bytes(p.read_bytes()) == config["source_sha256"]:
            return p
    raise Blocked("EPISODE_SOURCE_NOT_FOUND")


def family_roots(config: dict[str, Any]) -> list[Path]:
    """Family directories belonging strictly to this episode."""
    prefix = config["family_id_template"].split("{")[0]
    if not prefix:
        raise Blocked("FAMILY_PREFIX_EMPTY")
    return sorted(p for p in RUNTIME_EVIDENCE_ROOT.iterdir()
                  if p.is_dir() and p.name.startswith(prefix) and "_BATCH" in p.name)


def scan_translations(roots: list[Path]) -> dict[int, str]:
    translations: dict[int, str] = {}
    for root in roots:
        calls_dir = root / "calls"
        if not calls_dir.is_dir():
            continue
        for response_path in sorted(calls_dir.rglob("response.body")):
            if not response_path.is_file():
                continue
            try:
                envelope = json.loads(response_path.read_bytes().decode("utf-8"))
                content = (envelope.get("message") or {}).get("content") if isinstance(envelope, dict) else None
                rows: list[Any] = []
                if isinstance(content, str):
                    parsed = json.loads(content)
                    rows = parsed.get("translations", []) if isinstance(parsed, dict) else []
                elif isinstance(envelope.get("translations"), list):
                    rows = envelope["translations"]
                for row in rows:
                    if isinstance(row, dict) and isinstance(row.get("id"), int) \
                            and isinstance(row.get("text"), str) and row["text"].strip():
                        translations[row["id"]] = row["text"]
            except Exception:
                continue
    return translations


def assemble(config_path: Path, output_path: Path) -> dict[str, Any]:
    import pysubs2

    config = load_config(config_path)
    source_path = resolve_source(config)
    roots = family_roots(config)
    if not roots:
        raise Blocked("EPISODE_FAMILY_DIRS_NOT_FOUND")
    translations = scan_translations(roots)

    subs = pysubs2.load(str(source_path))
    applied = 0
    preserved = 0
    empty_source_preserved = 0
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
            if not event.text.strip():
                empty_source_preserved += 1

    output_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(str(output_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        subs.save(str(output_path))
        os.fsync(fd)
    finally:
        os.close(fd)

    return {
        "status": "READY",
        "mode": "EPISODE_ASSEMBLY",
        "action_id": ACTION_ID,
        "executor_id": EXECUTOR_ID,
        "episode_label": config.get("episode_label"),
        "side_effects_performed": True,
        "source": str(source_path),
        "output": str(output_path),
        "family_dirs_scanned": len(roots),
        "total_events": len(subs.events),
        "translated_applied": applied,
        "preserved_no_translation": preserved,
        "translations_available": len(translations),
        "note": f"empty-source events preserved by design: {empty_source_preserved}",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=str, default=str(CANDIDATE_ROOT / ".opencode/tools/episode_configs/e08_config.json"))
    parser.add_argument("--assemble", action="store_true")
    parser.add_argument("--output", type=str, required=True)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(assemble(Path(args.config), Path(args.output)), sort_keys=True, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "FAIL_STOP", "action_id": ACTION_ID,
                          "blocker": f"{type(exc).__name__}:{exc}"}, sort_keys=True, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
