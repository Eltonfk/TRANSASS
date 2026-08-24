#!/usr/bin/env python3
"""AUTO-03D translation assembly inventory (strictly read-only).

Phase D1 of the episode-79 subtitle assembly: scans EVERY runtime-evidence
family, extracts every verifiable per-unit translation candidate and produces
a deterministic inventory plus a coverage report against the source event
universe.

For each ``calls/<attempt>/response.body`` found:

  * parses the current durable envelope (``message.content`` holding a JSON
    ``translations`` array); falls back to parsing the raw body as the
    translations array; otherwise records an UNPARSABLE entry and continues;
  * cross-checks extracted ids against ``request_metadata.json`` unit_ids when
    present (EXTRA/MISSING flags);
  * records provenance (family, attempt, response sha256 when available).

Unreadable directories are recorded as INACCESSIBLE — never silently skipped.
The source universe is enumerated through the pinned contract engine
(``d9dbaa8``), reusing the proven planner machinery.

The tool has no apply surface, writes nothing and never invokes a model,
transport or retry.  The actual assembler (phase D2) is scoped by this
inventory in a separate gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import stat as _stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

CANDIDATE_ROOT = Path("/home/palhacinho/codex-projects/subtranslate-v238-candidate")
AUTHORITY_ROOT = Path("/home/palhacinho/codex-projects/anime-subtitle-translator-review")
SRC_ROOT = CANDIDATE_ROOT / "src/subtranslate"
RUNTIME_EVIDENCE_ROOT = AUTHORITY_ROOT / "runtime-evidence"

# Candidate durability helpers come from the worktree; the linguistic ENGINE
# is revision-pinned for the source universe enumeration.
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

ACTION_ID = "ASSEMBLY_INVENTORY"
EXECUTOR_ID = "ASSEMBLY_INVENTORY_V1"
SOURCE_SHA256 = "0283291ca1ad212c27a3519a56a0a4dd89c706fa6d055a2b987bd9470a826bc0"
SOURCE_CANDIDATES = (
    AUTHORITY_ROOT / "runtime-evidence/V238_E07_R6_TEXT_RECOVERY_FINAL/SUBTRANSLATE_V238_E07_R6_PRIMARY_20260815T002716Z/source/e07.ass",
    AUTHORITY_ROOT / "runtime-evidence/V238_E07_R5_FINAL_CANDIDATE/source/e07.ass",
    AUTHORITY_ROOT / "runtime-evidence/V238_E07_R4_RECOVERY/source/e07.ass",
)


class Blocked(RuntimeError):
    """Fail-closed inventory abort; never a transport or write condition."""


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
    raise Blocked("INVENTORY_SOURCE_NOT_FOUND")


def _extract_pinned_engine(revision: str) -> Path:
    root = Path(tempfile.mkdtemp(prefix="subtranslate-assembly-inventory-engine-"))
    try:
        listing = subprocess.run(
            ["git", "-C", str(CANDIDATE_ROOT), "ls-tree", "-r", "--name-only", revision, "--", "src/subtranslate"],
            capture_output=True, text=True, check=True,
        ).stdout.splitlines()
        for relative in listing:
            if not relative.endswith(".py"):
                continue
            content = subprocess.run(
                ["git", "-C", str(CANDIDATE_ROOT), "show", f"{revision}:{relative}"],
                capture_output=True, text=True, check=True,
            ).stdout
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content, encoding="utf-8")
    except Exception:
        shutil.rmtree(root, ignore_errors=True)
        raise
    return root


def _source_universe_ids(engine_revision: str) -> list[int]:
    engine_root = _extract_pinned_engine(engine_revision)
    try:
        engine_src = engine_root / "src/subtranslate"
        if str(engine_src) not in sys.path:
            sys.path.insert(0, str(engine_src))
        import pipeline_v2_1_3 as pipeline  # noqa: PLC0415
        from production_v2_1_3_adapter import _merged_glossary  # noqa: PLC0415

        merged = _merged_glossary(None)
        _original, events, _profile = pipeline.load_events(resolve_source(), merged)
        return sorted(event.id for event in events)
    finally:
        shutil.rmtree(engine_root, ignore_errors=True)


def parse_translations(raw: bytes) -> dict[int, str]:
    """Tolerant parser: current durable envelope first, then raw array."""
    envelope = json.loads(raw.decode("utf-8"))
    rows = None
    if isinstance(envelope, dict):
        message = envelope.get("message")
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            try:
                parsed_content = json.loads(message["content"])
            except json.JSONDecodeError:
                parsed_content = None
            if isinstance(parsed_content, dict):
                rows = parsed_content.get("translations")
            elif isinstance(parsed_content, list):
                rows = parsed_content
        if rows is None and isinstance(envelope.get("translations"), list):
            rows = envelope["translations"]
    elif isinstance(envelope, list):
        rows = envelope
    if not isinstance(rows, list):
        raise ValueError("no translations array found")
    result: dict[int, str] = {}
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("id"), int) and isinstance(row.get("text"), str):
            result[row["id"]] = row["text"]
    if not result:
        raise ValueError("empty translations array")
    return result


def scan_attempt(attempt_dir: Path, family: str, attempt_id: str) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "family": family,
        "attempt_id": attempt_id,
        "status": "NO_RESPONSE_EVIDENCE",
        "translations": {},
        "request_unit_match": None,
        "response_sha256": None,
    }
    response_path = attempt_dir / "response.body"
    if not response_path.is_file():
        return entry
    raw = response_path.read_bytes()
    entry["response_sha256"] = sha256_bytes(raw)
    try:
        entry["translations"] = parse_translations(raw)
        entry["status"] = "PARSED"
    except Exception as exc:
        entry["status"] = f"UNPARSABLE:{type(exc).__name__}"
        return entry
    metadata_path = attempt_dir / "request_metadata.json"
    if metadata_path.is_file():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            unit_ids = metadata.get("unit_ids")
            if isinstance(unit_ids, list):
                extracted = set(entry["translations"])
                expected = {u for u in unit_ids if isinstance(u, int)}
                entry["request_unit_match"] = {
                    "expected_count": len(expected),
                    "missing": sorted(expected - extracted),
                    "extra": sorted(extracted - expected),
                }
        except Exception:
            entry["request_unit_match"] = {"error": "metadata_unparsable"}
    return entry


def scan_families(root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    entries: list[dict[str, Any]] = []
    inaccessible: list[str] = []
    if not root.is_dir():
        raise Blocked("INVENTORY_RUNTIME_ROOT_MISSING")
    for family in sorted(p for p in root.iterdir() if p.is_dir()):
        calls_dir = family / "calls"
        try:
            attempts = sorted(p for p in calls_dir.iterdir() if p.is_dir()) if calls_dir.is_dir() else []
        except PermissionError:
            inaccessible.append(str(calls_dir))
            continue
        if not attempts:
            continue
        for attempt_dir in attempts:
            try:
                entries.append(scan_attempt(attempt_dir, family.name, attempt_dir.name))
            except PermissionError:
                inaccessible.append(str(attempt_dir))
    return entries, inaccessible


def build_inventory(universe_ids: list[int], entries: list[dict[str, Any]], inaccessible: list[str]) -> dict[str, Any]:
    translations: dict[str, list[dict[str, Any]]] = {}
    unparsable: list[dict[str, str]] = []
    for entry in entries:
        if entry["status"] == "PARSED":
            for unit_id in sorted(entry["translations"]):
                translations.setdefault(str(unit_id), []).append({
                    "family": entry["family"],
                    "attempt_id": entry["attempt_id"],
                    "text": entry["translations"][unit_id],
                    "response_sha256": entry["response_sha256"],
                    "request_unit_match": entry["request_unit_match"],
                })
        elif entry["status"].startswith("UNPARSABLE") or entry["status"] == "NO_RESPONSE_EVIDENCE":
            if entry["status"] != "NO_RESPONSE_EVIDENCE":
                unparsable.append({"family": entry["family"], "attempt_id": entry["attempt_id"], "status": entry["status"]})
    for candidates in translations.values():
        candidates.sort(key=lambda item: (item["family"], item["attempt_id"]))
    translated_ids = sorted(int(uid) for uid in translations)
    untranslated = sorted(set(universe_ids) - set(translated_ids))
    outside_universe = sorted(set(translated_ids) - set(universe_ids))
    conflicts = {
        uid: len({candidate["text"] for candidate in candidates})
        for uid, candidates in translations.items()
        if len({candidate["text"] for candidate in candidates}) > 1
    }
    return {
        "universe_total": len(universe_ids),
        "translated_distinct": len(translated_ids),
        "coverage_pct": round(100.0 * len(translated_ids) / len(universe_ids), 2) if universe_ids else 0.0,
        "untranslated_ids": untranslated,
        "outside_universe_ids": outside_universe,
        "conflicting_units": {uid: count for uid, count in sorted(conflicts.items(), key=lambda kv: int(kv[0]))},
        "unparsable_attempts": unparsable,
        "inaccessible_paths": sorted(inaccessible),
        "translations": dict(sorted(translations.items(), key=lambda kv: int(kv[0]))),
    }


def plan() -> dict[str, Any]:
    resolve_source()
    state_summary = json.loads((AUTHORITY_ROOT / "PROJECT_STATE.json").read_text(encoding="utf-8"))
    revision = ""
    for key in ("auto03d_b7_post_execution_reconciliation_r1", "auto03d_b6_post_execution_reconciliation_r1"):
        record = state_summary.get(key) or {}
        validation = record.get("planner_validation") or {}
        revision = str(validation.get("engine_revision") or "")
        if revision:
            break
    if not revision:
        revision = "d9dbaa8264992903c1c008461c5ae3ab4cc4fc84"  # family execution contract
    universe_ids = _source_universe_ids(revision)
    entries, inaccessible = scan_families(RUNTIME_EVIDENCE_ROOT)
    inventory = build_inventory(universe_ids, entries, inaccessible)
    return {
        "status": "READY",
        "mode": "INVENTORY_READ_ONLY",
        "action_id": ACTION_ID,
        "executor_id": EXECUTOR_ID,
        "side_effects_performed": False,
        "engine_revision": revision,
        "families_scanned": len({entry["family"] for entry in entries}),
        "attempts_scanned": len(entries),
        "inventory": inventory,
        "model_call": False,
        "transport": False,
        "runtime_write": False,
        "next_gate": "ASSEMBLY_D2_SCOPING_REQUIRED",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", action="store_true", required=True)
    args = parser.parse_args(argv)
    try:
        result = plan()
        print(json.dumps(result, sort_keys=True, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "FAIL_STOP", "action_id": ACTION_ID,
                          "side_effects_performed": False,
                          "blocker": f"{type(exc).__name__}:{exc}"}, sort_keys=True, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
