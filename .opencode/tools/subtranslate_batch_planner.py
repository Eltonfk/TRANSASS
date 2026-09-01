#!/usr/bin/env python3
"""AUTO-03D generalized batch planning (strictly read-only).

Derives the binding facts of ANY initial batch (``--batch N``, 8 <= N <= 232)
from canonical sources without any transport, model call, reservation, retry
or file write:

  * locates the episode source object by its family-contract SHA-256;
  * rebuilds the canonical planning universe through the frozen V226 adapter
    chain (``V226MemoryRunner``), with the ENGINE PINNED to the family
    execution-contract revision ``d9dbaa8``;
  * derives request contexts from the ORIGINAL untransformed source events;
  * proves engine correctness on EVERY run against the recorded anchors:
    the full plan must reproduce all inventory membership entries and the
    executed R6C payloads of plan indices 1-4 byte-exactly;
  * captures the exact transport payload for batch N through the canonical
    ``Client.finalize_request_payload`` seam before the durable boundary.

Epistemic note: batches 8+ have no recorded per-batch anchors; their
derivation correctness rests on the engine proof above, which fails closed
on any divergence.  The chain of custody is completed by the documentary
authorization gate (binding the derived facts) and by the executor, which
revalidates everything against that binding.

The planner has no apply surface, accepts no user paths beyond --batch N,
and never invokes an executor, a model, transport or retry.
"""

from __future__ import annotations

import argparse
import base64
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

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
from v238_per_call_durability import canonical_bytes, sha256_bytes  # noqa: E402

ACTION_ID = "BATCH_PLANNING"
EXECUTOR_ID = "BATCH_PLANNER_V1"
MODEL = "qwen3.5:9b"
MODEL_DIGEST = "6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7"
SOURCE_SHA256 = "0283291ca1ad212c27a3519a56a0a4dd89c706fa6d055a2b987bd9470a826bc0"
MIN_BATCH_INDEX = 8   # batches 5-7 are owned by their specific proven planners
MAX_BATCH_INDEX = 232  # plan total 233 -> indices 0..232
GLOSSARY_PATH = SRC_ROOT / "glossaries" / "v2_1_2_glossary.json"
FAMILY_LEDGER_PATH = (
    AUTHORITY_ROOT / "runtime-evidence/V238_E07_R6C_B4_RECOVERY/episode-budget.json"
)
PLAN_INVENTORY_PATH = (
    AUTHORITY_ROOT
    / "runtime-evidence/V238_E07_R6_TEXT_RECOVERY_FINAL/SUBTRANSLATE_V238_E07_R6_PRIMARY_20260815T002716Z/request-inventory.json"
)
CANONICAL_PLAN_TOTAL = 233

SOURCE_CANDIDATES = (
    AUTHORITY_ROOT / "runtime-evidence/V238_E07_R6_TEXT_RECOVERY_FINAL/SUBTRANSLATE_V238_E07_R6_PRIMARY_20260815T002716Z/source/e07.ass",
    AUTHORITY_ROOT / "runtime-evidence/V238_E07_R5_FINAL_CANDIDATE/source/e07.ass",
    AUTHORITY_ROOT / "runtime-evidence/V238_E07_R4_RECOVERY/source/e07.ass",
)

PAYLOAD_ORACLE_RECORDED_BATCHES = {
    1: {
        "unit_ids": [17, 18, 19, 20, 21, 22, 23, 25],
        "request_payload_sha256": "2de74a81d125a57c9dd4b51121a2d91ad42a4b71a2c1e24c41e0caf946ef2fb3",
    },
    2: {
        "unit_ids": [26, 27, 28, 29, 30, 31, 32, 33],
        "request_payload_sha256": "93195457c6596ee473be4033b5a35d45cb380f4cdb067c243407662b1421ceb0",
    },
    3: {
        "unit_ids": [34, 35, 36, 37, 38, 39, 40, 41],
        "request_payload_sha256": "1e0a7ca6f6f1f390043812917bd28fa10bca0b142ecb96733fd61d0ae768bfaf",
    },
    4: {
        "unit_ids": [42, 43, 44, 45, 46, 47, 48, 49],
        "request_payload_sha256": "236f7f81243f025bd757b6f116da7d0607529fa63559199309ad78513b92c7a8",
    },
}

REQUIRED_FROM_CANONICAL_AUTHORIZATION = (
    "operation_id",
    "family_id",
    "episode_id",
    "unit_ids",
    "unit_membership_sha256",
    "request_payload_sha256",
    "request_payload_path",
    "logical_batch_id",
)


class Blocked(RuntimeError):
    """Fail-closed planning abort; never a transport or write condition."""


def logical_batch_id(batch_index: int) -> str:
    # Pipeline convention from src/subtranslate/pipeline_v2_1_3.py:
    # logical_batch_id = f"v226-initial-{batch_index:06d}"
    return f"v226-initial-{batch_index:06d}"


def membership_sha256(unit_ids: list[int]) -> str:
    return hashlib.sha256(json.dumps(unit_ids, separators=(",", ":")).encode("utf-8")).hexdigest()


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
    raise Blocked("BATCH_SOURCE_NOT_FOUND")


