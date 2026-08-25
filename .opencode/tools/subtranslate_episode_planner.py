#!/usr/bin/env python3
"""AUTO-03D multi-episode planner — parameterized by episode config JSON."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
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

ACTION_ID = "EPISODE_BATCH_PLANNING"
EXECUTOR_ID = "EPISODE_PLANNER_V1"
MODEL = "qwen3.5:9b"
MODEL_DIGEST = "6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7"


class Blocked(RuntimeError):
    pass


def logical_batch_id(batch_index: int) -> str:
    return f"v226-initial-{batch_index:06d}"


def membership_sha256(unit_ids: list[int]) -> str:
    return hashlib.sha256(json.dumps(unit_ids, separators=(",", ":")).encode("utf-8")).hexdigest()


def load_config(path: str) -> dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        raise Blocked(f"CONFIG_NOT_FOUND:{path}")
    config = json.loads(p.read_text(encoding="utf-8"))
    for key in ("source_sha256", "source_candidates", "episode_id", "engine_revision"):
        if key not in config:
            raise Blocked(f"CONFIG_MISSING_KEY:{key}")
    return config


def resolve_source(config: dict) -> Path:
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


ENGINE_CACHE_ROOT = Path("/tmp/opencode/subtranslate-engine-cache")


def _safe_revision(revision: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ".-_" else "_" for ch in revision)


def extract_engine(revision: str) -> tuple[Path, bool]:
    """Extract the pinned engine sources for ``revision``.

    A persistent per-revision cache under /tmp/opencode makes every planning
    invocation after the first near-instant (the git extraction of ~100 files
    used to run once per batch).  Returns ``(root, from_cache)``.  Cache write
    failures degrade gracefully to the original throwaway tempdir behaviour.
    """
    cached = ENGINE_CACHE_ROOT / _safe_revision(revision)
    marker = cached / ".engine-cache-complete"
    try:
        if marker.is_file():
            info = cached.lstat()
            if _stat.S_ISDIR(info.st_mode) and not _stat.S_ISLNK(info.st_mode):
                return cached, True
    except OSError:
        pass
    root = Path(tempfile.mkdtemp(prefix="subtranslate-ep-planner-engine-"))
    try:
        listing = subprocess.run(
            ["git", "-C", str(CANDIDATE_ROOT), "ls-tree", "-r", "--name-only", revision, "--", "src/subtranslate"],
            capture_output=True, text=True, check=True,
        ).stdout.splitlines()
        for rel in listing:
            if not rel.endswith(".py"):
                continue
            content = subprocess.run(
                ["git", "-C", str(CANDIDATE_ROOT), "show", f"{revision}:{rel}"],
                capture_output=True, text=True, check=True,
            ).stdout
            dst = root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(content, encoding="utf-8")
        try:
            ENGINE_CACHE_ROOT.mkdir(parents=True, exist_ok=True)
            if cached.exists():
                shutil.rmtree(cached, ignore_errors=True)
            shutil.copytree(root, cached)
            (cached / ".engine-cache-complete").write_text(revision + "\n", encoding="utf-8")
            shutil.rmtree(root, ignore_errors=True)
            return cached, True
        except OSError:
            shutil.rmtree(cached, ignore_errors=True)
            return root, False
    except Exception:
        shutil.rmtree(root, ignore_errors=True)
        raise


def build_runner(source_path: Path, episode_title: str, engine_root: Path):
    engine_src = engine_root / "src/subtranslate"
    if str(engine_src) not in sys.path:
        sys.path.insert(0, str(engine_src))

    import pipeline_v2_1_3 as pipeline
    from production_v2_1_3_adapter import APPROVED_CONFIG, _merged_glossary
    from production_v2_2_6_adapter import V226MemoryRunner

    merged = _merged_glossary(None)
    values = dict(APPROVED_CONFIG)
    values.update({
        "ollama_url": "http://127.0.0.1:11434",
        "model": MODEL,
        "model_digest": MODEL_DIGEST,
        "series_title": "",
        "episode_title": episode_title,
        "glossary_path": str(CANDIDATE_ROOT / "src/subtranslate/glossaries/v2_1_2_glossary.json"),
        "english_dictionary_path": "/nonexistent/american-english",
    })
    config = pipeline.Config(**values)
    _orig, events, profile = pipeline.load_events(source_path, merged)
    runner = V226MemoryRunner(events, profile, config, merged, None, 3, 79, "multi-ep-planning")

    originals = getattr(runner, "v221_original_events", None)
    if not originals:
        raise Blocked("EPISODE_ORIGINAL_EVENTS_UNAVAILABLE")
    runner.contexts = {ev.id: pipeline.choose_context(originals, ev, runner.config) for ev in originals}
    return runner, pipeline


def capture_payload(pipeline, runner, units):
    class _Cap(Exception):
        def __init__(self, p):
            self.payload = p

    class _C(pipeline.Client):
        def finalize_request_payload(self, payload, units_, phase):
            raise _Cap(payload)

    orig = runner.client
    runner.client = _C(runner.config, runner.calls, glossary=runner.glossary)
    try:
        expected = {ev.id: ev for u in units for ev in u.events}
        try:
            runner.client.call(units, expected, runner.contexts, False, "initial")
        except _Cap as cap:
            return cap.payload
        raise Blocked("EPISODE_PAYLOAD_CAPTURE_FAILED")
    finally:
        runner.client = orig


def batch_ids(units):
    return [ev.id for u in units for ev in u.events]


def plan(config_path: str, batch_index: int) -> dict[str, Any]:
    import time

    timing: dict[str, float] = {}
    t0 = time.perf_counter()
    config = load_config(config_path)
    source_path = resolve_source(config)
    engine_root, engine_from_cache = extract_engine(config["engine_revision"])
    timing["engine_s"] = round(time.perf_counter() - t0, 3)
    try:
        t1 = time.perf_counter()
        runner, pipeline = build_runner(source_path, config["episode_title"], engine_root)
        timing["build_runner_s"] = round(time.perf_counter() - t1, 3)
        t2 = time.perf_counter()
        packed = runner.plan_initial_batches()
        timing["plan_batches_s"] = round(time.perf_counter() - t2, 3)

        # Validate against inventory if available
        inv_path = config.get("plan_inventory_path")
        plan_validation = {}
        if inv_path and Path(inv_path).is_file():
            inv = json.loads(Path(inv_path).read_text(encoding="utf-8"))
            entries = inv.get("batches", inv) if isinstance(inv, dict) else inv
            for entry in entries:
                idx = int(entry["batch_index"])
                ids = batch_ids(packed[idx])
                if len(ids) != int(entry["event_count"]) or membership_sha256(ids) != entry["event_id_set_sha256"]:
                    raise Blocked(f"EPISODE_PLAN_MEMBERSHIP_MISMATCH:index{idx}")
                plan_validation[str(idx)] = "MATCH"

        if batch_index >= len(packed):
            raise Blocked(f"EPISODE_BATCH_INDEX_OUT_OF_RANGE:{batch_index}:{len(packed)}")

        units = packed[batch_index]
        unit_ids = batch_ids(units)
        if not unit_ids:
            raise Blocked("EPISODE_TARGET_BATCH_EMPTY")

        payload = capture_payload(pipeline, runner, units)
        timing["capture_s"] = round(time.perf_counter() - t2 - timing["plan_batches_s"], 3)
        timing["total_s"] = round(time.perf_counter() - t0, 3)
        payload_bytes = canonical_bytes(payload)

        family_template = config.get("family_id_template", "V238_E{ep:02d}_R1_B{batch}_BATCH")

        return {
            "status": "READY",
            "mode": "PLAN_READ_ONLY",
            "action_id": ACTION_ID,
            "executor_id": EXECUTOR_ID,
            "side_effects_performed": False,
            "target": {
                "episode_id": config["episode_id"],
                "logical_batch_id": logical_batch_id(batch_index),
                "batch_index": batch_index,
                "unit_ids": unit_ids,
                "event_count": len(unit_ids),
                "unit_membership_sha256": membership_sha256(unit_ids),
                "request_payload_sha256": sha256_bytes(payload_bytes),
                "request_payload_bytes": len(payload_bytes),
                "request_payload_canonical_b64": base64.b64encode(payload_bytes).decode("ascii"),
                "family_id_template": family_template,
            },
            "validation": {
                "packed_total": len(packed),
                "inventory_validated": plan_validation,
                "engine_revision": config["engine_revision"],
                "engine_from_cache": engine_from_cache,
                "timing_seconds": timing,
                "source_path": str(source_path),
            },
            "execution_authorized": False,
            "model_call": False,
            "transport": False,
            "runtime_write": False,
        }
    finally:
        if not engine_from_cache:
            shutil.rmtree(engine_root, ignore_errors=True)


def plan_all_inventory(config_path: str, output_path: str) -> dict[str, Any]:
    """Single-pass full-episode planning: one runner instantiation derives the
    binding facts of EVERY batch (unit ids, membership hash, exact transport
    payload).  Strictly read-only except the inventory file itself."""
    import time

    t0 = time.perf_counter()
    config = load_config(config_path)
    source_path = resolve_source(config)
    engine_root, _from_cache = extract_engine(config["engine_revision"])
    try:
        runner, pipeline = build_runner(source_path, config["episode_title"], engine_root)
        packed = runner.plan_initial_batches()
        batches: list[dict[str, Any]] = []
        for idx, units in enumerate(packed):
            unit_ids = batch_ids(units)
            if not unit_ids:
                raise Blocked(f"EPISODE_TARGET_BATCH_EMPTY:{idx}")
            payload = capture_payload(pipeline, runner, units)
            payload_bytes = canonical_bytes(payload)
            batches.append({
                "batch_index": idx,
                "unit_ids": unit_ids,
                "event_count": len(unit_ids),
                "unit_membership_sha256": membership_sha256(unit_ids),
                "request_payload_sha256": sha256_bytes(payload_bytes),
                "request_payload_bytes": len(payload_bytes),
                "request_payload_canonical_b64": base64.b64encode(payload_bytes).decode("ascii"),
            })
        result = {
            "status": "READY",
            "mode": "PLAN_ALL_INVENTORY",
            "action_id": ACTION_ID,
            "executor_id": EXECUTOR_ID,
            "side_effects_performed": False,
            "engine_revision": config["engine_revision"],
            "source_sha256": config["source_sha256"],
            "packed_total": len(packed),
            "batches": batches,
            "timing_seconds": {"total_s": round(time.perf_counter() - t0, 3)},
        }
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(str(out), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            os.write(fd, json.dumps(result, sort_keys=True).encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        return result
    finally:
        shutil.rmtree(engine_root, ignore_errors=True)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=str, required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--batch", type=int)
    group.add_argument("--plan-all", type=str, metavar="OUTPUT_JSON")
    parser.add_argument("--plan", action="store_true", required=True)
    args = parser.parse_args(argv)
    try:
        if args.plan_all is not None:
            result = plan_all_inventory(args.config, args.plan_all)
        else:
            result = plan(args.config, args.batch)
        print(json.dumps(result, sort_keys=True, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "FAIL_STOP", "action_id": ACTION_ID,
                          "blocker": f"{type(exc).__name__}:{exc}"}, sort_keys=True, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
