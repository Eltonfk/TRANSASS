"""Startup migration regressions using only disposable state and Library data."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from transass_desktop.paths import DesktopPaths
from transass_desktop.runtime import LocalRuntime


@pytest.fixture
def roots(tmp_path, monkeypatch):
    for name in (
        "STATE_DIR", "MEDIA_ROOT", "TRANSLATOR_WEB_STATE_DIR",
        "TRANSLATOR_BASE_LIBRARY", "TRANSASS_CORE_ROOT",
    ):
        monkeypatch.delenv(name, raising=False)
    core = tmp_path / "checkout"
    (core / "src" / "subtranslate").mkdir(parents=True)
    legacy = core / "deploy" / "state"
    (legacy / "anime-subtitle-library").mkdir(parents=True)
    (legacy / "jobs.json").write_bytes(b'{"history": ["legacy"]}')
    (legacy / "anime-subtitle-library" / "episode.pt-BR.ass").write_bytes(b"legacy subtitle")
    paths = DesktopPaths(tmp_path / "data", tmp_path / "config")
    monkeypatch.setenv("TRANSASS_DATA_DIR", str(paths.data_root))
    monkeypatch.setenv("TRANSASS_CONFIG_DIR", str(paths.config_root))
    return core, legacy, paths


@pytest.mark.parametrize("mode", ["injected", "default", "frozen"])
def test_startup_migrates_before_ensure_and_preserves_source(roots, monkeypatch, mode):
    core, legacy, paths = roots
    originals = {p.relative_to(legacy): p.read_bytes() for p in legacy.rglob("*") if p.is_file()}
    if mode == "frozen":
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        # A frozen resource root has no source checkout directory.
        core = legacy.parent.parent
        (core / "src" / "subtranslate").rmdir()
    runtime = LocalRuntime(paths=paths if mode == "injected" else None, core_root=core)

    for relative, content in originals.items():
        assert (legacy / relative).read_bytes() == content
        assert (runtime.paths.state_dir / relative).read_bytes() == content
    assert runtime.paths.failure_ledger_root.is_dir()
    backups = list((paths.data_root / "migration-backups").glob("state-*"))
    assert len(backups) == 1
    manifest = json.loads((backups[0] / "manifest.json").read_text(encoding="utf-8"))
    assert {p["path"] for p in manifest["files"]} == {p.as_posix() for p in originals}
    for relative, content in originals.items():
        assert (backups[0] / relative).read_bytes() == content

    LocalRuntime(paths=paths, core_root=core)
    assert list((paths.data_root / "migration-backups").glob("state-*")) == backups


def test_startup_does_not_merge_into_occupied_target(roots):
    core, legacy, paths = roots
    paths.state_dir.mkdir(parents=True)
    sentinel = paths.state_dir / "user-history.json"
    sentinel.write_bytes(b"existing user history")

    LocalRuntime(paths=paths, core_root=core)

    assert sentinel.read_bytes() == b"existing user history"
    assert not (paths.state_dir / "jobs.json").exists()
    assert not (paths.data_root / "migration-backups").exists()
    assert (legacy / "jobs.json").read_bytes() == b'{"history": ["legacy"]}'


def test_startup_stops_on_migration_error_before_ensure(roots, monkeypatch):
    import state_migration

    core, _, paths = roots

    def fail_copy(*args):
        assert not paths.state_dir.exists()
        raise OSError("synthetic copy failure")

    monkeypatch.setattr(state_migration, "migrate_from_candidates", fail_copy)
    with pytest.raises(OSError, match="synthetic copy failure"):
        LocalRuntime(paths=paths, core_root=core)

    assert not paths.library_root.exists()
    assert not paths.failure_ledger_root.exists()
    assert not paths.media_root.exists()


def test_injected_roots_are_frozen_before_project_env_load(roots):
    core, _, paths = roots
    (core / ".env").write_text("STATE_DIR=/app/state\nMEDIA_ROOT=/shows\n", encoding="utf-8")

    runtime = LocalRuntime(paths=paths, core_root=core)

    assert runtime.paths.state_dir == paths.data_root / "state"
    assert runtime.paths.media_root == paths.data_root / "media"
    assert (runtime.paths.state_dir / "jobs.json").is_file()


def test_permanent_coordination_lock_does_not_block_empty_target_migration(roots):
    from state_access import StateAccessLease

    core, legacy, paths = roots
    with StateAccessLease(paths.state_dir):
        pass
    runtime = LocalRuntime(paths=paths, core_root=core)
    assert (runtime.paths.state_dir / "jobs.json").read_bytes() == (legacy / "jobs.json").read_bytes()
    backups = list((paths.data_root / "migration-backups").glob("state-*/manifest.json"))
    assert len(backups) == 1
    assert all(entry["path"] != ".state-access.lock" for entry in json.loads(backups[0].read_text())["files"])


def test_concurrent_service_or_cleanup_blocks_migration(roots):
    from state_access import StateAccessBusy, StateAccessLease

    core, _, paths = roots
    with StateAccessLease(paths.state_dir):
        with pytest.raises(StateAccessBusy):
            LocalRuntime(paths=paths, core_root=core)
    assert not (paths.state_dir / "jobs.json").exists()