def load_family_facts() -> dict[str, Any]:
    ledger = json.loads(FAMILY_LEDGER_PATH.read_text(encoding="utf-8"))
    contract = ledger.get("family_contract") or {}
    raw_revision = str(contract.get("candidate_execution_contract") or "")
    revision = next((token for token in reversed(raw_revision.replace("@", " ").split()) if len(token) == 40 and all(c in "0123456789abcdef" for c in token)), "")
    facts = {
        "episode_id": str(ledger.get("episode_id")),
        "source_sha256": contract.get("source_sha256"),
        "execution_contract_revision": revision,
    }
    if facts["source_sha256"] != SOURCE_SHA256:
        raise Blocked("BATCH_FAMILY_SOURCE_MISMATCH")
    if not revision:
        raise Blocked("BATCH_EXECUTION_CONTRACT_REVISION_UNBOUND")
    return facts


def load_plan_inventory() -> list[dict[str, Any]]:
    document = json.loads(PLAN_INVENTORY_PATH.read_text(encoding="utf-8"))
    entries = document.get("batches") if isinstance(document, dict) else document
    if not isinstance(entries, list) or not entries:
        raise Blocked("BATCH_PLAN_INVENTORY_INVALID")
    ordered = sorted(entries, key=lambda item: int(item["batch_index"]))
    if [int(item["batch_index"]) for item in ordered] != list(range(len(ordered))):
        raise Blocked("BATCH_PLAN_INVENTORY_NON_CONTIGUOUS")
    return ordered


def _extract_pinned_engine(revision: str) -> Path:
    root = Path(tempfile.mkdtemp(prefix="subtranslate-batch-planner-engine-"))
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


class _PayloadCaptured(Exception):
    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__("payload captured before durable boundary")
        self.payload = payload


def _build_runner_with_engine(source_path: Path, episode_title: str, engine_root: Path) -> tuple[Any, Any]:
    engine_src = engine_root / "src/subtranslate"
    for module_name in ("pipeline_v2_1_3", "production_v2_1_3_adapter", "production_v2_2_6_adapter"):
        sys.modules.pop(module_name, None)
    if str(engine_src) not in sys.path:
        sys.path.insert(0, str(engine_src))

    import pipeline_v2_1_3 as pipeline  # noqa: PLC0415
    from production_v2_1_3_adapter import APPROVED_CONFIG, _merged_glossary  # noqa: PLC0415
    from production_v2_2_6_adapter import V226MemoryRunner  # noqa: PLC0415

    merged_glossary = _merged_glossary(None)
    values = dict(APPROVED_CONFIG)
    values.update(
        {
            "ollama_url": "http://127.0.0.1:11434",  # never contacted
            "model": MODEL,
            "model_digest": MODEL_DIGEST,
            "series_title": "",
            "episode_title": episode_title,
            "glossary_path": str(GLOSSARY_PATH),
            "english_dictionary_path": "/nonexistent/american-english",
        }
    )
    config = pipeline.Config(**values)
    _original, events, profile = pipeline.load_events(source_path, merged_glossary)
    runner = V226MemoryRunner(events, profile, config, merged_glossary, None, 3, 79, "batch-planning-read-only")

    original_events = getattr(runner, "v221_original_events", None)
    if not original_events:
        raise Blocked("BATCH_ORIGINAL_EVENTS_UNAVAILABLE")
    runner.contexts = {event.id: pipeline.choose_context(original_events, event, runner.config) for event in original_events}
    return runner, pipeline


def capture_payload(pipeline: Any, runner: Any, units: list[Any]) -> dict[str, Any]:
    class _Captured(Exception):
        def __init__(self, payload: dict[str, Any]) -> None:
            self.payload = payload

    class _Client(pipeline.Client):
        def finalize_request_payload(self, payload, units_, phase):  # type: ignore[override]
            raise _Captured(payload)

    original_client = runner.client
    runner.client = _Client(runner.config, runner.calls, glossary=runner.glossary)
    try:
        expected = {event.id: event for unit in units for event in unit.events}
        try:
            runner.client.call(units, expected, runner.contexts, False, "initial")
        except _Captured as captured:
            return captured.payload
        raise Blocked("BATCH_PAYLOAD_CAPTURE_FAILED")
    finally:
        runner.client = original_client


def batch_ids(units: list[Any]) -> list[int]:
    return [event.id for unit in units for event in unit.events]


