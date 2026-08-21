"""Fixed-manifest subprocess adapter. Callers cannot provide argv or environment."""
from __future__ import annotations

import os
import subprocess
import hashlib
import json
import selectors
import time
from pathlib import Path
from typing import Any, Callable

from .manifest import validate_manifest

FUTURE_INTERPRETER = "/usr/bin/python3.12"
EXECUTOR_RELATIVE = ".opencode/tools/subtranslate_recovery_ledger_reprepare_v2.py"
MAX_OUTPUT_BYTES = 65536
RUNNER_TIMEOUT_SECONDS = 30.0

class RunnerError(RuntimeError): pass

def _bounded_subprocess_run(argv, **kwargs):
    """Run with bounded in-memory output; never delegates to a shell."""
    timeout = float(kwargs.pop("timeout", RUNNER_TIMEOUT_SECONDS))
    process = subprocess.Popen(argv, **kwargs)
    selector = selectors.DefaultSelector()
    buffers = {process.stdout: bytearray(), process.stderr: bytearray()}
    for stream in buffers:
        selector.register(stream, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                process.kill()
                process.wait()
                raise RunnerError("EXECUTOR_TIMEOUT")
            for key, _ in selector.select(remaining):
                stream = key.fileobj
                chunk = stream.read1(8192)
                if not chunk:
                    selector.unregister(stream)
                    stream.close()
                    continue
                buffers[stream].extend(chunk)
                if len(buffers[stream]) > MAX_OUTPUT_BYTES:
                    process.kill()
                    process.wait()
                    raise RunnerError("EXECUTOR_OUTPUT_LIMIT")
        return subprocess.CompletedProcess(argv, process.wait(), bytes(buffers[process.stdout]), bytes(buffers[process.stderr]))
    finally:
        selector.close()
        for stream in (process.stdout, process.stderr):
            try: stream.close()
            except Exception: pass

class FixedRunner:
    def __init__(self, bundle_root: Path, manifest: dict[str, Any], invoke: Callable[..., Any] = _bounded_subprocess_run):
        validate_manifest(manifest, bundle_root)
        self.bundle_root, self.manifest, self.invoke = bundle_root, manifest, invoke

    def run(self) -> bool:
        validate_manifest(self.manifest, self.bundle_root)
        interpreter = self.manifest["interpreter"]
        interpreter_path = Path(interpreter.get("resolved_path", ""))
        if interpreter.get("declared_path") != FUTURE_INTERPRETER or interpreter_path != Path(FUTURE_INTERPRETER) or interpreter_path.is_symlink() or not interpreter_path.is_file():
            raise RunnerError("INTERPRETER_POLICY_INVALID")
        if hashlib.sha256(interpreter_path.read_bytes()).hexdigest() != interpreter["sha256"]: raise RunnerError("INTERPRETER_HASH_MISMATCH")
        expected_identity = hashlib.sha256(json.dumps([FUTURE_INTERPRETER, "-I", "-B", EXECUTOR_RELATIVE, "--apply"], separators=(",", ":")).encode()).hexdigest()
        if self.manifest.get("fixed_argv_identity") != expected_identity: raise RunnerError("ARGV_IDENTITY_MISMATCH")
        executor_path = self.bundle_root / EXECUTOR_RELATIVE
        if self.manifest.get("executor_sha256") != self.manifest.get("components", {}).get(EXECUTOR_RELATIVE): raise RunnerError("EXECUTOR_HASH_BINDING_MISSING")
        argv = (FUTURE_INTERPRETER, "-I", "-B", str(executor_path), "--apply")
        env = {"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"}
        result = self.invoke(argv, shell=False, cwd=str(self.bundle_root), env=env, close_fds=True,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=False, timeout=RUNNER_TIMEOUT_SECONDS)
        if len(getattr(result, "stdout", b"") or b"") > MAX_OUTPUT_BYTES or len(getattr(result, "stderr", b"") or b"") > MAX_OUTPUT_BYTES:
            raise RunnerError("EXECUTOR_OUTPUT_LIMIT")
        return getattr(result, "returncode", 1) == 0
