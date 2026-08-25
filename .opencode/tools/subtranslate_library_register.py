#!/usr/bin/env python3
"""Register the V238-translated season subtitles (E07-E12) into the app Library.

Uses the same AnimeSubtitleLibrary class the web app runs, against the real
state root mounted at /docker/subtranslate/state.  Backs up the SQLite DB
before writing, then ingests each translated .ass as a TRANSLATED record with
pipeline_version v2_3_8.  Idempotent per episode: an existing TRANSLATED
pt-BR record for the same episode is reported as SKIPPED.
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

CANDIDATE_ROOT = Path("/home/palhacinho/codex-projects/subtranslate-v238-candidate")
SRC = CANDIDATE_ROOT / "src/subtranslate"
LIBRARY_ROOT = Path("/docker/subtranslate/state/anime-subtitle-library")
MEDIA_ROOT = Path("/Tank/data/Shows")
BACKUP_PARENT = Path("/home/palhacinho/opencode-backups")
SEASON_DIR = MEDIA_ROOT / "Zombie Land Saga/Season 1"

EPISODES = [
    (79, "S01E07", "e07_v238_full_styled.ass"),
    (80, "S01E08", "e08_v238_full_styled.ass"),
    (81, "S01E09", "e09_v238_full_styled.ass"),
    (82, "S01E10", "e10_v238_full_styled.ass"),
    (83, "S01E11", "e11_v238_full_styled.ass"),
    (84, "S01E12", "e12_v238_full_styled.ass"),
]


def main() -> int:
    sys.path.insert(0, str(SRC))
    from anime_subtitle_library import AnimeSubtitleLibrary

    # Backup every sqlite file under the library root before any write.
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = BACKUP_PARENT / f"subtranslate-library-register-v240-{stamp}"
    backup_dir.mkdir(mode=0o700)
    backed_up = []
    for db in list(LIBRARY_ROOT.rglob("*.db")) + list(LIBRARY_ROOT.rglob("*.sqlite3")):
        dst = backup_dir / db.name
        shutil.copyfile(db, dst)
        backed_up.append(str(dst))
    if not backed_up:
        print(json.dumps({"status": "FAIL_STOP", "blocker": "NO_LIBRARY_DB_FOUND"}))
        return 1

    library = AnimeSubtitleLibrary(LIBRARY_ROOT, media_roots=[MEDIA_ROOT])
    results = []
    for episode_id, tag, subtitle_name in EPISODES:
        source = SEASON_DIR / f"Zombie Land Saga - {tag} - *.pt-BR.ass"
        matches = sorted(SEASON_DIR.glob(f"Zombie Land Saga - {tag} - *.pt-BR.ass"))
        if not matches:
            results.append({"episode_id": episode_id, "tag": tag, "status": "SOURCE_NOT_FOUND"})
            continue
        source = matches[0]
        existing = library.list_records(episode_id=episode_id, language="pt-BR", source_kind="TRANSLATED")
        if existing:
            results.append({"episode_id": episode_id, "tag": tag, "status": "SKIPPED_ALREADY_REGISTERED",
                            "record_id": existing[0].get("id")})
            continue
        record = library.ingest_file(
            source,
            episode_id=episode_id,
            language="pt-BR",
            source_kind="TRANSLATED",
            pipeline_version="v2_3_8",
            model="qwen3.5:9b",
            created_by="v2.4.0-toolchain-registration",
            notes="Traduzido via toolchain V238 (E07-E12); legenda deployada no Jellyfin",
        )
        results.append({"episode_id": episode_id, "tag": tag, "status": "REGISTERED",
                        "record_id": record.get("id"), "sha256": record.get("sha256")})

    summary = {"status": "PASS", "mode": "LIBRARY_REGISTER_V240", "backup_root": str(backup_dir),
               "backed_up_dbs": backed_up, "results": results}
    print(json.dumps(summary, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
