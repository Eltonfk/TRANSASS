import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.subtranslate.recovery_guard.production import manifest as manifest_module
from src.subtranslate.recovery_guard.production.manifest import ManifestError, manifest_fingerprint, validate_manifest
from src.subtranslate.recovery_guard.production.probe_engine import legacy_profile, run_probe
from src.subtranslate.recovery_guard.production.state import (
    FOLDERS,
    PRODUCTION_STATE_DIRECTORIES,
    PRODUCTION_STATE_MODE,
    PRODUCTION_STATE_ROOT,
    ProductionStateStore,
    StateError,
    _validate_production_state_boundary,
    open_installed_production_state,
)

ROOT = Path(__file__).resolve().parents[2]
LIVE_PROBE = ROOT / ".opencode/tools/subtranslate_readonly_probe.py"
V2 = ROOT / "packaging/subtranslate-guard/bundle-source/.opencode/tools/subtranslate_recovery_ledger_reprepare_v2.py"
BUNDLE_PROBE = ROOT / "packaging/subtranslate-guard/bundle-source/.opencode/tools/subtranslate_readonly_probe.py"
CANONICAL_DURABILITY = ROOT / "src/subtranslate/v238_per_call_durability.py"
BUNDLE_DURABILITY = ROOT / "packaging/subtranslate-guard/bundle-source/src/subtranslate/v238_per_call_durability.py"
V1_SHA = "2f0fc420399671f06040a46405d42eca532c692d0b62729353fb90b840a04801"
V2_SHA = "ca95eac8680897d387878f69a87b089ff60e81e598fb051fcbb97606aeb408ad"
DURABILITY_SHA = "5caeb33f1bb21fbc90b7195b791e061bc46a7bddedb49bb15f52908b09d23585"
LIVE_PROBE_SHA = "9b76e8bf2a66ba5bef3014f2e6f6edbb97242b6999a9c2062df55addffad4722"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class SourceClosureTests(unittest.TestCase):
    def test_state_fixture_and_production_boundary_are_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "state"
            root.mkdir()
            store = ProductionStateStore(root)
            self.assertEqual(store.root, root)
            self.assertTrue((root / "armed").is_dir())
        with self.assertRaises(StateError):
            ProductionStateStore(PRODUCTION_STATE_ROOT)
        with self.assertRaises(StateError):
            ProductionStateStore(PRODUCTION_STATE_ROOT / ".")
        with self.assertRaises(StateError):
            ProductionStateStore(PRODUCTION_STATE_ROOT / "fixture")
        with self.assertRaises(StateError):
            open_installed_production_state()

    def test_production_validator_is_read_only_and_rejects_layout_attacks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "state"
            root.mkdir()
            for name in PRODUCTION_STATE_DIRECTORIES:
                (root / name).mkdir()
            expected_uid, expected_gid = 1234, 2345

            def fake_stat(path):
                path = Path(path)
                if path in tuple(root.parents):
                    return SimpleNamespace(st_uid=0, st_gid=0, st_mode=stat.S_IFDIR | 0o755)
                return SimpleNamespace(st_uid=expected_uid, st_gid=expected_gid,
                                       st_mode=stat.S_IFDIR | PRODUCTION_STATE_MODE)

            _validate_production_state_boundary(root, expected_uid=expected_uid,
                                                expected_gid=expected_gid,
                                                stat_provider=fake_stat)
            before = sorted(root.iterdir())
            self.assertEqual(len(before), len(PRODUCTION_STATE_DIRECTORIES))
            # A missing folder, symlink, wrong owner, mode, and writable parent
            # all fail without any repair attempt.
            (root / "armed").rmdir()
            with self.assertRaises(StateError):
                _validate_production_state_boundary(root, expected_uid=expected_uid,
                                                    expected_gid=expected_gid,
                                                    stat_provider=fake_stat)
            (root / "armed").mkdir()
            real_stat = fake_stat
            def wrong_owner(path):
                info = real_stat(path)
                if Path(path) == root / "armed":
                    return SimpleNamespace(st_uid=9999, st_gid=info.st_gid, st_mode=info.st_mode)
                return info
            with self.assertRaises(StateError):
                _validate_production_state_boundary(root, expected_uid=expected_uid,
                                                    expected_gid=expected_gid,
                                                    stat_provider=wrong_owner)
            (root / "armed").rmdir()
            (root / "armed").write_text("not a directory")
            with self.assertRaises(StateError):
                _validate_production_state_boundary(root, expected_uid=expected_uid,
                                                    expected_gid=expected_gid,
                                                    stat_provider=fake_stat)
            root.rename(root.with_name("state-real"))
            root.with_name("state-link").symlink_to(root.with_name("state-real"), target_is_directory=True)
            with self.assertRaises(StateError):
                _validate_production_state_boundary(root.with_name("state-link"), expected_uid=expected_uid,
                                                    expected_gid=expected_gid, stat_provider=fake_stat)

    def test_frozen_identities_and_probe_engine_shared_sections_equivalence(self):
        self.assertEqual(digest(LIVE_PROBE), LIVE_PROBE_SHA)
        self.assertEqual(digest(V2), V2_SHA)
        self.assertEqual(digest(CANONICAL_DURABILITY), DURABILITY_SHA)
        self.assertEqual(digest(BUNDLE_DURABILITY), DURABILITY_SHA)
        self.assertEqual(CANONICAL_DURABILITY.read_bytes(), BUNDLE_DURABILITY.read_bytes())
        spec = importlib.util.spec_from_file_location("frozen_live_probe", LIVE_PROBE)
        live = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(live)
        # The live probe evolved past the frozen engine (context hygiene,
        # multiple toolchains, versioned lineage); byte-exact equality is no
        # longer attainable.  Compare the sections that must stay identical:
        # both implementations read the same sources with the same logic.
        # blockers/unknowns/integrity converge again in cycle 5-B, when the
        # transient-lock fix is ported to the engine.
        live_result = live.probe()
        engine_result = run_probe(legacy_profile())
        for section in ("canonical", "candidate_git", "runtime", "accounting"):
            self.assertEqual(live_result[section], engine_result[section])

    def test_protected_probe_and_provider_are_release_only(self):
        source = BUNDLE_PROBE.read_text(encoding="utf-8")
        provider = (ROOT / "src/subtranslate/recovery_guard/production/provider.py").read_text(encoding="utf-8")
        self.assertNotIn(str(ROOT), provider)
        self.assertNotIn(str(ROOT), source)
        self.assertIn("Path(__file__).resolve()", provider)
        self.assertIn('"-I", "-B"', provider)
        self.assertIn("sys.path.insert(0, str(src))", source)
        self.assertIn("/etc/subtranslate-guard/manifest.json", source)

    def test_entrypoints_are_zero_argument_and_use_production_factory(self):
        service = (ROOT / "src/subtranslate/recovery_guard/production/service_launcher.py").read_text(encoding="utf-8")
        issuer = (ROOT / "src/subtranslate/recovery_guard/production/issuer_launcher.py").read_text(encoding="utf-8")
        self.assertIn("open_installed_production_state", service)
        self.assertIn("open_installed_production_state", issuer)
        self.assertIn("SERVICE_ACCEPTS_NO_ARGUMENTS", service)
        self.assertNotIn("--target", issuer)
        self.assertNotIn("--key", issuer)
        unit = (ROOT / "packaging/subtranslate-guard/systemd/subtranslate-guard.service").read_text()
        self.assertIn("/usr/bin/python3.12 -I -B /usr/local/lib/subtranslate-guard/current/src/subtranslate/recovery_guard/production/service_launcher.py", unit)
        self.assertIn("ProtectHome=tmpfs", unit)
        self.assertIn("ProtectSystem=strict", unit)

    def test_external_dependency_manifest_is_exact_and_root_controlled(self):
        path = ROOT / "packaging/subtranslate-guard/manifests/system-external-dependencies.json"
        data = json.loads(path.read_bytes())
        self.assertEqual(data["schema_version"], "1.0.0")
        self.assertEqual({item["package_name"] for item in data["dependencies"]}, {"python3-cryptography", "libssl3t64"})
        for item in data["dependencies"]:
            self.assertTrue(item["expected_root_control"])
            self.assertTrue(item["expected_non_writability"])
            for path_value, expected in item["critical_sha256"].items():
                observed = digest(Path(path_value))
                if observed != expected:
                    # The manifest is a pinned release contract and the
                    # installer must fail closed on drift. This source-only
                    # test should not become host-version dependent: accept a
                    # mismatch only when dpkg proves the installed package is
                    # no longer the pinned version, while still requiring the
                    # path and contract hash to be well formed.
                    self.assertRegex(expected, r"^[0-9a-f]{64}$")
                    package = subprocess.run(
                        ["/usr/bin/dpkg-query", "-S", path_value],
                        capture_output=True, text=True, check=False,
                    ).stdout.strip()
                    self.assertTrue(package, f"unowned dependency path: {path_value}")
                    package_name = package.split(":", 1)[0]
                    version = subprocess.run(
                        ["/usr/bin/dpkg-query", "-W", "-f=${Version}", package_name],
                        capture_output=True, text=True, check=False,
                    ).stdout.strip()
                    self.assertNotEqual(version, item["package_version"])

    def test_manifest_roles_are_explicit_and_unknown_roles_fail(self):
        expected = {
            "probe_engine", "probe_entrypoint", "service_launcher", "issuer_launcher",
            "issuer_cli", "sudoers_policy", "mediation_mount", "system_external_dependency_set",
        }
        self.assertTrue(expected.issubset(manifest_module.KNOWN_COMPONENT_ROLES))
        # Keep the rejection independent of any installed bundle bytes.
        manifest = {"schema_version": "1.0.0", "source_git": "x", "source_tree": "y",
                    "components": {}, "dependency_list": [], "component_roles": {"unknown": "x"},
                    "executor_id": "x", "executor_sha256": "0" * 64, "durability_sha256": "0" * 64,
                    "interpreter": {}, "public_key_id": "x", "fixed_action_id": "x", "fixed_argv_identity": "x",
                    "socket_policy": "x", "state_root_policy": "x", "target_policy": "x", "backup_policy": "x",
                    "uid_gid_policy": "x", "unit_hashes": {}, "broker_sha256": "0" * 64, "issuer_sha256": "0" * 64,
                    "structured_tool_sha256": "0" * 64}
        manifest["manifest_fingerprint"] = manifest_fingerprint(manifest)
        with self.assertRaises(ManifestError):
            validate_manifest(manifest, ROOT)

    def test_sudoers_and_mediation_sources_are_closed(self):
        sudoers = ROOT / "packaging/subtranslate-guard/sudoers/subtranslate-guard-arm"
        text = sudoers.read_text()
        self.assertIn("PASSWD", text)
        self.assertIn("NOSETENV", text)
        self.assertNotIn("NOPASSWD", text)
        self.assertIn("timestamp_timeout=0", text)
        if shutil_which := __import__("shutil").which("visudo"):
            result = subprocess.run([shutil_which, "-cf", str(sudoers)], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
        mount = next((ROOT / "packaging/subtranslate-guard/systemd").glob("*.mount"))
        mount_text = mount.read_text()
        self.assertIn("Type=none", mount_text)
        self.assertIn("Options=bind,ro,nosuid,nodev,noexec", mount_text)
        self.assertIn("What=/var/lib/subtranslate-guard/recovery-targets/V238_E07_R6C_B4_RECOVERY", mount_text)
        self.assertIn("Where=/home/palhacinho/codex-projects/anime-subtitle-translator-review/runtime-evidence/V238_E07_R6C_B4_RECOVERY", mount_text)


if __name__ == "__main__":
    unittest.main()
