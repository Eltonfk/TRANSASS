import hashlib
import json
import os
import shutil
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.subtranslate.recovery_guard.production.broker import BrokerError, ProductionBroker
from src.subtranslate.recovery_guard.production.crypto import Ed25519Verifier, public_key_id
from src.subtranslate.recovery_guard.production.issuer import ExternalIssuer
from src.subtranslate.recovery_guard.production.manifest import (
    EXECUTOR_RELATIVE,
    MANIFEST_SCHEMA_VERSION,
    ManifestError,
    build_manifest_template,
    manifest_fingerprint,
    validate_final_manifest,
    validate_manifest,
)
from src.subtranslate.recovery_guard.production.provider import fixed_argv_identity
from src.subtranslate.recovery_guard.production.protocol import REQUEST
from src.subtranslate.recovery_guard.production.runner import FixedRunner, FUTURE_INTERPRETER
from src.subtranslate.recovery_guard.production.schema import CAPABILITY_SCHEMA_VERSION, EXECUTOR_ID
from src.subtranslate.recovery_guard.production.service import UnixBrokerService, activated_listener
from src.subtranslate.recovery_guard.production.state import ProductionStateStore


ROOT = Path(__file__).resolve().parents[2]
V2_SOURCE = ROOT / "packaging/subtranslate-guard/bundle-source/.opencode/tools/subtranslate_recovery_ledger_reprepare_v2.py"
DURABILITY_SOURCE = ROOT / "packaging/subtranslate-guard/bundle-source/src/subtranslate/v238_per_call_durability.py"
DURABILITY_SHA = "5caeb33f1bb21fbc90b7195b791e061bc46a7bddedb49bb15f52908b09d23585"
V2_SHA = "ca95eac8680897d387878f69a87b089ff60e81e598fb051fcbb97606aeb408ad"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class IntegratedGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.bundle = self.root / "bundle"
        self.bundle.mkdir()
        self.private = Ed25519PrivateKey.generate()
        self.key_id = public_key_id(self.private.public_key())
        self.manifest = self._make_bundle()

    def tearDown(self):
        self.tmp.cleanup()

    def _make_bundle(self):
        source_map = {
            "broker": "src/subtranslate/recovery_guard/production/broker.py",
            "capability_schema": "src/subtranslate/recovery_guard/production/schema.py",
            "crypto_verifier": "src/subtranslate/recovery_guard/production/crypto.py",
            "state": "src/subtranslate/recovery_guard/production/state.py",
            "journal": "src/subtranslate/recovery_guard/production/journal.py",
            "binding_provider": "src/subtranslate/recovery_guard/production/provider.py",
            "runner": "src/subtranslate/recovery_guard/production/runner.py",
            "protocol": "src/subtranslate/recovery_guard/production/protocol.py",
            "executor": EXECUTOR_RELATIVE,
            "durability": "src/subtranslate/v238_per_call_durability.py",
            "structured_tool": "opencode/subtranslate_recovery_apply_once.ts",
            "systemd_service": "systemd/subtranslate-guard.service",
            "systemd_socket": "systemd/subtranslate-guard.socket",
            "service_entrypoint": "src/subtranslate/recovery_guard/production/service_main.py",
            "interpreter": "manifest/interpreter.identity",
            "issuer": "src/subtranslate/recovery_guard/production/issuer.py",
        }
        for role, relative in source_map.items():
            target = self.bundle / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            source = {
                EXECUTOR_RELATIVE: V2_SOURCE,
                "src/subtranslate/v238_per_call_durability.py": DURABILITY_SOURCE,
                "opencode/subtranslate_recovery_apply_once.ts": ROOT / "packaging/subtranslate-guard/opencode/subtranslate_recovery_apply_once.ts",
                "systemd/subtranslate-guard.service": ROOT / "packaging/subtranslate-guard/systemd/subtranslate-guard.service",
                "systemd/subtranslate-guard.socket": ROOT / "packaging/subtranslate-guard/systemd/subtranslate-guard.socket",
            }.get(relative, ROOT / relative)
            if relative == "manifest/interpreter.identity":
                target.write_text(FUTURE_INTERPRETER + "\n", encoding="utf-8")
            else:
                shutil.copyfile(source, target)
        components = {relative: digest(self.bundle / relative) for relative in source_map.values()}
        interpreter_sha = digest(Path(FUTURE_INTERPRETER))
        manifest = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "source_git": "sha1:8740bc80116fefa4c8dd732976e931bd833a1c6c",
            "source_tree": "sha1:1b1e2b099f54f5bf89727cf7b9e5c9c9c30dfbf0",
            "components": components,
            "dependency_list": sorted(components),
            "component_roles": {role: relative for role, relative in source_map.items()},
            "executor_id": EXECUTOR_ID,
            "executor_sha256": components[EXECUTOR_RELATIVE],
            "durability_sha256": components["src/subtranslate/v238_per_call_durability.py"],
            "interpreter": {"declared_path": FUTURE_INTERPRETER, "resolved_path": FUTURE_INTERPRETER, "sha256": interpreter_sha},
            "public_key_id": self.key_id,
            "fixed_action_id": "RECOVERY_LEDGER_REPREPARATION",
            "fixed_argv_identity": fixed_argv_identity(),
            "socket_policy": "/run/subtranslate-guard/guard.sock;uid-gated;fixed-frame",
            "state_root_policy": "/var/lib/subtranslate-guard;0700;guard-owned",
            "target_policy": "fixed-B4-runtime-target;preexec-revalidation",
            "backup_policy": "/var/lib/subtranslate-guard/backups;before-publish",
            "uid_gid_policy": "subtranslate-guard:subtranslate-guard",
            "unit_hashes": {"service": components[source_map["systemd_service"]], "socket": components[source_map["systemd_socket"]]},
            "broker_sha256": components[source_map["broker"]],
            "issuer_sha256": components[source_map["issuer"]],
            "structured_tool_sha256": components[source_map["structured_tool"]],
        }
        manifest["manifest_fingerprint"] = manifest_fingerprint(manifest)
        validate_manifest(manifest, self.bundle)
        return manifest

    def _bindings(self, **changes):
        values = {
            "operation_id": "SUBTRANSLATE_V238_E07_R6C_B4_RECOVERY_20260818T165144Z",
            "family_id": "V238_E07_R6C_B4_RECOVERY", "episode_id": "79",
            "target_path": str(self.root / "target.json"), "target_prewrite_sha256": "t" * 64,
            "snapshot_fingerprint": "s" * 64, "execution_toolchain_fingerprint": "c" * 64,
            "executor_id": EXECUTOR_ID, "executor_sha256": V2_SHA, "durability_sha256": DURABILITY_SHA,
            "bundle_manifest_fingerprint": self.manifest["manifest_fingerprint"],
            "expected_blocker": "RECOVERY_LEDGER_IDENTITY_SCHEMA_INCOMPLETE",
            "fixed_argv_identity": fixed_argv_identity(),
            "python_interpreter_identity": FUTURE_INTERPRETER + ":" + digest(Path(FUTURE_INTERPRETER)),
            "public_key_id": self.key_id, "authorization_policy_version": "AUTO03B2B-INTEGRATED-1",
            "arming_authority": "root-external-issuer",
        }
        values.update(changes)
        return values

    def _context(self, *, provider=None, runner=None, manifest_check=None):
        state_root = self.root / ("state-" + str(len(list(self.root.glob("state-*")))))
        state_root.mkdir()
        state = ProductionStateStore(state_root)
        provider = provider or (lambda: self._bindings())
        issuer = ExternalIssuer(state, self.private, provider)
        issuer.issue_fixed_action()
        starts = []
        runner = runner or (lambda: starts.append(True) or True)
        broker = ProductionBroker(
            state, Ed25519Verifier(self.private.public_key(), self.key_id), provider, runner,
            manifest_check=manifest_check or (lambda: validate_manifest(self.manifest, self.bundle)),
        )
        return state, broker, starts

    def test_v2_identity_manifest_and_template_placeholder_policy(self):
        self.assertEqual(EXECUTOR_ID, "RECOVERY_LEDGER_REPREPARATION_V2")
        self.assertEqual(digest(V2_SOURCE), V2_SHA)
        self.assertEqual(digest(DURABILITY_SOURCE), DURABILITY_SHA)
        self.assertEqual(self.manifest["executor_id"], EXECUTOR_ID)
        self.assertEqual(self.manifest["executor_sha256"], V2_SHA)
        self.assertEqual(self.manifest["durability_sha256"], DURABILITY_SHA)
        template = build_manifest_template(
            components=self.manifest["components"], component_roles=self.manifest["component_roles"],
            public_key_id=self.key_id, interpreter_sha256=self.manifest["interpreter"]["sha256"],
        )
        self.assertEqual(template["executor_id"], EXECUTOR_ID)
        self.assertEqual(template["dependency_list"], sorted(self.manifest["components"]))
        with self.assertRaises(ManifestError):
            validate_final_manifest({**self.manifest, "source_git": "UNRESOLVED", "manifest_fingerprint": manifest_fingerprint({**self.manifest, "source_git": "UNRESOLVED"})}, self.bundle)
        validate_final_manifest(self.manifest, self.bundle)

    def test_integrated_issue_claim_terminal_and_replay(self):
        state, broker, starts = self._context()
        self.assertEqual(len(state.armed_paths()), 1)
        self.assertEqual(broker.execute_fixed_request(REQUEST), "SUCCEEDED")
        self.assertEqual(len(starts), 1)
        with self.assertRaises(BrokerError):
            broker.execute_fixed_request(REQUEST)
        self.assertEqual(len(starts), 1)
        self.assertEqual(len(list((state.root / "terminal").glob("*.json"))), 1)

    def test_duplicate_issue_is_blocked_and_current_physical_state_is_not_armable(self):
        state, broker, _ = self._context()
        with self.assertRaises(Exception):
            ExternalIssuer(state, self.private, lambda: self._bindings()).issue_fixed_action()
        from src.subtranslate.recovery_guard.production.provider import PhysicalBindingProvider, BindingProviderError
        provider = PhysicalBindingProvider(bundle_manifest_fingerprint=self.manifest["manifest_fingerprint"], public_key_id=self.key_id, run_probe=lambda: {
            "blockers": [{"code": "RECOVERY_LEDGER_IDENTITY_SCHEMA_INCOMPLETE"}, {"code": "CANDIDATE_UNTRACKED_UNKNOWN"}],
            "unknowns": [], "integrity": {"snapshot_consistent": True, "side_effects_performed": False},
            "runtime": {"operation": {}, "episode_budget": {}},
        })
        with self.assertRaises(BindingProviderError):
            provider.measure()

    def test_eight_concurrent_calls_have_one_claim_and_one_runner(self):
        state, broker, starts = self._context()
        outcomes = []
        lock = threading.Lock()
        def call():
            try:
                result = broker.execute_fixed_request(REQUEST)
            except Exception as exc:
                result = type(exc).__name__
            with lock:
                outcomes.append(result)
        threads = [threading.Thread(target=call) for _ in range(8)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(outcomes.count("SUCCEEDED"), 1)
        self.assertEqual(len(starts), 1)

    def test_stale_binding_and_bundle_toctou_are_fail_closed(self):
        for field in ("target_prewrite_sha256", "operation_id", "family_id", "episode_id", "snapshot_fingerprint", "execution_toolchain_fingerprint", "executor_id", "executor_sha256", "durability_sha256", "fixed_argv_identity", "public_key_id", "authorization_policy_version"):
            with self.subTest(field=field):
                changed = {field: "changed" if field not in {"executor_id"} else "RECOVERY_LEDGER_REPREPARATION_V1"}
                state, _, starts = self._context()
                changed_provider = lambda changed=changed: self._bindings(**changed)
                broker = ProductionBroker(
                    state, Ed25519Verifier(self.private.public_key(), self.key_id), changed_provider,
                    lambda: starts.append(True) or True,
                    manifest_check=lambda: validate_manifest(self.manifest, self.bundle),
                )
                with self.assertRaises(BrokerError): broker.execute_fixed_request(REQUEST)
                self.assertEqual(starts, [])
        state, broker, starts = self._context()
        original = (self.bundle / EXECUTOR_RELATIVE).read_bytes()
        (self.bundle / EXECUTOR_RELATIVE).write_bytes(original + b"x")
        with self.assertRaises(BrokerError): broker.execute_fixed_request(REQUEST)
        self.assertEqual(starts, [])

    def test_socket_frame_peer_and_activation_contract(self):
        state, broker, starts = self._context()
        left, right = socket.socketpair()
        try:
            service = UnixBrokerService(broker, os.getuid())
            class Listener:
                def accept(self): return left, None
            thread = threading.Thread(target=service.serve_once, args=(Listener(),))
            thread.start(); right.sendall(REQUEST); right.shutdown(socket.SHUT_WR)
            response = right.recv(256); thread.join()
            self.assertIn(b'"status":"SUCCEEDED"', response)
        finally:
            right.close()
        self.assertEqual(len(starts), 1)
        state2, broker2, starts2 = self._context()
        left2, right2 = socket.socketpair()
        try:
            denied = UnixBrokerService(broker2, os.getuid() + 1)
            class DeniedListener:
                def accept(self): return left2, None
            thread = threading.Thread(target=denied.serve_once, args=(DeniedListener(),))
            thread.start()
            try:
                right2.sendall(REQUEST); right2.shutdown(socket.SHUT_WR)
                response2 = right2.recv(256)
            except BrokenPipeError:
                response2 = b""
            thread.join()
            self.assertTrue(b'PEER_UID' in response2 or response2 == b"")
        finally:
            right2.close()
        self.assertEqual(starts2, [])
        class ActivatedFixture:
            family = socket.AF_UNIX
            type = socket.SOCK_STREAM
            def getsockopt(self, *_): return 1
            def close(self): pass
        activated = activated_listener({"LISTEN_PID": str(os.getpid()), "LISTEN_FDS": "1"}, fromfd=lambda *_: ActivatedFixture())
        self.assertEqual(activated.family, socket.AF_UNIX)
        with self.assertRaises(Exception):
            activated_listener({"LISTEN_PID": str(os.getpid()), "LISTEN_FDS": "0"}, fromfd=lambda *_: None)

    def test_fixed_runner_uses_v2_argv_and_rejects_bundle_mutation(self):
        calls = []
        class Result:
            returncode = 0; stdout = b""; stderr = b""
        self.assertTrue(FixedRunner(self.bundle, self.manifest, lambda *args, **kwargs: calls.append((args, kwargs)) or Result()).run())
        argv = calls[0][0][0]
        self.assertEqual(argv[0:3], (FUTURE_INTERPRETER, "-I", "-B"))
        self.assertEqual(argv[2], "-B")
        self.assertTrue(argv[3].endswith("subtranslate_recovery_ledger_reprepare_v2.py"))
        self.assertEqual(argv[4], "--apply")
        self.assertFalse(calls[0][1]["shell"])
        self.assertNotIn("PYTHONPATH", calls[0][1]["env"])
        changed = (self.bundle / "src/subtranslate/recovery_guard/production/broker.py")
        changed.write_bytes(changed.read_bytes() + b"x")
        with self.assertRaises(Exception): FixedRunner(self.bundle, self.manifest, lambda *a, **k: self.fail("runner started" )).run()

    def test_zero_model_execution_fields_and_no_v1_target(self):
        source = (ROOT / "packaging/subtranslate-guard/opencode/subtranslate_recovery_apply_once.ts").read_text()
        self.assertIn("export const args = {}", source)
        for forbidden in ("private", "issuer", "rearm", "reset", "child_process", "shell", "argv"):
            self.assertNotIn(forbidden, source.lower())
        self.assertNotEqual(EXECUTOR_ID, "RECOVERY_LEDGER_REPREPARATION_V1")


if __name__ == "__main__":
    unittest.main()
