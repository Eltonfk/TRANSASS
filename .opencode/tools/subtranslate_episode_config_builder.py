#!/usr/bin/env python3
"""AUTO-03E episode config builder — generates an episode config JSON from an
already-extracted source subtitle under the shared SOURCES directory.

Derives every binding field from explicit arguments plus the approved series
constants.  Refuses to overwrite an existing config.  Read-only except for the
generated config file itself.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat as _stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CANDIDATE_ROOT = Path("/home/palhacinho/codex-projects/subtranslate-v238-candidate")
AUTHORITY_ROOT = Path("/home/palhacinho/codex-projects/anime-subtitle-translator-review")
SOURCES_ROOT = AUTHORITY_ROOT / "runtime-evidence/V238_E07_R6C_E08_E12_SOURCES"
CONFIGS_DIR = CANDIDATE_ROOT / ".opencode/tools/episode_configs"
ENGINE_REVISION = "d9dbaa8264992903c1c008461c5ae3ab4cc4fc84"
LIBRARY_ROOT = "/home/palhacinho/codex-projects/anime-subtitle-translator-review/runtime-evidence/V238_E07_R5_FINAL_CANDIDATE/memory"


class BuilderBlocked(RuntimeError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def regular(path: Path) -> os.stat_result:
    info = path.lstat()
    if _stat.S_ISLNK(info.st_mode) or not _stat.S_ISREG(info.st_mode):
        raise BuilderBlocked(f"UNSAFE_FILE:{path}")
    return info


def build(episode_label: str, episode_id: int, series_id: int, source_filename: str | None) -> dict[str, Any]:
    if not episode_label.upper().startswith("E") or not episode_label[1:].isdigit():
        raise BuilderBlocked("EPISODE_LABEL_INVALID")
    filename = source_filename or f"e{episode_label[1:]}.ass"
    source_path = SOURCES_ROOT / filename
    info = regular(source_path)
    source_sha256 = sha256_bytes(source_path.read_bytes())
    nn = episode_label[1:].zfill(2)
    config = {
        "episode_label": episode_label.upper(),
        "source_sha256": source_sha256,
        "source_candidates": [str(source_path)],
        "episode_id": int(episode_id),
        "series_id": int(series_id),
        "engine_revision": ENGINE_REVISION,
        "family_id_template": f"V238_ZLS_S01{nn}_B{{batch_index}}_BATCH",
        "operation_id_template": f"SUBTRANSLATE_V238_ZLS_S01{nn}_B{{batch_index}}_{{timestamp}}",
        "plan_all_inventory_path": f"/tmp/opencode/{episode_label.lower()}_plan_inventory.json",
        "library_root": LIBRARY_ROOT,
        "episode_title": episode_label.upper(),
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "source_size_bytes": info.st_size,
    }
    return config


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--episode-label", required=True, help="e.g. E09")
    parser.add_argument("--episode-id", type=int, required=True, help="Library episode id, e.g. 81")
    parser.add_argument("--series-id", type=int, default=3)
    parser.add_argument("--source-filename", type=str, default=None, help="default: e<NN>.ass")
    args = parser.parse_args(argv)
    try:
        config = build(args.episode_label, args.episode_id, args.series_id, args.source_filename)
        out = CONFIGS_DIR / f"{config['episode_label'].lower()}_config.json"
        if out.exists():
            raise BuilderBlocked(f"CONFIG_ALREADY_EXISTS:{out}")
        fd = os.open(str(out), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            os.write(fd, json.dumps(config, indent=2, sort_keys=True).encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        print(json.dumps({"status": "PASS", "config": str(out),
                          "source_sha256": config["source_sha256"],
                          "episode_id": config["episode_id"]}, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "FAIL_STOP", "blocker": f"{type(exc).__name__}:{exc}"}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
