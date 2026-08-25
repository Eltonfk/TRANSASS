#!/usr/bin/env python3
"""AUTO-03E hygiene detach R1 — migrate RECONCILED batch-family evidence from
the hot authority runtime-evidence root to the history root.

Nothing is ever deleted.  Only directories whose canonical reconciliation is
PROVEN by their per-batch post_execution_reconciliation object living in
PROJECT_STATE.json are eligible.  Every move is recorded in a manifest
(origin, destination, per-file sha256) and ``--rollback`` restores everything
from that manifest.

Modes:
  --plan                       read-only eligibility + file-count report
  --apply                      perform the migration (atomic per directory,
                               full manifest, HANDOFF addendum, pointer update)
  --rollback --manifest PATH   restore every moved directory from a manifest
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat as _stat
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

AUTHORITY_ROOT = Path("/home/palhacinho/codex-projects/anime-subtitle-translator-review")
HISTORY_ROOT = Path("/home/palhacinho/codex-projects/anime-subtitle-translator-review-history")
PROJECT = AUTHORITY_ROOT / "PROJECT_STATE.json"
HANDOFF = AUTHORITY_ROOT / "HANDOFF_CHATGPT.md"
RUNTIME_ROOT = AUTHORITY_ROOT / "runtime-evidence"
HISTORY_RUNTIME = HISTORY_ROOT / "runtime-evidence"
BACKUP_PARENT = Path("/home/palhacinho/opencode-backups")

ACTION_ID = "HYGIENE_R2_DETACH"
RUNNER_ID = "HYGIENE_R2_DETACH_V1"

E07_RECON_KEY = "auto03d_b{batch_index}_post_execution_reconciliation_r1"
E07_DIR_TEMPLATE = "V238_E07_R6C_B{batch_index}_BATCH"
E07_RANGE = range(5, 233)
E08_RECON_KEY = "auto03e_e08_b{batch_index}_post_execution_reconciliation_r1"
E08_DIR_TEMPLATE = "V238_ZLS_S01E08_B{batch_index}_BATCH"
E08_RANGE = range(0, 360)

TARGET_LATEST_DECISION = (
    "E08_ASSEMBLY_COMPLETED_HYGIENE_R2_DETACH_APPLIED_NEXT_EPISODE_DECISION_REQUIRED")
TARGET_NEXT_ACTION = "E08_ASSEMBLY_COMPLETED_NEXT_EPISODE_DECISION_REQUIRED"


class HygieneBlocked(RuntimeError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def regular(path: Path) -> os.stat_result:
    info = path.lstat()
    if _stat.S_ISLNK(info.st_mode) or not _stat.S_ISREG(info.st_mode):
        raise HygieneBlocked(f"UNSAFE_FILE:{path}")
    return info


def load_json(path: Path) -> dict[str, Any]:
    regular(path)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise HygieneBlocked(f"JSON_ROOT_INVALID:{path}")
    return value


def candidate_directories(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Eligible directories, episode-agnostic: every batch family whose
    canonical reconciliation is proven by the matching
    post_execution_reconciliation object in PROJECT_STATE.json."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key, record in state.items():
        if not key.endswith("_batch_execution_authorization_r1") or not isinstance(record, dict):
            continue
        family = str(record.get("family_id") or "")
        if not family or family in seen:
            continue
        recon_key = key.replace(
            "_batch_execution_authorization_r1", "_post_execution_reconciliation_r1")
        if recon_key not in state:
            continue  # mid-cycle: never detach an unreconciled family
        seen.add(family)
        source = RUNTIME_ROOT / family
        if source.is_dir():
            out.append({"episode": str(record.get("episode_id") or "?"),
                        "name": family, "source": source})
    return out


def count_files(path: Path) -> tuple[int, int]:
    files = 0
    total_bytes = 0
    for p in path.rglob("*"):
        if p.is_file() and not p.is_symlink():
            files += 1
            total_bytes += p.stat().st_size
    return files, total_bytes


def plan_mode() -> dict[str, Any]:
    state = load_json(PROJECT)
    candidates = candidate_directories(state)
    total_files = 0
    total_bytes = 0
    details = []
    for entry in candidates:
        files, size = count_files(entry["source"])
        total_files += files
        total_bytes += size
        details.append({"name": entry["name"], "episode": entry["episode"], "files": files})
    return {
        "status": "PASS", "mode": "PLAN_READ_ONLY", "action_id": ACTION_ID,
        "side_effects_performed": False,
        "candidate_dirs": len(candidates),
        "candidate_files": total_files,
        "candidate_bytes": total_bytes,
        "authority_files_observed_before": None,
        "note": "run --mode apply to migrate these directories into the history root",
        "sample": details[:10],
    }


def fsync_dir(path: Path) -> None:
    fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.tmp-", suffix=".json", dir=str(path.parent))
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        tmp_path.write_text(serialized, encoding="utf-8")
        with path.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        fsync_dir(path.parent)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def apply_mode() -> dict[str, Any]:
    regular(PROJECT)
    regular(HANDOFF)
    before_project_bytes = PROJECT.read_bytes()
    before_handoff = HANDOFF.read_bytes()
    state = json.loads(before_project_bytes)

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = BACKUP_PARENT / f"subtranslate-hygiene-r2-detach-{stamp}"
    if backup_dir.exists():
        raise HygieneBlocked("HYGIENE_BACKUP_ALREADY_EXISTS")
    backup_dir.mkdir(mode=0o700)
    (backup_dir / "PROJECT_STATE.json.before").write_bytes(before_project_bytes)
    (backup_dir / "HANDOFF_CHATGPT.md.before").write_bytes(before_handoff)
    fsync_dir(backup_dir)

    candidates = candidate_directories(state)
    if not candidates:
        raise HygieneBlocked("NO_ELIGIBLE_CANDIDATES")

    manifest_entries: list[dict[str, Any]] = []
    moved_files = 0
    HISTORY_RUNTIME.mkdir(parents=True, exist_ok=True)

    for entry in candidates:
        source = entry["source"]
        destination = HISTORY_RUNTIME / entry["name"]
        if destination.exists():
            raise HygieneBlocked(f"HYGIENE_DESTINATION_EXISTS:{entry['name']}")
        files_manifest: dict[str, str] = {}
        for p in sorted(source.rglob("*")):
            rel = p.relative_to(source)
            if p.is_file() and not p.is_symlink():
                files_manifest[str(rel)] = sha256_bytes(p.read_bytes())
        shutil.move(str(source), str(destination))
        moved_files += len(files_manifest)
        manifest_entries.append({
            "name": entry["name"], "episode": entry["episode"],
            "from": str(source), "to": str(destination),
            "files_sha256": files_manifest,
        })
        fsync_dir(destination.parent)

    manifest_path = backup_dir / "detach-manifest.json"
    manifest_raw = json.dumps({
        "action_id": ACTION_ID, "runner_id": RUNNER_ID, "created_at_utc": datetime.now(UTC).isoformat(),
        "backup_root": str(backup_dir), "entries": manifest_entries,
        "moved_dir_count": len(manifest_entries), "moved_file_count": moved_files,
    }, sort_keys=True, ensure_ascii=False, indent=2).encode("utf-8")
    fd = os.open(str(manifest_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(fd, manifest_raw)
        os.fsync(fd)
    finally:
        os.close(fd)
    fsync_dir(backup_dir)

    after = json.loads(before_project_bytes)
    after["latest_decision"] = TARGET_LATEST_DECISION
    after["next_action"] = TARGET_NEXT_ACTION
    after["auto03e_hygiene_r2_detach_r1"] = {
        "action_id": ACTION_ID, "runner_id": RUNNER_ID, "applied_at": datetime.now(UTC).isoformat(),
        "moved_dir_count": len(manifest_entries), "moved_file_count": moved_files,
        "manifest": str(manifest_path), "history_runtime_root": str(HISTORY_RUNTIME),
        "note": "evidence preserved under history root; canonical object paths refer to original locations",
        "future_side_effects_authorized": False,
    }
    after_project = (json.dumps(after, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()

    title = "AUTO-03E-HYGIENE-R2-DETACH-R1"
    summary = (f"Migracao de higiene R2: {len(manifest_entries)} diretorios de familias de lote "
               f"(E07+E08, todos com reconciliacao canonica provada) movidos para o history root "
               f"com manifest completo; nada apagado; ponteiros atualizados.")
    addendum = (f"\n\n---\n\n## Addendum {datetime.now(UTC).date().isoformat()} — {title}\n\n"
                f"{summary}\n\nMANIFEST={manifest_path}\n"
                f"NEXT_ACTION={TARGET_NEXT_ACTION}\n").encode()
    after_handoff = before_handoff + addendum

    project_info = regular(PROJECT)
    handoff_info = regular(HANDOFF)
    published: list[tuple[Path, bytes, int]] = []

    def publish(path: Path, data: bytes, mode: int) -> None:
        fd, raw = tempfile.mkstemp(prefix=f".{path.name}.hyg-", dir=path.parent)
        tmp = Path(raw)
        try:
            os.fchmod(fd, mode)
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, path)
            fsync_dir(path.parent)
        finally:
            tmp.unlink(missing_ok=True)

    try:
        publish(PROJECT, after_project, _stat.S_IMODE(project_info.st_mode))
        published.append((PROJECT, before_project_bytes, _stat.S_IMODE(project_info.st_mode)))
        publish(HANDOFF, after_handoff, _stat.S_IMODE(handoff_info.st_mode))
        published.append((HANDOFF, before_handoff, _stat.S_IMODE(handoff_info.st_mode)))
        if json.loads(PROJECT.read_text(encoding="utf-8")) != json.loads(after_project) \
                or not HANDOFF.read_bytes().startswith(before_handoff):
            raise HygieneBlocked("POST_PUBLISH_VERIFICATION_FAILED")
    except Exception:
        for path, data, mode_bits in reversed(published):
            publish(path, data, mode_bits)
        raise

    return {
        "status": "PASS", "transition": "hygiene-r2-detach",
        "moved_dir_count": len(manifest_entries), "moved_file_count": moved_files,
        "manifest": str(manifest_path), "backup_root": str(backup_dir),
        "project_state_sha256": sha256_bytes(after_project),
        "handoff_sha256": sha256_bytes(after_handoff),
        "next_action": TARGET_NEXT_ACTION,
        "rollback": f"--mode rollback --manifest {manifest_path}",
    }


def rollback_mode(manifest_path: Path) -> dict[str, Any]:
    regular(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    restored = []
    errors = []
    for entry in reversed(manifest.get("entries", [])):
        destination = Path(entry["to"])
        source = Path(entry["from"])
        if destination.is_dir():
            shutil.move(str(destination), str(source))
            restored.append(entry["name"])
        else:
            errors.append(f"MISSING:{destination}")
    return {"status": "PASS" if not errors else "FAIL", "restored": restored, "errors": errors}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True, choices=("plan", "apply", "rollback"))
    parser.add_argument("--manifest", type=str, default=None)
    args = parser.parse_args(argv)
    try:
        if args.mode == "plan":
            print(json.dumps(plan_mode(), sort_keys=True, ensure_ascii=False))
            return 0
        if args.mode == "apply":
            print(json.dumps(apply_mode(), sort_keys=True, ensure_ascii=False))
            return 0
        if not args.manifest:
            raise HygieneBlocked("ROLLBACK_REQUIRES_MANIFEST")
        print(json.dumps(rollback_mode(Path(args.manifest)), sort_keys=True, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "FAIL_STOP", "action_id": ACTION_ID,
                          "blocker": f"{type(exc).__name__}:{exc}"}, sort_keys=True, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