def plan(batch_index: int) -> dict[str, Any]:
    if not isinstance(batch_index, int) or batch_index < MIN_BATCH_INDEX or batch_index > MAX_BATCH_INDEX:
        raise Blocked(f"BATCH_INDEX_OUT_OF_RANGE:{batch_index}:allowed=[{MIN_BATCH_INDEX},{MAX_BATCH_INDEX}]")

    source_path = resolve_source()
    family = load_family_facts()
    inventory = load_plan_inventory()

    engine_root = _extract_pinned_engine(family["execution_contract_revision"])
    try:
        runner, pipeline = _build_runner_with_engine(source_path, family["episode_id"], engine_root)
        packed = runner.plan_initial_batches()

        if len(packed) != CANONICAL_PLAN_TOTAL:
            raise Blocked(f"BATCH_PLAN_TOTAL_MISMATCH:packed={len(packed)}:canonical={CANONICAL_PLAN_TOTAL}")
        if len(packed) < len(inventory):
            raise Blocked(f"BATCH_PLAN_SHORTER_THAN_INVENTORY:packed={len(packed)}:recorded={len(inventory)}")

        plan_validation: dict[str, str] = {}
        for entry in inventory:
            index = int(entry["batch_index"])
            ids = batch_ids(packed[index])
            if len(ids) != int(entry["event_count"]):
                raise Blocked(f"BATCH_PLAN_EVENT_COUNT_MISMATCH:index{index}")
            if membership_sha256(ids) != entry["event_id_set_sha256"]:
                raise Blocked(f"BATCH_PLAN_MEMBERSHIP_MISMATCH:index{index}")
            plan_validation[str(index)] = "MATCH"

        payload_validation: dict[str, str] = {}
        for index, recorded in sorted(PAYLOAD_ORACLE_RECORDED_BATCHES.items()):
            ids = batch_ids(packed[index])
            if ids != recorded["unit_ids"]:
                raise Blocked(f"BATCH_PAYLOAD_ORACLE_UNIT_IDS_MISMATCH:index{index}")
            payload = capture_payload(pipeline, runner, packed[index])
            if sha256_bytes(canonical_bytes(payload)) != recorded["request_payload_sha256"]:
                raise Blocked(f"BATCH_PAYLOAD_ORACLE_HASH_MISMATCH:index{index}")
            payload_validation[str(index)] = "MATCH"

        if BATCH_INDEX_OUT_OF_RANGE_CHECK(batch_index, len(packed)):
            raise Blocked(f"BATCH_INDEX_BEYOND_PLAN:{batch_index}")
        units = packed[batch_index]
        unit_ids = batch_ids(units)
        if not unit_ids:
            raise Blocked("BATCH_TARGET_EMPTY")

        anchor = next((e for e in inventory if int(e["batch_index"]) == batch_index), None)
        target_membership = membership_sha256(unit_ids)
        if anchor is not None:
            if target_membership != anchor["event_id_set_sha256"] or anchor["logical_batch_id"] != logical_batch_id(batch_index):
                raise Blocked(f"BATCH_TARGET_ANCHOR_MISMATCH:index{batch_index}")

        payload = capture_payload(pipeline, runner, units)
        payload_bytes = canonical_bytes(payload)
        payload_sha256 = sha256_bytes(payload_bytes)

        return {
            "status": "READY",
            "mode": "PLAN_READ_ONLY",
            "action_id": ACTION_ID,
            "executor_id": EXECUTOR_ID,
            "side_effects_performed": False,
            "target": {
                "operation_id_binding": "REQUIRED_FROM_CANONICAL_AUTHORIZATION",
                "episode_id": family["episode_id"],
                "execution_contract_revision": family["execution_contract_revision"],
                "logical_batch_id": logical_batch_id(batch_index),
                "batch_index": batch_index,
                "unit_ids": unit_ids,
                "event_count": len(unit_ids),
                "unit_membership_sha256": target_membership,
                "recorded_event_id_set_sha256": anchor["event_id_set_sha256"] if anchor else None,
                "anchor_status": "RECORDED_MATCH" if anchor else "ENGINE_PROOF_ONLY_NO_RECORDED_ANCHOR",
                "request_payload_sha256": payload_sha256,
                "request_payload_bytes": len(payload_bytes),
                "request_payload_canonical_b64": base64.b64encode(payload_bytes).decode("ascii"),
                "suggested_request_payload_dir": str(
                    AUTHORITY_ROOT / "runtime-evidence/V238_E07_R6C_BATCHES_1_7/planning" / logical_batch_id(batch_index)
                ),
            },
            "required_from_canonical_authorization": list(REQUIRED_FROM_CANONICAL_AUTHORIZATION),
            "validation": {
                "plan_inventory_entries": len(inventory),
                "packed_initial_batches_total": len(packed),
                "plan_membership_reconstructed": plan_validation,
                "payload_oracle_reconstructed": payload_validation,
                "contexts_source": "V221_ORIGINAL_EVENTS",
                "engine_revision": family["execution_contract_revision"],
                "source_path": str(source_path),
            },
            "execution_authorized": False,
            "model_call": False,
            "transport": False,
            "runtime_write": False,
            "next_gate": "BATCH_DOCUMENTAL_AUTHORIZATION_REQUIRED",
        }
    finally:
        shutil.rmtree(engine_root, ignore_errors=True)


def BATCH_INDEX_OUT_OF_RANGE_CHECK(batch_index: int, packed_len: int) -> bool:
    return batch_index >= packed_len


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", action="store_true", required=True)
    parser.add_argument("--batch", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        result = plan(args.batch)
        print(json.dumps(result, sort_keys=True, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "FAIL_STOP", "action_id": ACTION_ID,
                          "side_effects_performed": False,
                          "blocker": f"{type(exc).__name__}:{exc}"}, sort_keys=True, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
