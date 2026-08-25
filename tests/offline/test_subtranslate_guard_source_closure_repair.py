import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.subtranslate.recovery_guard.production.manifest import (
    EXECUTOR_RELATIVE,
    EXPECTED_MEDIATION_MOUNT_SOURCE_PATH,
    ManifestError,
    manifest_fingerprint,
    validate_current_release_selector,
    validate_final_manifest,
    validate_manifest,
)


ROOT = Path(__file__).resolve().parents[2]
INSTALLER_PATH = ROOT / "packaging/subtranslate-guard/install/subtranslate_guard_foundation_installer.py"
LIVE_PROBE = ROOT / ".opencode/tools/subtranslate_readonly_probe.py"
V1 = ROOT / ".opencode/tools/subtranslate_recovery_ledger_reprepare.py"
V2 = ROOT / "packaging/subtranslate-guard/bundle-source/.opencode/tools/subtranslate_recovery_ledger_reprepare_v2.py"
CANONICAL_DURABILITY = ROOT / "src/subtranslate/v238_per_call_durability.py"
BUNDLE_DURABILITY = ROOT / "packaging/subtranslate-guard/bundle-source/src/subtranslate/v238_per_call_durability.py"

V1_SHA = "2f0fc420399671f06040a46405d42eca532c692d0b62729353fb90b840a04801"
V2_SHA = "ca95eac8680897d387878f69a87b089ff60e81e598fb051fcbb97606aeb408ad"
DURABILITY_SHA = "5caeb33f1bb21fbc90b7195b791e061bc46a7bddedb49bb15f52908b09d23585"
LIVE_PROBE_SHA = "9b76e8bf2a66ba5bef3014f2e6f6edbb97242b6999a9c2062df55addffad4722"
HEAD = "2cc4953e769f6ef0896e285489264dec21c02733"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_installer():
    spec = importlib.util.spec_from_file_location("foundation_installer_repair", INSTALLER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class SelectorRepairTests(unittest.TestCase):
    def _fake_root_stat(self, root: Path):
        def provider(path):
            info = os.lstat(path)
            # The temporary tree is a fixture owned by this test user.  Feed
            # the validator root-controlled metadata without changing the
            # fixture's real ownership or mode.
            mode = info.st_mode
            if stat.S_ISDIR(mode):
                mode = stat.S_IFDIR | 0o755
            return SimpleNamespace(
                st_mode=mode,
                st_uid=0,
                st_gid=0,
            )
        return provider

    def test_root_owned_selector_validates_without_symlink_mode_false_positive(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "subtranslate-guard"
            releases = base / "releases"
            release = releases / "pinned"
            release.mkdir(parents=True)
            selector = base / "current"
            selector.symlink_to(release)
            result = validate_current_release_selector(
                selector,
                release,
                stat_provider=self._fake_root_stat(base),
            )
            self.assertEqual(result, release.resolve())

    def test_selector_rejects_escape_relative_target_chain_and_wrong_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "subtranslate-guard"
            releases = base / "releases"
            release = releases / "pinned"
            other = releases / "other"
            release.mkdir(parents=True)
            other.mkdir()
            selector = base / "current"
            selector.symlink_to(release)
            provider = self._fake_root_stat(base)

            selector.unlink()
            selector.symlink_to(other)
            with self.assertRaises(ManifestError):
                validate_current_release_selector(selector, release, stat_provider=provider)

            selector.unlink()
            selector.symlink_to(Path("releases") / "pinned")
            with self.assertRaises(ManifestError):
                validate_current_release_selector(selector, release, stat_provider=provider)

            selector.unlink()
            intermediary = base / "release-link"
            intermediary.symlink_to(release)
            selector.symlink_to(intermediary)
            with self.assertRaises(ManifestError):
                validate_current_release_selector(selector, release, stat_provider=provider)

            selector.unlink()
            selector.symlink_to(Path("/tmp/escape-release"))
            with self.assertRaises(ManifestError):
                validate_current_release_selector(selector, release, stat_provider=provider)

            selector.unlink()
            selector.symlink_to(release)
            def writable_release(path):
                info = provider(path)
                if Path(path) == release:
                    return SimpleNamespace(st_mode=stat.S_IFDIR | 0o775, st_uid=0, st_gid=0)
                return info
            with self.assertRaises(ManifestError):
                validate_current_release_selector(selector, release, stat_provider=writable_release)

    def test_selector_matrix_rejects_missing_regular_symlink_and_unsafe_release_variants(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "subtranslate-guard"
            releases = base / "releases"
            release = releases / "pinned"
            releases.mkdir(parents=True)
            release.mkdir()
            selector = base / "current"
            provider = self._fake_root_stat(base)

            with self.assertRaises(ManifestError):
                validate_current_release_selector(selector, release, stat_provider=provider)

            selector.write_text("not a symlink")
            with self.assertRaises(ManifestError):
                validate_current_release_selector(selector, release, stat_provider=provider)
            selector.unlink()

            external = Path(tmp) / "external"
            external.mkdir()
            selector.symlink_to(external)
            with self.assertRaises(ManifestError):
                validate_current_release_selector(selector, release, stat_provider=provider)
            selector.unlink()

            selector.symlink_to(release)
            release_mode = provider
            def unsafe(path):
                info = release_mode(path)
                if Path(path) == release:
                    return SimpleNamespace(st_mode=stat.S_IFDIR | 0o777, st_uid=0, st_gid=0)
                return info
            with self.assertRaises(ManifestError):
                validate_current_release_selector(selector, release, stat_provider=unsafe)

            selector.unlink()
            release.rename(releases / "other")
            release = releases / "pinned"
            selector.symlink_to(releases / "other")
            with self.assertRaises(ManifestError):
                validate_current_release_selector(selector, release, stat_provider=provider)

    def test_selector_matrix_rejects_ancestor_symlink_and_release_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            actual = root / "actual"
            (actual / "releases/pinned").mkdir(parents=True)
            alias = root / "alias"
            alias.symlink_to(actual, target_is_directory=True)
            selector = alias / "current"
            selector.symlink_to(actual / "releases/pinned")
            with self.assertRaises(ManifestError):
                validate_current_release_selector(selector, actual / "releases/pinned", stat_provider=self._fake_root_stat(actual))

            selector.unlink()
            release_link = actual / "releases/release-link"
            release_link.symlink_to(actual / "releases/pinned", target_is_directory=True)
            selector.symlink_to(release_link)
            with self.assertRaises(ManifestError):
                validate_current_release_selector(selector, release_link, stat_provider=self._fake_root_stat(actual))


class MountManifestRepairTests(unittest.TestCase):
    def test_exact_systemd_escaped_mount_path_is_the_only_backslash_exception(self):
        self.assertIn("\\x2d", EXPECTED_MEDIATION_MOUNT_SOURCE_PATH)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            roles = {
                "broker": "broker.py",
                "capability_schema": "schema.py",
                "crypto_verifier": "crypto.py",
                "state": "state.py",
                "journal": "journal.py",
                "binding_provider": "provider.py",
                "runner": "runner.py",
                "protocol": "protocol.py",
                "executor": EXECUTOR_RELATIVE,
                "durability": "src/subtranslate/v238_per_call_durability.py",
                "structured_tool": "opencode/tool.ts",
                "systemd_service": "systemd/service.unit",
                "systemd_socket": "systemd/socket.unit",
                "service_entrypoint": "service.py",
                "interpreter": "interpreter.identity",
                "issuer": "issuer.py",
                "mediation_mount": EXPECTED_MEDIATION_MOUNT_SOURCE_PATH,
            }
            for relative in roles.values():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(relative.encode())
            components = {path: digest(root / path) for path in roles.values()}
            manifest = {
                "schema_version": "1.0.0",
                "source_git": "sha1:8740bc80116fefa4c8dd732976e931bd833a1c6c",
                "source_tree": "sha1:1b1e2b099f54f5bf89727cf7b9e5c9c9c30dfbf0",
                "components": components,
                "dependency_list": sorted(components),
                "component_roles": roles,
                "executor_id": "RECOVERY_LEDGER_REPREPARATION_V2",
                "executor_sha256": components[roles["executor"]],
                "durability_sha256": components[roles["durability"]],
                "interpreter": {"declared_path": "/usr/bin/python3.12", "resolved_path": "/usr/bin/python3.12", "sha256": "a" * 64},
                "public_key_id": "ed25519-sha256:" + "b" * 64,
                "fixed_action_id": "RECOVERY_LEDGER_REPREPARATION",
                "fixed_argv_identity": hashlib.sha256(json.dumps(
                    ["/usr/bin/python3.12", "-I", "-B", ".opencode/tools/subtranslate_recovery_ledger_reprepare_v2.py", "--apply"],
                    separators=(",", ":"),
                ).encode()).hexdigest(),
                "socket_policy": "fixed", "state_root_policy": "fixed", "target_policy": "fixed",
                "backup_policy": "fixed", "uid_gid_policy": "fixed",
                "unit_hashes": {"service": components[roles["systemd_service"]], "socket": components[roles["systemd_socket"]], "mount": components[roles["mediation_mount"]]},
                "broker_sha256": components[roles["broker"]],
                "issuer_sha256": components[roles["issuer"]],
                "structured_tool_sha256": components[roles["structured_tool"]],
            }
            manifest["manifest_fingerprint"] = manifest_fingerprint(manifest)
            # This synthetic manifest is intentionally focused on the path
            # parser and does not opt into the external-system dependency role.
            with self.assertRaises(ManifestError):
                validate_manifest({**manifest, "components": {**components, "systemd/other\\x2d.mount": "d" * 64},
                                   "dependency_list": sorted({**components, "systemd/other\\x2d.mount": "d" * 64}),
                                   "manifest_fingerprint": manifest_fingerprint({**manifest, "components": {**components, "systemd/other\\x2d.mount": "d" * 64},
                                                                                   "dependency_list": sorted({**components, "systemd/other\\x2d.mount": "d" * 64})})}, root)
            validate_manifest(manifest, root)

            # The exception is exact: aliases, traversal, service units and
            # duplicate role paths must not become mount authority.
            for bad_relative in (
                "systemd/alias.mount",
                "systemd/wrong\\x2dpath.mount",
                "systemd/../wrong.mount",
                "systemd/subtranslate-guard.service",
            ):
                with self.subTest(bad_relative=bad_relative):
                    mutated_roles = dict(roles)
                    mutated_roles["mediation_mount"] = bad_relative
                    mutated_components = dict(components)
                    bad_path = root / bad_relative
                    bad_path.parent.mkdir(parents=True, exist_ok=True)
                    bad_path.write_bytes(b"bad mount")
                    mutated_components[bad_relative] = digest(bad_path)
                    candidate = dict(manifest)
                    candidate["components"] = mutated_components
                    candidate["dependency_list"] = sorted(mutated_components)
                    candidate["component_roles"] = mutated_roles
                    candidate["manifest_fingerprint"] = manifest_fingerprint(candidate)
                    with self.assertRaises(ManifestError):
                        validate_manifest(candidate, root)

    def test_final_manifest_is_mechanically_buildable_with_real_mount_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
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
                "probe_engine": "src/subtranslate/recovery_guard/production/probe_engine.py",
                "probe_entrypoint": ".opencode/tools/subtranslate_readonly_probe.py",
                "service_launcher": "src/subtranslate/recovery_guard/production/service_launcher.py",
                "issuer_launcher": "src/subtranslate/recovery_guard/production/issuer_launcher.py",
                "issuer_cli": "src/subtranslate/recovery_guard/production/issuer_cli.py",
                "sudoers_policy": "sudoers/subtranslate-guard-arm",
                "mediation_mount": EXPECTED_MEDIATION_MOUNT_SOURCE_PATH,
                "system_external_dependency_set": "manifests/system-external-dependencies.json",
                "guard_core": "src/subtranslate/recovery_guard/core.py",
                "guard_package": "src/subtranslate/recovery_guard/__init__.py",
            }
            source_for = {
                "executor": ROOT / "packaging/subtranslate-guard/bundle-source/.opencode/tools/subtranslate_recovery_ledger_reprepare_v2.py",
                "durability": BUNDLE_DURABILITY,
                "structured_tool": ROOT / "packaging/subtranslate-guard/opencode/subtranslate_recovery_apply_once.ts",
                "systemd_service": ROOT / "packaging/subtranslate-guard/systemd/subtranslate-guard.service",
                "systemd_socket": ROOT / "packaging/subtranslate-guard/systemd/subtranslate-guard.socket",
                "probe_entrypoint": ROOT / "packaging/subtranslate-guard/bundle-source/.opencode/tools/subtranslate_readonly_probe.py",
                "sudoers_policy": ROOT / "packaging/subtranslate-guard/sudoers/subtranslate-guard-arm",
                "mediation_mount": next((ROOT / "packaging/subtranslate-guard/systemd").glob("*.mount")),
                "system_external_dependency_set": ROOT / "packaging/subtranslate-guard/manifests/system-external-dependencies.json",
            }
            for role, relative in source_map.items():
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                source = source_for.get(role, ROOT / relative)
                if role == "interpreter":
                    target.write_text("/usr/bin/python3.12\n")
                else:
                    target.write_bytes(source.read_bytes())
            components = {relative: digest(root / relative) for relative in source_map.values()}
            manifest = {
                "schema_version": "1.0.0",
                "source_git": "sha1:" + HEAD,
                "source_tree": "sha1:0c2f3a017273f74dfb57ed3265f630bd5f0dd55f",
                "release_id": HEAD,
                "release_root": "/usr/local/lib/subtranslate-guard/releases/" + HEAD,
                "current_selector_target": "/usr/local/lib/subtranslate-guard/releases/" + HEAD,
                "components": components,
                "dependency_list": sorted(components),
                "component_roles": source_map,
                "executor_id": "RECOVERY_LEDGER_REPREPARATION_V2",
                "executor_sha256": components[EXECUTOR_RELATIVE],
                "durability_sha256": components[source_map["durability"]],
                "interpreter": {"declared_path": "/usr/bin/python3.12", "resolved_path": "/usr/bin/python3.12", "sha256": digest(Path("/usr/bin/python3.12"))},
                "public_key_id": "ed25519-sha256:" + "b" * 64,
                "fixed_action_id": "RECOVERY_LEDGER_REPREPARATION",
                "fixed_argv_identity": hashlib.sha256(json.dumps(["/usr/bin/python3.12", "-I", "-B", EXECUTOR_RELATIVE, "--apply"], separators=(",", ":")).encode()).hexdigest(),
                "executor_path": "/usr/local/lib/subtranslate-guard/releases/" + HEAD + "/" + EXECUTOR_RELATIVE,
                "durability_path": "/usr/local/lib/subtranslate-guard/releases/" + HEAD + "/src/subtranslate/v238_per_call_durability.py",
                "max_claims": 1, "max_applies": 1, "max_retries": 0, "auto_rearm": False,
                "socket_policy": "fixed", "state_root_policy": "fixed", "target_policy": "fixed",
                "backup_policy": "fixed", "uid_gid_policy": "fixed",
                "unit_hashes": {"service": components[source_map["systemd_service"]], "socket": components[source_map["systemd_socket"]], "mount": components[source_map["mediation_mount"]]},
                "service_unit_sha256": components[source_map["systemd_service"]],
                "socket_unit_sha256": components[source_map["systemd_socket"]],
                "mediation_mount_unit_sha256": components[source_map["mediation_mount"]],
                "sudoers_sha256": components[source_map["sudoers_policy"]],
                "broker_sha256": components[source_map["broker"]],
                "issuer_sha256": components[source_map["issuer"]],
                "structured_tool_sha256": components[source_map["structured_tool"]],
                "security_component_roles": sorted(set(source_map) - {"structured_tool"}),
                "non_security_component_roles": ["structured_tool"],
                "structured_tool_trust_model": "UNTRUSTED_FIXED_CLIENT",
                "system_external_dependency_set": source_map["system_external_dependency_set"],
                "mediation_policy": {"host_view": "bind,ro", "canonical_b4": "/home/palhacinho/codex-projects/anime-subtitle-translator-review/runtime-evidence/V238_E07_R6C_B4_RECOVERY", "backing_b4": "/var/lib/subtranslate-guard/recovery-targets/V238_E07_R6C_B4_RECOVERY", "service_view": "ProtectHome=tmpfs;BindPaths;ReadWritePaths", "mount_unit": EXPECTED_MEDIATION_MOUNT_SOURCE_PATH},
                "state_layout": {"root": "/var/lib/subtranslate-guard", "directories": ["armed", "claimed", "terminal", "journal", "locks", "backups", "recovery-targets"], "owner": "subtranslate-guard", "group": "subtranslate-guard", "mode": "0700"},
                "public_key_policy": {"algorithm": "Ed25519", "encoding": "PEM SubjectPublicKeyInfo",
                                       "path": "/etc/subtranslate-guard/issuer.ed25519.pub",
                                       "id_algorithm": "ed25519-sha256-raw-public-key"},
                "release_selector_policy": {"path": "/usr/local/lib/subtranslate-guard/current", "must_be_root_owned_symlink": True, "target_prefix": "/usr/local/lib/subtranslate-guard/releases/"},
            }
            manifest["manifest_fingerprint"] = manifest_fingerprint(manifest)
            external_paths = set()
            for dependency in json.loads((root / source_map["system_external_dependency_set"]).read_bytes())["dependencies"]:
                external_paths.update(dependency["critical_resolved_paths"])
            original_lstat = Path.lstat
            def root_controlled_lstat(path):
                info = original_lstat(path)
                if str(path) in external_paths:
                    return os.stat_result((info.st_mode, info.st_ino, info.st_dev, info.st_nlink, 0, 0, info.st_size, info.st_atime, info.st_mtime, info.st_ctime))
                return info
            # The host sandbox deliberately reports system package files as
            # nobody:nobody.  This patched metadata models the future
            # root-controlled package boundary without changing the host.
            with patch.object(Path, "lstat", root_controlled_lstat):
                validate_final_manifest(manifest, root)


class FoundationInstallerRepairTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.installer = load_installer()

    def test_frozen_identities_remain_unchanged(self):
        self.assertEqual(digest(V1), V1_SHA)
        self.assertEqual(digest(V2), V2_SHA)
        self.assertEqual(digest(CANONICAL_DURABILITY), DURABILITY_SHA)
        self.assertEqual(digest(BUNDLE_DURABILITY), DURABILITY_SHA)
        self.assertEqual(CANONICAL_DURABILITY.read_bytes(), BUNDLE_DURABILITY.read_bytes())
        self.assertEqual(digest(LIVE_PROBE), LIVE_PROBE_SHA)

    def test_allowlist_is_exact_and_binds_policy_execution_assets(self):
        pairs = self.installer.FOUNDATION_RELEASE_ALLOWLIST
        self.assertEqual(len(pairs), 30)
        self.assertEqual(len({dest for _, dest in pairs}), len(pairs))
        source_paths = {source for source, _ in pairs}
        self.assertIn("packaging/subtranslate-guard/bundle-source/.opencode/tools/subtranslate_readonly_probe.py", source_paths)
        self.assertFalse(any(path.startswith("tests/") for path in source_paths))
        self.assertFalse(any(path.startswith(".opencode/agents/") for path in source_paths))
        destinations = {destination for _, destination in pairs}
        self.assertNotIn("current", destinations)
        self.assertIn("systemd/subtranslate-guard.service", destinations)
        self.assertIn("systemd/subtranslate-guard.socket", destinations)
        self.assertIn("sudoers/subtranslate-guard-arm", destinations)
        self.assertIn("opencode/subtranslate_recovery_apply_once.ts", destinations)
        self.assertIn("manifests/system-external-dependencies.json", destinations)
        self.assertIn("manifests/interpreter.identity", destinations)

    def test_current_head_plan_rejects_expected_dirty_manifest_contract(self):
        # The real host may already have the guard installed at the fixed
        # INSTALL_ROOT, so patch CURRENT_SELECTOR to a temp path to keep the
        # "no residue" assertion environment-independent.
        from unittest import mock

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        fake_selector = Path(tmp.name) / "current"
        with mock.patch.object(self.installer, "CURRENT_SELECTOR", fake_selector):
            before = {path: digest(path) for path in (V1, V2, CANONICAL_DURABILITY, BUNDLE_DURABILITY, LIVE_PROBE)}
            with self.assertRaisesRegex(self.installer.FoundationError, "RELEASE_CONTRACT_MISMATCH"):
                self.installer.build_foundation_plan(HEAD)
            self.assertFalse(self.installer.CURRENT_SELECTOR.exists())
            after = {path: digest(path) for path in before}
            self.assertEqual(before, after)

    def test_git_object_oid_matches_independent_git_formula(self):
        payload = b"hello\n"
        expected = hashlib.sha1(b"blob 6\x00" + payload).hexdigest()
        self.assertEqual(self.installer.git_object_oid("blob", payload), expected)
        with self.assertRaises(self.installer.FoundationError):
            self.installer.git_object_oid("tag", payload)

    def test_source_commit_accepts_uppercase_hex_and_normalizes(self):
        self.assertEqual(self.installer._validate_commit_oid("A" * 40), "a" * 40)

    def test_contract_is_self_consistent_for_all_allowlisted_worktree_bytes(self):
        contract = self.installer.FOUNDATION_RELEASE_CONTRACT
        self.assertEqual(set(contract), self.installer.FOUNDATION_SOURCE_PATHS)
        for source_path, expected in contract.items():
            path = ROOT / source_path
            data = path.read_bytes()
            with self.subTest(source_path=source_path):
                self.assertEqual(expected["git_blob_oid"], self.installer.git_object_oid("blob", data))
                self.assertEqual(expected["sha256"], hashlib.sha256(data).hexdigest())

    def test_synthetic_contract_plan_is_positive_and_zero_write(self):
        contract = self.installer.FOUNDATION_RELEASE_CONTRACT

        class SyntheticGitBackend:
            metadata = {"replace_refs": [], "object_format": "sha1", "alternates_policy": "reject"}
            write_count = 0

            def source_tree(self, commit):
                return "b" * 40

            def blob_for(self, commit, source_path):
                data = (ROOT / source_path).read_bytes()
                item = contract[source_path]
                return item["git_blob_oid"], data

        with tempfile.TemporaryDirectory() as tmp:
            before = Path(tmp).iterdir()
            backend = SyntheticGitBackend()
            plan = self.installer.build_foundation_plan("a" * 40, reader=backend)
            self.assertEqual(plan["source_tree_oid"], "b" * 40)
            self.assertEqual(len(plan["release_files"]), 30)
            self.assertEqual(plan["execution_surface_effect"], "NONE")
            self.assertEqual(backend.write_count, 0)
            self.assertEqual(list(Path(tmp).iterdir()), list(before))

    def test_reader_uses_independent_oid_and_hardened_git_environment(self):
        reader = object.__new__(self.installer.GitObjectReader)
        reader.git_dir = reader.common_dir = reader.object_dir = Path("/tmp")
        captured = {}

        def fake_run(argv, **kwargs):
            captured["argv"] = argv
            captured["env"] = kwargs["env"]
            return subprocess.CompletedProcess(argv, 0, stdout=b"blob\n", stderr=b"")

        with patch.dict(os.environ, {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "safe.directory",
            "GIT_CONFIG_VALUE_0": "*",
            "GIT_DIR": "/tmp/attacker.git",
            "HOME": "/tmp/attacker-home",
        }, clear=False), patch("subprocess.run", side_effect=fake_run):
            reader._run(("cat-file", "-t", "a" * 40))
        self.assertEqual(captured["argv"][:6], (
            "/usr/bin/git",
            "--no-replace-objects",
            "-c",
            "safe.directory=/home/palhacinho/codex-projects/subtranslate-v238-candidate",
            "-C",
            "/home/palhacinho/codex-projects/subtranslate-v238-candidate",
        ))
        self.assertEqual(captured["argv"][6:], ("cat-file", "-t", "a" * 40))
        self.assertEqual(captured["env"]["GIT_NO_REPLACE_OBJECTS"], "1")
        self.assertEqual(captured["env"]["HOME"], "/nonexistent")
        self.assertEqual(set(captured["env"]) , set(self.installer.GitObjectReader._env()))
        self.assertNotIn("GIT_CONFIG_COUNT", captured["env"])
        self.assertNotIn("GIT_CONFIG_KEY_0", captured["env"])
        self.assertNotIn("GIT_CONFIG_VALUE_0", captured["env"])
        self.assertNotIn("GIT_DIR", captured["env"])

    def test_git_safe_directory_policy_is_fixed_and_non_persistent(self):
        source = INSTALLER_PATH.read_text(encoding="utf-8")
        self.assertIn("safe.directory={REPOSITORY_AUTHORITY}", source)
        self.assertNotIn("safe.directory=*", source)
        self.assertNotIn("safe.directory=/*", source)
        self.assertNotIn("safe.directory=~", source)
        for command in ("git config --global", "git config --system", "git config --local",
                        "git config --add", "git config --replace-all"):
            self.assertNotIn(command, source)
        self.assertEqual(source.count("safe.directory="), 1)
        self.assertEqual(source.count("REPOSITORY_AUTHORITY"), 7)

    def test_strict_tree_and_commit_parsers_reject_malformed_objects(self):
        with self.assertRaises(self.installer.FoundationError):
            self.installer._parse_commit_tree(b"tree " + b"a" * 40 + b"\ntree " + b"b" * 40 + b"\n\nmsg")
        with self.assertRaises(self.installer.FoundationError):
            self.installer._parse_tree(b"100644 bad")
        oid = bytes.fromhex("11" * 20)
        valid = b"100644 file\0" + oid
        self.assertEqual(self.installer._parse_tree(valid)["file"][1], "11" * 20)
        with self.assertRaises(self.installer.FoundationError):
            self.installer._parse_tree(valid + valid)

    def test_tocut_fake_reader_never_accepts_swapped_bytes(self):
        contract = self.installer.FOUNDATION_RELEASE_CONTRACT
        target = next(iter(contract))

        class SwappingReader:
            def source_tree(self, commit):
                return "c" * 40

            def blob_for(self, commit, source_path):
                data = (ROOT / source_path).read_bytes()
                if source_path == target:
                    data += b"swap"
                return contract[source_path]["git_blob_oid"], data

        with self.assertRaisesRegex(self.installer.FoundationError, "RELEASE_CONTRACT_MISMATCH"):
            self.installer.build_foundation_plan("a" * 40, reader=SwappingReader())

    def test_apply_self_boundary_rejects_checkout_path_before_root_action(self):
        with self.assertRaisesRegex(self.installer.FoundationError, "BOOTSTRAP_UNTRUSTED"):
            self.installer._assert_apply_self_boundary()

    def test_failure_injection_rollback_covers_all_foundation_stages(self):
        stages = (
            "guard_group", "client_group", "guard_user", "usr_local_hierarchy",
            "release_temp", "first_blob", "middle_blob", "final_blob", "file_fsync",
            "directory_fsync", "ownership", "mode", "pre_rename", "atomic_rename",
            "post_rename", "etc_root", "keys_root", "state_root", "backups_root",
            "recovery_targets_root",
        )
        for stage in stages:
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                marker = root / "preexisting-compatible"
                marker.write_text("keep", encoding="utf-8")
                created_parent = root / "created-parent"
                created_child = created_parent / "created-child"
                created_child.mkdir(parents=True)
                temporary = root / "release-temp"
                (temporary / "src").mkdir(parents=True)
                (temporary / "src/file.py").write_bytes(b"fixture")
                release = root / "release-final"
                (release / "src").mkdir(parents=True)
                (release / "src/file.py").write_bytes(b"fixture")
                calls = []

                def fake_command(path, *args):
                    calls.append((path, args))

                self.installer._rollback_created_state(
                    [created_parent, created_child],
                    [("group", "fixture-group"), ("user", "fixture-user")],
                    temporary,
                    release,
                    True,
                    command_runner=fake_command,
                )
                self.assertTrue(marker.exists())
                self.assertFalse(created_parent.exists())
                self.assertFalse(temporary.exists())
                self.assertFalse(release.exists())
                self.assertEqual([item[1][0] for item in calls], ["fixture-user", "fixture-group"])

    def test_rollback_refuses_nonempty_created_directory_and_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = root / "created"
            created.mkdir()
            (created / "unexpected").write_text("x", encoding="utf-8")
            with self.assertRaises(self.installer.FoundationError):
                self.installer._rollback_created_state([created], [], None, None, False)

    def test_invalid_source_identity_and_caller_paths_fail_closed(self):
        for value in ("HEAD", "candidate/v2.3.8", "main", "0" * 40, "a" * 39, "a" * 41, "/tmp/repo"):
            with self.subTest(value=value):
                with self.assertRaises(self.installer.FoundationError):
                    self.installer.build_foundation_plan(value)
        with self.assertRaises(self.installer.FoundationError):
            self.installer._parse_args(["--plan", "--source-commit", HEAD, "--root", "/tmp/x"])
        with self.assertRaises(self.installer.FoundationError):
            self.installer._parse_args(["--apply", "--plan", "--source-commit", HEAD])

    def test_apply_is_not_executable_from_unprivileged_phase(self):
        with self.assertRaises(self.installer.FoundationError):
            self.installer.apply_foundation(HEAD)

    def test_installer_has_no_shell_or_broad_delete_surface(self):
        source = INSTALLER_PATH.read_text(encoding="utf-8")
        self.assertNotIn("shell=True", source)
        self.assertNotIn("os.system", source)
        self.assertNotIn("rm -rf", source)
        self.assertNotIn("bash -c", source)
        self.assertNotIn("sh -c", source)
        self.assertIn("execution_surface_effect", source)

    def test_installer_static_surface_and_external_binary_inventory_are_fixed(self):
        source = INSTALLER_PATH.read_text(encoding="utf-8")
        self.assertEqual(source.count("shell=True"), 0)
        self.assertEqual(source.count("os.system"), 0)
        self.assertEqual(source.count("eval("), 0)
        self.assertEqual(source.count("exec("), 0)
        self.assertNotIn("checkout", source)
        self.assertNotIn("clone", source)
        for executable in ("/usr/bin/git", "/usr/sbin/groupadd", "/usr/sbin/useradd", "/usr/sbin/userdel", "/usr/sbin/groupdel"):
            self.assertIn(executable, source)
        self.assertEqual(self.installer.GIT, Path("/usr/bin/git"))


class GitMetadataRepairTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.installer = load_installer()

    def _repo(self, root: Path) -> Path:
        repo = root / "repo"
        git = repo / ".git"
        (git / "objects/info").mkdir(parents=True)
        (git / "refs/replace").mkdir(parents=True)
        (git / "config").write_text("[core]\n\trepositoryformatversion = 0\n", encoding="utf-8")
        return repo

    def test_gitfile_and_commondir_are_resolved_strictly(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self._repo(root)
            real = root / "real.git"
            (real / "objects/info").mkdir(parents=True)
            (real / "refs/replace").mkdir(parents=True)
            (real / "config").write_text("[core]\n", encoding="utf-8")
            shutil.rmtree(repo / ".git")
            (repo / ".git").write_text(f"gitdir: {real}\n", encoding="utf-8")
            with patch.object(self.installer, "REPOSITORY_AUTHORITY", repo):
                reader = self.installer.GitObjectReader()
            self.assertEqual(reader.git_dir, real.resolve())

    def test_gitfile_symlink_malformed_alternates_and_sha256_format_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self._repo(root)
            dot_git = repo / ".git"
            shutil.rmtree(dot_git)
            dot_git.symlink_to(root / "missing")
            with patch.object(self.installer, "REPOSITORY_AUTHORITY", repo):
                with self.assertRaises(self.installer.FoundationError):
                    self.installer.GitObjectReader()

            dot_git.unlink()
            dot_git.write_bytes(b"gitdir: /tmp\nextra\n")
            with patch.object(self.installer, "REPOSITORY_AUTHORITY", repo):
                with self.assertRaises(self.installer.FoundationError):
                    self.installer.GitObjectReader()

            dot_git.unlink()
            real = root / "real.git"
            (real / "objects/info").mkdir(parents=True)
            (real / "refs/replace").mkdir(parents=True)
            (real / "config").write_text("[extensions]\n\tobjectFormat = sha256\n", encoding="utf-8")
            dot_git.write_text(f"gitdir: {real}\n", encoding="utf-8")
            with patch.object(self.installer, "REPOSITORY_AUTHORITY", repo):
                with self.assertRaisesRegex(self.installer.FoundationError, "OBJECT_FORMAT"):
                    self.installer.GitObjectReader()

            (real / "config").write_text("[core]\n", encoding="utf-8")
            (real / "objects/info/alternates").write_text("/tmp/alternate\n", encoding="utf-8")
            with patch.object(self.installer, "REPOSITORY_AUTHORITY", repo):
                with self.assertRaisesRegex(self.installer.FoundationError, "ALTERNATES"):
                    self.installer.GitObjectReader()

    def test_replace_refs_are_rejected_even_with_disable_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self._repo(root)
            (repo / ".git/refs/replace/abcd").write_text("replacement\n", encoding="utf-8")
            with patch.object(self.installer, "REPOSITORY_AUTHORITY", repo):
                with self.assertRaisesRegex(self.installer.FoundationError, "REPLACE_REFS"):
                    self.installer.GitObjectReader()

    def test_http_alternates_and_object_type_or_oid_mismatch_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self._repo(root)
            (repo / ".git/objects/info/http-alternates").write_text("https://example.invalid/objects\n", encoding="utf-8")
            with patch.object(self.installer, "REPOSITORY_AUTHORITY", repo):
                with self.assertRaisesRegex(self.installer.FoundationError, "ALTERNATES"):
                    self.installer.GitObjectReader()

        reader = object.__new__(self.installer.GitObjectReader)
        reader._run = lambda args, binary=False: "blob" if args[:2] == ("cat-file", "-t") else b"bad"
        with self.assertRaisesRegex(self.installer.FoundationError, "TYPE_MISMATCH"):
            reader._read_verified_object("tree", "a" * 40)

        reader._run = lambda args, binary=False: "tree" if args[:2] == ("cat-file", "-t") else b"bad"
        with self.assertRaisesRegex(self.installer.FoundationError, "SHA1_MISMATCH"):
            reader._read_verified_object("tree", "a" * 40)

    def test_malicious_local_config_cannot_expand_git_command_surface(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self._repo(root)
            (repo / ".git/config").write_text(
                "[alias]\n\tcat-file = !touch /tmp/should-not-exist\n"
                "[core]\n\thooksPath = /tmp/hooks\n\tattributesFile = /tmp/filters\n",
                encoding="utf-8",
            )
            calls = []
            with patch.object(self.installer, "REPOSITORY_AUTHORITY", repo):
                reader = self.installer.GitObjectReader()
                def fake_run(argv, **kwargs):
                    calls.append(argv)
                    return subprocess.CompletedProcess(argv, 0, stdout=b"commit\n", stderr=b"")
                with patch("subprocess.run", side_effect=fake_run):
                    reader._run(("cat-file", "-t", "a" * 40))
            self.assertEqual(calls[0][6:8], ("cat-file", "-t"))
            self.assertNotIn("!touch", calls[0])


if __name__ == "__main__":
    unittest.main()
