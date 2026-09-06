"""Deterministic beta regressions for clean-machine scenarios."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).parents[2] / "src" / "subtranslate"))

from transass_desktop.paths import DesktopPaths


def test_user_paths_accept_spaces_and_unicode(tmp_path, monkeypatch):
    data = tmp_path / "Dados do Transass — usuário"
    config = tmp_path / "Configuração local"
    media = tmp_path / "Biblioteca de Animes [PT-BR]"
    media.mkdir()
    monkeypatch.setenv("TRANSASS_DATA_DIR", str(data))
    monkeypatch.setenv("TRANSASS_CONFIG_DIR", str(config))

    paths = DesktopPaths(data, config).ensure()
    paths.selected_media_folder_file.write_text(str(media) + "\n", encoding="utf-8")

    assert paths.media_root == media.resolve()
    assert paths.instance_lock.parent == config


def test_runtime_environment_never_reuses_container_aliases(monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIA_ROOT", "/shows")
    monkeypatch.setenv("STATE_DIR", "/app/state")
    paths = DesktopPaths(tmp_path / "dados", tmp_path / "config")
    paths.ensure()

    assert "/shows" not in os.fspath(paths.media_root)
    assert "/app" not in os.fspath(paths.state_dir)


def test_live_execution_identity_has_local_defaults_and_honors_build_metadata(monkeypatch):
    from runtime_config import execution_identity

    for key in ("PROMPT_SCHEMA_HASH", "CONFIGURATION_HASH", "CANDIDATE_COMMIT", "CANDIDATE_IMAGE_ID"):
        monkeypatch.delenv(key, raising=False)
    local = execution_identity({"primary": {"provider": "ollama", "model": "qwen3.5:9b"}})
    assert all(local[key] for key in ("prompt_schema_hash", "configuration_hash", "candidate_commit", "candidate_image_id"))

    monkeypatch.setenv("CANDIDATE_COMMIT", "ci-commit")
    monkeypatch.setenv("CANDIDATE_IMAGE_ID", "ci-image")
    release = execution_identity({})
    assert release["candidate_commit"] == "ci-commit"
    assert release["candidate_image_id"] == "ci-image"


def test_migration_preserves_existing_user_legend_and_is_repeatable(tmp_path):
    from state_migration import migrate_state

    legacy = tmp_path / "legacy"
    target = tmp_path / "target"
    (legacy / "anime-subtitle-library").mkdir(parents=True)
    (legacy / "jobs.json").write_text("old", encoding="utf-8")
    (legacy / "anime-subtitle-library" / "episode.pt-BR.ass").write_text("old legend", encoding="utf-8")
    (target / "anime-subtitle-library").mkdir(parents=True)
    (target / "anime-subtitle-library" / "episode.pt-BR.ass").write_text("user legend", encoding="utf-8")

    first = migrate_state(legacy, target, tmp_path / "backups")
    second = migrate_state(legacy, target, tmp_path / "backups")

    assert first["migrated"] is True
    assert second["migrated"] is False
    assert (target / "jobs.json").read_text(encoding="utf-8") == "old"
    assert (target / "anime-subtitle-library" / "episode.pt-BR.ass").read_text(encoding="utf-8") == "user legend"
