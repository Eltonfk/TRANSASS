import hashlib
import importlib.util
import os
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.subtranslate.recovery_guard.production.state import (
    PRODUCTION_STATE_DIRECTORIES,
    PRODUCTION_STATE_MODE,
    StateError,
    _validate_production_state_boundary,
)


ROOT = Path(__file__).resolve().parents[2]
MEDIATION_INSTALLER = ROOT / "packaging/subtranslate-guard/install/subtranslate_guard_mediation_installer.py"


def load_mediation_installer():
    spec = importlib.util.spec_from_file_location("mediation_installer_test", MEDIATION_INSTALLER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ProductionStateBoundaryTests(unittest.TestCase):
    def _fixture(self):
        root = Path(tempfile.mkdtemp()) / "state"
        root.mkdir()
        for name in PRODUCTION_STATE_DIRECTORIES:
            (root / name).mkdir()
        uid, gid = 995, 985

        def provider(path):
            path = Path(path)
            info = os.lstat(path)
            if path in root.parents:
                return SimpleNamespace(st_uid=0, st_gid=0, st_mode=stat.S_IFDIR | 0o755)
            return SimpleNamespace(st_uid=uid, st_gid=gid, st_mode=stat.S_IFDIR | PRODUCTION_STATE_MODE)

        return root, uid, gid, provider

    def test_complete_layout_passes_and_partial_layout_never_repairs(self):
        root, uid, gid, provider = self._fixture()
        _validate_production_state_boundary(root, expected_uid=uid, expected_gid=gid, stat_provider=provider)
        target = root / "recovery-targets"
        target.rmdir()
        before = sorted(path.name for path in root.iterdir())
        with self.assertRaisesRegex(StateError, "LAYOUT_INCOMPLETE"):
            _validate_production_state_boundary(root, expected_uid=uid, expected_gid=gid, stat_provider=provider)
        self.assertEqual(sorted(path.name for path in root.iterdir()), before)

    def test_owner_group_mode_symlink_and_object_attacks_fail_closed(self):
        root, uid, gid, provider = self._fixture()
        for label, override in (
            ("owner", lambda info: SimpleNamespace(st_uid=1, st_gid=gid, st_mode=info.st_mode)),
            ("group", lambda info: SimpleNamespace(st_uid=uid, st_gid=1, st_mode=info.st_mode)),
            ("mode", lambda info: SimpleNamespace(st_uid=uid, st_gid=gid, st_mode=stat.S_IFDIR | 0o770)),
            ("world", lambda info: SimpleNamespace(st_uid=uid, st_gid=gid, st_mode=stat.S_IFDIR | 0o707)),
        ):
            with self.subTest(label=label):
                def bad(path, override=override):
                    info = provider(path)
                    if Path(path) == root / "armed":
                        return override(info)
                    return info
                with self.assertRaises(StateError):
                    _validate_production_state_boundary(root, expected_uid=uid, expected_gid=gid, stat_provider=bad)

        (root / "armed").rmdir()
        (root / "armed").symlink_to(root / "claimed", target_is_directory=True)
        with self.assertRaises(StateError):
            _validate_production_state_boundary(root, expected_uid=uid, expected_gid=gid, stat_provider=provider)

        (root / "armed").unlink()
        (root / "armed").write_text("not a directory")
        with self.assertRaises(StateError):
            _validate_production_state_boundary(root, expected_uid=uid, expected_gid=gid, stat_provider=provider)


class MediationInstallerSourceClosureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.installer = load_mediation_installer()

    def _synthetic_release(self, root: Path, commit: str) -> Path:
        release = root / "releases" / commit
        for relative in self.installer.RUNTIME_RELEASE_PATHS:
            if relative.startswith(".opencode/"):
                source = ROOT / "packaging/subtranslate-guard/bundle-source" / relative
            elif relative == "src/subtranslate/v238_per_call_durability.py":
                source = ROOT / "packaging/subtranslate-guard/bundle-source" / relative
            elif relative.startswith(("systemd/", "sudoers/", "opencode/", "manifests/")):
                source = ROOT / "packaging/subtranslate-guard" / relative
            else:
                source = ROOT / relative
            target = release / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
        return release

    def test_plan_is_zero_write_fixed_and_contains_complete_mediation_boundary(self):
        commit = "a" * 40
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            release = self._synthetic_release(root, commit)
            before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in release.rglob("*") if path.is_file()}
            def root_controlled_stat(path):
                path = Path(path)
                info = os.lstat(path)
                mode = stat.S_IFMT(info.st_mode) | (0o755 if stat.S_ISDIR(info.st_mode) else 0o644)
                return SimpleNamespace(st_mode=mode, st_uid=0, st_gid=0)
            plan = self.installer.build_mediation_plan(
                commit,
                release_root=release,
                _test_token=self.installer._TEST_RELEASE_TOKEN,
                prestate_provider=lambda path: {"path": str(path), "exists": False},
                release_stat_provider=lambda path: SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0, st_gid=0),
                release_file_stat_provider=root_controlled_stat,
            )
            after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in release.rglob("*") if path.is_file()}
            self.assertEqual(before, after)
            self.assertEqual(len(plan["protected_release_components"]), 30)
            self.assertEqual(plan["external_dependencies"]["schema_version"], "1.0.0")
            self.assertEqual(plan["execution_surface_effect"], "INERT_UNTIL_EXPLICIT_ACTIVATION")
            self.assertEqual(plan["activation_boundary"]["initial_capability_state"], "NOT_ISSUED")
            self.assertEqual(plan["capability_policy"], {"max_claims": 1, "max_applies": 1, "max_retries": 0, "auto_rearm": False})
            self.assertEqual(plan["apply_source_status"], "DEFERRED_EXPLICIT_PRIVILEGED_GATE")
            self.assertFalse(plan["activation_boundary"]["service_running"])
            self.assertFalse(plan["activation_boundary"]["socket_active"])
            self.assertFalse(plan["activation_boundary"]["mount_active"])
            actions = {item["action"]: item for item in plan["ordered_writeset"]}
            self.assertIn("populate_private_b4_backing", actions)
            self.assertIn("publish_current_release_selector", actions)
            self.assertIn("publish_human_arm_policy", actions)
            self.assertEqual(plan["fixed_bindings"]["structured_tool_request"], self.installer.REQUEST_LITERAL)
            self.assertFalse(plan["structured_tool_mapping"]["physical_ready"])
            self.assertFalse(plan["bypass_policy"]["direct_v2_apply_bypass_broker"])
            self.assertFalse(plan["bypass_policy"]["candidate_executor_is_production_authority"])

    def test_failure_injection_rolls_back_every_declared_stage_in_fixture(self):
        for stage in self.installer.FAILURE_INJECTION_STAGES:
            with self.subTest(stage=stage):
                result = self.installer.simulate_failure_injection(stage)
                self.assertTrue(result["rollback_complete"])
                self.assertEqual(result["residue"], [])

    def test_plan_cli_has_no_operational_path_overrides_and_apply_is_bootstrap_bound(self):
        with self.assertRaisesRegex(self.installer.MediationInstallerError, "RELEASE_ROOT_POLICY_INVALID"):
            self.installer.build_mediation_plan("a" * 40, release_root=Path("/tmp/caller-selected"))
        with self.assertRaises(self.installer.MediationInstallerError):
            self.installer._parse_args(["--plan", "--source-commit", "a" * 40, "--state-root", "/tmp/x"])
        with self.assertRaises(self.installer.MediationInstallerError):
            self.installer._parse_args(["--plan", "--apply", "--source-commit", "a" * 40])
        with self.assertRaisesRegex(self.installer.MediationInstallerError, "BOOTSTRAP_UNTRUSTED"):
            self.installer._assert_apply_self_boundary()

    def test_release_contract_and_directory_confinement_are_enforced(self):
        commit = "b" * 40
        with tempfile.TemporaryDirectory() as tmp:
            release = self._synthetic_release(Path(tmp), commit)
            def root_controlled_stat(path):
                path = Path(path)
                info = os.lstat(path)
                mode = stat.S_IFMT(info.st_mode) | (0o755 if stat.S_ISDIR(info.st_mode) else 0o644)
                return SimpleNamespace(st_mode=mode, st_uid=0, st_gid=0)
            release_file = release / "src/subtranslate/recovery_guard/production/broker.py"
            release_file.write_text("mutated\n")
            with self.assertRaisesRegex(self.installer.MediationInstallerError, "CONTRACT_MISMATCH"):
                self.installer.build_mediation_plan(
                    commit, release_root=release,
                    _test_token=self.installer._TEST_RELEASE_TOKEN,
                    prestate_provider=lambda path: {"path": str(path), "exists": False},
                    release_stat_provider=lambda path: SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0, st_gid=0),
                    release_file_stat_provider=root_controlled_stat,
                )
            release_file.write_bytes((ROOT / "src/subtranslate/recovery_guard/production/broker.py").read_bytes())
            intermediate = release / "src/subtranslate/recovery_guard/production"
            moved = release / "src/subtranslate/recovery_guard/production.real"
            intermediate.rename(moved)
            intermediate.symlink_to(moved, target_is_directory=True)
            with self.assertRaisesRegex(self.installer.MediationInstallerError, "COMPONENT_SYMLINK"):
                self.installer.build_mediation_plan(
                    commit, release_root=release,
                    _test_token=self.installer._TEST_RELEASE_TOKEN,
                    prestate_provider=lambda path: {"path": str(path), "exists": False},
                    release_stat_provider=lambda path: SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0, st_gid=0),
                    release_file_stat_provider=root_controlled_stat,
                )

    def test_key_serialization_id_pairing_and_rotation_policy_are_fail_closed(self):
        self.assertEqual(self.installer.classify_key_state(None, None), "GENERATE")
        with self.assertRaisesRegex(self.installer.MediationInstallerError, "INCOMPLETE"):
            self.installer.classify_key_state(b"private", None)
        generated_private, generated_public, generated_id = self.installer.generate_key_material()
        self.assertEqual(generated_id, self.installer.validate_key_pair(generated_private, generated_public))
        self.assertEqual(self.installer.classify_key_state(generated_private, generated_public), "REUSE")
        private = Ed25519PrivateKey.generate()
        private_bytes = self.installer.serialize_private_key(private)
        public_bytes = self.installer.serialize_public_key(private.public_key())
        key_id = self.installer.validate_key_pair(private_bytes, public_bytes)
        self.assertRegex(key_id, r"^ed25519-sha256:[0-9a-f]{64}$")
        self.assertEqual(key_id, self.installer.public_key_id(private.public_key()))
        self.assertEqual(key_id, self.installer.validate_key_pair(private_bytes, public_bytes))
        with self.assertRaisesRegex(self.installer.MediationInstallerError, "MISMATCH"):
            self.installer.validate_key_pair(private_bytes, self.installer.serialize_public_key(Ed25519PrivateKey.generate().public_key()))
        with self.assertRaises(self.installer.MediationInstallerError):
            self.installer.load_private_key_bytes(b"not-a-key")
        with self.assertRaises(self.installer.MediationInstallerError):
            self.installer.load_public_key_bytes(b"not-a-key")

    def test_source_mapping_and_static_security_surface(self):
        source = MEDIATION_INSTALLER.read_text(encoding="utf-8")
        self.assertNotIn("shell=True", source)
        self.assertNotIn("os.system", source)
        self.assertNotIn("rm -rf", source)
        self.assertNotIn("sudo -", source.lower())
        self.assertEqual(self.installer.REQUEST_LITERAL, "EXECUTE_CURRENT_ARMED_RECOVERY_CAPABILITY")
        self.assertEqual(self.installer.STRUCTURED_TOOL_PATH, Path("/usr/local/lib/subtranslate-guard/opencode/subtranslate_recovery_apply_once.ts"))
        self.assertEqual(self.installer.PRIVATE_KEY_PATH, Path("/etc/subtranslate-guard/keys/issuer.ed25519"))
        self.assertEqual(self.installer.PUBLIC_KEY_PATH, Path("/etc/subtranslate-guard/issuer.ed25519.pub"))
        self.assertEqual(self.installer.FAILURE_INJECTION_STAGES[-1], "before_final_validation")

    def test_public_key_service_path_never_traverses_private_key_directory(self):
        service = (ROOT / "src/subtranslate/recovery_guard/production/service_launcher.py").read_text(encoding="utf-8")
        self.assertIn("/etc/subtranslate-guard/issuer.ed25519.pub", service)
        self.assertNotIn("/etc/subtranslate-guard/keys/issuer.ed25519.pub", service)
        manifest = (ROOT / "src/subtranslate/recovery_guard/production/manifest.py").read_text(encoding="utf-8")
        self.assertIn("PUBLIC_KEY_POLICY_REQUIRED", manifest)
        self.assertIn("ed25519-sha256-raw-public-key", manifest)


if __name__ == "__main__":
    unittest.main()
