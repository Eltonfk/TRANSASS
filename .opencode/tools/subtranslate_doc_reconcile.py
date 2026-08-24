#!/usr/bin/env python3
"""Fixed, atomic canonical reconciliation for the AUTO-03E infra/toolchain cycle.

No paths or document contents are accepted from the caller.  The additive
object and pointer updates are embedded below; every run derives facts from
one fresh read-only probe, validates the exact canonical prestate, backs up
both authority documents and publishes atomically with rollback on failure.
``--dry-run`` performs the full derivation and reports hashes without writing.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import stat
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CANDIDATE_ROOT = Path("/home/palhacinho/codex-projects/subtranslate-v238-candidate")
AUTHORITY_ROOT = Path("/home/palhacinho/codex-projects/anime-subtitle-translator-review")
PROJECT = AUTHORITY_ROOT / "PROJECT_STATE.json"
HANDOFF = AUTHORITY_ROOT / "HANDOFF_CHATGPT.md"
PROBE_PATH = CANDIDATE_ROOT / ".opencode/tools/subtranslate_readonly_probe.py"
BACKUP_PARENT = Path("/home/palhacinho/opencode-backups")

PRESTATE_STATE = "SUBTRANSLATE_V238_E07_R6C_COMPLETE_BATCHES_1_232_ALL_PARSED_VALID_ZERO_RETRY"
PRESTATE_NEXT = "BATCH_RANGE_112_232_COMPLETED_NEXT_RANGE_DECISION_REQUIRED"
RECONCILIATION_KEY = "auto03e_infra_toolchain_reconciliation_r1"
TARGET_LATEST_DECISION = "HUMAN_DECISION_RECORDED_REVIEW_DEFERRED_UNTIL_ALL_EPISODES_E08_E12_V238_FLOW_SELECTED"
TARGET_NEXT_ACTION = "E08_E12_V238_FLOW_PLANNING_REQUIRED"

WEB_IMAGE = "subtranslate:v2.3.8-dockerfile-rc4567-20260824T151953Z"
ROLLBACK_IMAGE = "subtranslate:p2c3-20260813T223000Z"


class ReconcileBlocked(RuntimeError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def regular(path: Path) -> os.stat_result:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ReconcileBlocked(f"UNSAFE_FILE:{path}")
    return info


def fresh_probe() -> dict[str, Any]:
    regular(PROBE_PATH)
    spec = importlib.util.spec_from_file_location("auto03e_fresh_probe", PROBE_PATH)
    if spec is None or spec.loader is None:
        raise ReconcileBlocked("PROBE_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.probe()
    if result.get("blockers") or result.get("unknowns") or not result.get("integrity", {}).get("snapshot_consistent"):
        raise ReconcileBlocked("FRESH_PROBE_NOT_CLEAN")
    return result


def build_additive_object(now: str, fingerprint: str) -> dict[str, Any]:
    return {
        "mode": "INFRA_TOOLCHAIN_RECONCILIATION",
        "recorded_at": now,
        "snapshot_fingerprint": fingerprint,
        "scope": {
            "web_deploy": {
                "image": WEB_IMAGE,
                "deployed_at": "2026-08-24T15:20Z",
                "mechanism": "docker compose --project-directory /docker/subtranslate up -d with SUBTRANSLATE_IMAGE override; no files edited",
                "config_preserved": {
                    "TRANSLATOR_PIPELINE": "v2_3_0",
                    "TRANSLATOR_OLLAMA_URL": "http://192.168.1.5:11434/api/chat",
                    "TRANSLATOR_REVIEW_MODEL": "llama3.1:8b",
                    "TRANSLATOR_FALLBACK_OLLAMA_MODEL": "llama3.1:8b",
                },
                "rollback_image": ROLLBACK_IMAGE,
                "health": "healthy; /health ok",
            },
            "candidate_toolchain_commits": [
                {"sha": "89ae63d", "summary": "deploy/Dockerfile includes V238 rc4-rc7b1 modules"},
                {"sha": "b4203f9", "summary": "probe: absent transient ledger lock is normal state"},
                {"sha": "ae298b6", "summary": "context_inspect canonical_keys in summary; probe 0.4.1 lineage"},
                {"sha": "27c565a", "summary": "cycle 5-A tests: routing whitespace-tolerant, frozen baseline d2ee2d37, shared-section engine equivalence"},
            ],
            "release_gate_e07": {
                "result": "BLOCK",
                "readiness": "NOT_READY",
                "pending": [
                    "human review deferred by human decision until all episodes E07-E12 finalized",
                    "human playback of output/e07_v238_full_styled.ass",
                    "app_safety/translator_safety suites NOT_RUN on host (flask missing)",
                ],
            },
            "pending_toolchain_projects": [
                "cycle 5-B guard contract reconciliation (probe_engine lock-fix port; foundation/mediation/bundle contracts; 5 pre-existing closure_repair failures)",
                "R1 probe_engine mirror parity",
            ],
        },
        "human_decisions": {
            "recorded_at_source": "OpenCode session 2026-08-24",
            "human_review_deferred": "human review of translations DEFERRED until ALL subtitles E07-E12 are finalized (single future gate)",
            "e08_e12_flow": "episodes E08-E12 will use the V238 flow of the updated app image",
        },
        "future_side_effects_authorized": False,
        "next_phase_planning_required": True,
        "production_write_authorized": False,
        "model_call_authorized": False,
    }


def transition(before: dict[str, Any], probe: dict[str, Any]) -> tuple[dict[str, Any], str, str]:
    if before.get("state") != PRESTATE_STATE or before.get("next_action") != PRESTATE_NEXT:
        raise ReconcileBlocked("RECONCILIATION_PRESTATE_MISMATCH")
    if RECONCILIATION_KEY in before:
        raise ReconcileBlocked("RECONCILIATION_RECORD_ALREADY_EXISTS")
    now = datetime.now(UTC).isoformat()
    after = json.loads(json.dumps(before))
    after[RECONCILIATION_KEY] = build_additive_object(now, str(probe["snapshot_fingerprint"]))
    after["latest_decision"] = TARGET_LATEST_DECISION
    after["next_action"] = TARGET_NEXT_ACTION
    title = "AUTO-03E-INFRA-TOOLCHAIN-RECONCILIATION-R1"
    summary = ("Reconciliacao aditiva: deploy web rc4567 ativo com config preservada; 4 commits de toolchain "
               "registrados; release gate E07 BLOCK documentado; decisao humana: revisao adiada ate E07-E12 "
               "finalizados e fluxo V238 selecionado para E08-E12; nenhum side effect futuro autorizado.")
    return after, title, summary


def fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_new(path: Path, data: bytes, mode: int = 0o600) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def publish(path: Path, data: bytes, mode: int) -> None:
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.auto03e-", dir=path.parent)
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


def derive() -> tuple[dict[str, Any], dict[str, Any], bytes, bytes, bytes, bytes, str, str]:
    project_info = regular(PROJECT)
    handoff_info = regular(HANDOFF)
    before_project_bytes = PROJECT.read_bytes()
    before_handoff = HANDOFF.read_bytes()
    before = json.loads(before_project_bytes)
    probe = fresh_probe()
    after, title, summary = transition(before, probe)
    after_project = (json.dumps(after, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
    addendum = (f"\n\n---\n\n## Addendum {datetime.now(UTC).date().isoformat()} — {title}\n\n"
                f"{summary}\n\nSNAPSHOT_FINGERPRINT={probe['snapshot_fingerprint']}\n"
                "FUTURE_SIDE_EFFECTS_AUTHORIZED=false\n"
                f"NEXT_ACTION={after['next_action']}\n").encode()
    after_handoff = before_handoff + addendum
    changed = sorted(set(before) ^ set(after)) + sorted(
        key for key in set(before) & set(after) if before[key] != after[key]
    )
    report = {
        "mode_reported": True,
        "state_before_sha256": digest(before_project_bytes),
        "state_candidate_sha256": digest(after_project),
        "handoff_before_sha256": digest(before_handoff),
        "handoff_candidate_sha256": digest(after_handoff),
        "changed_top_level_keys": sorted(set(changed)),
        "handoff_append_only": True,
        "snapshot_fingerprint": probe["snapshot_fingerprint"],
        "next_action": after["next_action"],
        "project_mode": oct(stat.S_IMODE(project_info.st_mode)),
        "handoff_mode": oct(stat.S_IMODE(handoff_info.st_mode)),
    }
    return report, after, after_project, before_project_bytes, before_handoff, after_handoff, title, summary


def apply_reconciliation() -> dict[str, Any]:
    report, _, after_project, before_project_bytes, before_handoff, after_handoff, title, _ = derive()
    project_info = regular(PROJECT)
    handoff_info = regular(HANDOFF)
    backup = BACKUP_PARENT / "subtranslate-auto03e-infra-toolchain-reconciliation-r1"
    if backup.exists() or backup.is_symlink():
        raise ReconcileBlocked("TRANSITION_BACKUP_ALREADY_EXISTS")
    backup.mkdir(mode=0o700)
    write_new(backup / "PROJECT_STATE.json.before", before_project_bytes)
    write_new(backup / "HANDOFF_CHATGPT.md.before", before_handoff)
    fsync_dir(backup)
    fsync_dir(BACKUP_PARENT)
    published: list[tuple[Path, bytes, int]] = []
    try:
        publish(PROJECT, after_project, stat.S_IMODE(project_info.st_mode))
        published.append((PROJECT, before_project_bytes, stat.S_IMODE(project_info.st_mode)))
        publish(HANDOFF, after_handoff, stat.S_IMODE(handoff_info.st_mode))
        published.append((HANDOFF, before_handoff, stat.S_IMODE(handoff_info.st_mode)))
        if json.loads(PROJECT.read_text(encoding="utf-8")) != json.loads(after_project) or not HANDOFF.read_bytes().startswith(before_handoff):
            raise ReconcileBlocked("POST_PUBLISH_VERIFICATION_FAILED")
    except Exception:
        for path, data, mode_bits in reversed(published):
            publish(path, data, mode_bits)
        raise
    report.update({"status": "PASS", "transition": "documental-apply",
                   "backup_root": str(backup), "files_written": [str(PROJECT), str(HANDOFF)]})
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True, choices=("dry-run", "apply"))
    args = parser.parse_args(argv)
    try:
        if args.mode == "dry-run":
            report, *_ = derive()
            report.update({"status": "PASS", "transition": "dry-run", "side_effects_performed": False})
            print(json.dumps(report, sort_keys=True, ensure_ascii=False))
            return 0
        print(json.dumps(apply_reconciliation(), sort_keys=True, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "FAIL_STOP", "transition": args.mode,
                          "blocker": f"{type(exc).__name__}:{exc}"}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
