import sys
from urllib.request import urlopen
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

import pytest

from transass_desktop.runtime import LocalRuntime
from transass_desktop.paths import DesktopPaths


def runtime_for(tmp_path, *, core_root=None):
    return LocalRuntime(
        paths=DesktopPaths(tmp_path / "data", tmp_path / "config"),
        core_root=core_root or tmp_path,
    )


def test_port_defaults_to_ephemeral(monkeypatch, tmp_path):
    monkeypatch.delenv("TRANSASS_PORT", raising=False)
    runtime = runtime_for(tmp_path)
    assert runtime._port_value(None) == 0


@pytest.mark.parametrize("value", ["abc", "-1", "65536"])
def test_port_rejects_invalid_values(tmp_path, value):
    runtime = runtime_for(tmp_path)
    with pytest.raises(ValueError):
        runtime._port_value(value)


def test_port_reads_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("TRANSASS_PORT", "43127")
    runtime = runtime_for(tmp_path)
    assert runtime._port_value(None) == 43127


def test_runtime_serves_health_on_localhost(tmp_path):
    runtime = runtime_for(tmp_path, core_root=Path(__file__).parents[2])
    try:
        url = runtime.start(port=0)
        with urlopen(url + "health", timeout=5) as response:
            assert response.status == 200
        assert url.startswith("http://127.0.0.1:")
        assert runtime.running
    finally:
        runtime.stop()
    assert not runtime.running
