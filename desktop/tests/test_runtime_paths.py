import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / ".." / "src" / "subtranslate"))

from runtime_paths import bundled_bin_dir, configure_binary_path


def test_binary_dir_override_is_prepended(tmp_path, monkeypatch):
    monkeypatch.setenv("TRANSASS_BIN_DIR", str(tmp_path / "bin"))
    (tmp_path / "bin").mkdir()
    monkeypatch.setenv("PATH", "/usr/bin")

    configure_binary_path()

    assert bundled_bin_dir() == tmp_path / "bin"
    assert os.environ["PATH"].split(os.pathsep)[0] == str(tmp_path / "bin")
