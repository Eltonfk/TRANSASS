"""The no-model smoke must end its process before deleting its state root."""

import os
from pathlib import Path
import subprocess
import sys


def test_beta_smoke_releases_state_handles_before_temporary_cleanup(tmp_path):
    script = Path(__file__).with_name("run_beta_smoke.py")
    environment = os.environ.copy()
    for name in ("TMPDIR", "TMP", "TEMP"):
        environment[name] = str(tmp_path)
    result = subprocess.run(
        [sys.executable, str(script)],
        env=environment,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "BETA_SMOKE_OK health,unicode-path,onboarding,provider-offline,diagnostic" in result.stdout
    assert not list(tmp_path.glob("transass-beta-*"))
