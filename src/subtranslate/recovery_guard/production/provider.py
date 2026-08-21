"""Read-only physical binding provider using the existing probe contract."""
from __future__ import annotations

import hashlib
import json
import subprocess
import re
from pathlib import Path
from typing import Any, Callable

from .bindings import validate_bindings
from .runner import FUTURE_INTERPRETER

FIXED_PROBE = Path("/home/palhacinho/codex-projects/subtranslate-v238-candidate/.opencode/tools/subtranslate_readonly_probe.py")
FIXED_PYTHON = Path(FUTURE_INTERPRETER)
FIXED_EXECUTOR = Path("/home/palhacinho/codex-projects/subtranslate-v238-candidate/packaging/subtranslate-guard/bundle-source/.opencode/tools/subtranslate_recovery_ledger_reprepare_v2.py")
FIXED_DURABILITY = Path("/home/palhacinho/codex-projects/subtranslate-v238-candidate/packaging/subtranslate-guard/bundle-source/src/subtranslate/v238_per_call_durability.py")
FIXED_TARGET = Path("/home/palhacinho/codex-projects/anime-subtitle-translator-review/runtime-evidence/V238_E07_R6C_B4_RECOVERY/episode-budget.json")
EXPECTED_OPERATION = "SUBTRANSLATE_V238_E07_R6C_B4_RECOVERY_20260818T165144Z"
EXPECTED_FAMILY = "V238_E07_R6C_B4_RECOVERY"
EXPECTED_EPISODE = "79"
EXPECTED_EXECUTOR = "RECOVERY_LEDGER_REPREPARATION_V2"
EXPECTED_EXECUTOR_SHA256 = "ca95eac8680897d387878f69a87b089ff60e81e598fb051fcbb97606aeb408ad"
EXPECTED_DURABILITY_SHA256 = "5caeb33f1bb21fbc90b7195b791e061bc46a7bddedb49bb15f52908b09d23585"
HEX64 = re.compile(r"^[0-9a-f]{64}$")

class BindingProviderError(RuntimeError):
    pass

def _sha(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise BindingProviderError("BINDING_PATH_UNSAFE")
    before = path.stat()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise BindingProviderError("BINDING_PATH_CHANGED_DURING_READ")
    return digest

def fixed_argv_identity(bundle_relative: str = ".opencode/tools/subtranslate_recovery_ledger_reprepare_v2.py") -> str:
    data = json.dumps([FUTURE_INTERPRETER, "-I", "-B", bundle_relative, "--apply"], separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(data).hexdigest()

class PhysicalBindingProvider:
    """No caller fields are accepted; action identity is fixed by construction."""
    def __init__(self, *, bundle_manifest_fingerprint: str, public_key_id: str, run_probe: Callable[[], dict[str, Any]] | None = None):
        if not HEX64.fullmatch(bundle_manifest_fingerprint) or not re.fullmatch(r"ed25519-sha256:[0-9a-f]{64}", public_key_id):
            raise BindingProviderError("PROTECTED_BINDING_ID_INVALID")
        self.bundle_manifest_fingerprint = bundle_manifest_fingerprint
        self.public_key_id = public_key_id
        self.run_probe = run_probe or self._probe

    def _probe(self) -> dict[str, Any]:
        environment = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PYTHONPATH": ""}
        try:
            result = subprocess.run((FUTURE_INTERPRETER, "-I", "-B", str(FIXED_PROBE)), shell=False, cwd="/", env=environment,
                                    capture_output=True, text=True, check=False)
            if result.returncode != 2 or len(result.stdout.encode()) > 2 * 1024 * 1024:
                raise BindingProviderError("PROBE_EXIT_UNEXPECTED")
            return json.loads(result.stdout)
        except Exception as exc:
            raise BindingProviderError("PROBE_READ_FAILED") from exc

    def measure(self) -> dict[str, str]:
        report = self.run_probe()
        blockers = report.get("blockers")
        runtime = report.get("runtime", {})
        operation = runtime.get("operation", {})
        budget = runtime.get("episode_budget", {})
        if not isinstance(blockers, list) or not isinstance(operation, dict) or not isinstance(budget, dict):
            raise BindingProviderError("PROBE_SCHEMA_INVALID")
        integrity = report.get("integrity", {})
        if report.get("unknowns") != [] or integrity.get("snapshot_consistent") is not True or integrity.get("side_effects_performed") is not False:
            raise BindingProviderError("PROBE_INTEGRITY_INVALID")
        codes = [item.get("code") for item in blockers if isinstance(item, dict)]
        if set(codes) != {"RECOVERY_LEDGER_IDENTITY_SCHEMA_INCOMPLETE"}:
            raise BindingProviderError("EXPECTED_BLOCKER_MISSING")
        operation_id = operation.get("operation_id")
        family_id = budget.get("episode_family_id")
        episode_id = budget.get("episode_id")
        if (operation_id, family_id, episode_id) != (EXPECTED_OPERATION, EXPECTED_FAMILY, EXPECTED_EPISODE):
            raise BindingProviderError("IDENTITY_FIELDS_INCOMPLETE")
        snapshot = report.get("snapshot_fingerprint", "")
        toolchain = report.get("execution_toolchain", {}).get("execution_toolchain_fingerprint", "")
        if not HEX64.fullmatch(snapshot) or not HEX64.fullmatch(toolchain):
            raise BindingProviderError("FINGERPRINT_INVALID")
        if report.get("execution_toolchain", {}).get("executor_id") != EXPECTED_EXECUTOR:
            raise BindingProviderError("EXECUTOR_ID_INVALID")
        interpreter_path = FIXED_PYTHON.resolve(strict=True)
        values = {
            "operation_id": operation_id, "family_id": family_id, "episode_id": episode_id,
            "target_path": str(FIXED_TARGET), "target_prewrite_sha256": _sha(FIXED_TARGET),
            "snapshot_fingerprint": snapshot,
            "execution_toolchain_fingerprint": toolchain,
            "executor_id": EXPECTED_EXECUTOR, "executor_sha256": _sha(FIXED_EXECUTOR),
            "durability_sha256": _sha(FIXED_DURABILITY), "bundle_manifest_fingerprint": self.bundle_manifest_fingerprint,
            "expected_blocker": "RECOVERY_LEDGER_IDENTITY_SCHEMA_INCOMPLETE",
            "fixed_argv_identity": fixed_argv_identity(),
            "python_interpreter_identity": f"{interpreter_path}:{_sha(interpreter_path)}",
            "public_key_id": self.public_key_id,
            "authorization_policy_version": "AUTO03B2B-PRODUCTION-1", "arming_authority": "root-external-issuer",
        }
        validate_bindings(values)
        if values["executor_sha256"] != EXPECTED_EXECUTOR_SHA256 or values["durability_sha256"] != EXPECTED_DURABILITY_SHA256:
            raise BindingProviderError("PROTECTED_EXECUTOR_IDENTITY_MISMATCH")
        return values
