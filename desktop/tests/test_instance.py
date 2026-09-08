import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

import pytest

from transass_desktop.instance import InstanceAlreadyRunning, SingleInstanceLock


def test_single_instance_lock_rejects_second_owner(tmp_path):
    path = tmp_path / "transass.lock"
    first = SingleInstanceLock(path)
    second = SingleInstanceLock(path)
    first.acquire()
    try:
        with pytest.raises(InstanceAlreadyRunning):
            second.acquire()
    finally:
        first.release()
        second.release()


def test_lock_can_be_reacquired_after_release(tmp_path):
    path = tmp_path / "transass.lock"
    first = SingleInstanceLock(path)
    first.acquire()
    first.release()
    with SingleInstanceLock(path):
        assert path.is_file()
    # A Windows byte-range lock intentionally prevents a second handle from
    # reading the locked byte. Verify the diagnostic PID after release.
    assert path.read_text(encoding="utf-8").strip().isdigit()
