import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from transass_desktop.paths import default_paths


def test_paths_honor_explicit_roots(tmp_path, monkeypatch):
    monkeypatch.setenv("TRANSASS_DATA_DIR", str(tmp_path / "dados"))
    monkeypatch.setenv("TRANSASS_CONFIG_DIR", str(tmp_path / "config"))

    paths = default_paths().ensure()

    assert paths.data_root == tmp_path / "dados"
    assert paths.config_root == tmp_path / "config"
    assert paths.state_dir.is_dir()
    assert paths.library_root.is_dir()
    assert paths.failure_ledger_root.is_dir()
    assert paths.temp_root.is_dir()
    assert paths.log_root.is_dir()


def test_default_paths_are_not_container_paths(monkeypatch):
    monkeypatch.delenv("TRANSASS_DATA_DIR", raising=False)
    monkeypatch.delenv("TRANSASS_CONFIG_DIR", raising=False)
    paths = default_paths()

    assert "/app" not in os.fspath(paths.data_root)
    assert "/shows" not in os.fspath(paths.data_root)


def test_paths_honor_compose_host_aliases(monkeypatch, tmp_path):
    monkeypatch.delenv("TRANSLATOR_BASE_LIBRARY", raising=False)
    monkeypatch.delenv("TRANSLATOR_WEB_STATE_DIR", raising=False)
    media = tmp_path / "Tank" / "data" / "Shows"
    state = tmp_path / "docker" / "transass" / "state"
    media.mkdir(parents=True)
    state.mkdir(parents=True)
    monkeypatch.setenv("MEDIA_ROOT", str(media))
    monkeypatch.setenv("STATE_DIR", str(state))
    monkeypatch.setenv("TRANSASS_CONFIG_DIR", str(tmp_path / "config"))

    paths = default_paths().ensure()

    assert paths.media_root == media
    assert paths.state_dir == state
    assert paths.transport_config == state / "transport_config.json"

def test_container_state_aliases_are_bypassed(monkeypatch, tmp_path):
    monkeypatch.setenv("TRANSLATOR_WEB_STATE_DIR", "/app/state")
    monkeypatch.setenv("STATE_DIR", "/app/state")
    monkeypatch.setenv("TRANSASS_DATA_DIR", str(tmp_path / "data"))
    paths = default_paths()
    assert os.fspath(paths.state_dir) != "/app/state"
    assert paths.state_dir == tmp_path / "data" / "state"
    assert paths.transport_config == tmp_path / "data" / "state" / "transport_config.json"
