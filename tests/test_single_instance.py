"""I1: only one SmartCal runs per data folder; a second launch shows the first's popup."""
import pytest
from PyQt6.QtCore import QLockFile

from smartcal import single_instance as si
from smartcal.single_instance import SingleInstance, server_name

Err = QLockFile.LockError


class FakeLock:
    """QLockFile stand-in: held by `pid` while `path` exists."""

    def __init__(self, path, held_by=None, error=Err.LockFailedError, stale=False):
        self.path, self.pid, self.err, self.stale = path, held_by, error, stale
        self.tries = 0
        self.unlocked = False
        if held_by is not None:
            path.write_text(str(held_by))

    def setStaleLockTime(self, ms):
        pass

    def tryLock(self, timeout=0):
        self.tries += 1
        if self.err != Err.LockFailedError:
            return False
        if self.path.exists():
            return False
        self.path.write_text("me")
        return True

    def error(self):
        return self.err

    def removeStaleLockFile(self):
        if self.stale and self.path.exists():
            self.path.unlink()
            return True
        return False

    def getLockInfo(self):
        return (self.pid is not None, self.pid or 0, "host", "python")

    def fileName(self):
        return str(self.path)

    def unlock(self):
        self.unlocked = True


class FakeSocket:
    def __init__(self, answers, log):
        self.answers, self.log = answers, log

    def connectToServer(self, name):
        self.log.append("connect")

    def waitForConnected(self, ms):
        return self.answers

    def write(self, data):
        self.log.append(data)

    def waitForBytesWritten(self, ms):
        return True

    def disconnectFromServer(self):
        pass


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(si, "ANSWER_WITHIN_S", 0.2)
    monkeypatch.setattr(si, "RETRY_SLEEP_MS", 10)


def make_instance(tmp_path, lock, answers=False, names=None, own_pid=4242):
    log = []
    inst = SingleInstance(tmp_path, lock=lock, socket_factory=lambda: FakeSocket(answers, log),
                          process_name_fn=lambda pid: (names or {}).get(pid), own_pid=own_pid)
    return inst, log


def test_lock_permission_error_still_starts(qtbot, tmp_path, caplog):
    for err in (Err.PermissionError, Err.UnknownError):
        lock = FakeLock(tmp_path / "l", error=err)
        inst, log = make_instance(tmp_path, lock)
        with caplog.at_level("WARNING"):
            assert inst.acquire() is True
        assert not log and not inst.took_over      # never waited on a "first instance"
        assert "starting without it" in caplog.text
        inst.release()
        assert not lock.unlocked                   # we never held it


def test_stale_lock_removed_and_retried(qtbot, tmp_path):
    lock = FakeLock(tmp_path / "l", held_by=999, stale=True)
    inst, log = make_instance(tmp_path, lock, answers=False)
    try:
        assert inst.acquire() is True
        assert lock.tries == 2 and not inst.took_over and log   # asked first, then retried
    finally:
        inst.release()
    assert lock.unlocked


def test_unresponsive_first_instance_starts_anyway(qtbot, tmp_path, caplog):
    # Held by a live SmartCal (not stale) that never answers.
    lock = FakeLock(tmp_path / "l", held_by=999)
    inst, log = make_instance(tmp_path, lock, answers=False, names={999: "SmartCal.exe"})
    with caplog.at_level("WARNING"):
        assert inst.acquire() is True
    try:
        assert inst.took_over and lock.tries == 1
        assert "not responding; starting anyway" in caplog.text
        assert (tmp_path / "l").exists()            # a live owner's lock is left alone
    finally:
        inst.release()


def test_responsive_first_instance_makes_second_exit(qtbot, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(si, "allow_foreground", lambda: calls.append("asfw"))
    lock = FakeLock(tmp_path / "l", held_by=999)
    inst, log = make_instance(tmp_path, lock, answers=True, names={999: "SmartCal.exe"})
    assert inst.acquire() is False
    assert log == ["connect", b"show\n"] and not inst.took_over
    assert calls == ["asfw"]                        # before connecting: popup may take focus


def test_lock_with_own_pid_is_stale(qtbot, tmp_path):
    lock = FakeLock(tmp_path / "l", held_by=4242)   # our PID: left by a dead earlier process
    inst, _ = make_instance(tmp_path, lock, own_pid=4242)
    try:
        assert inst.acquire() is True and not inst.took_over and lock.tries == 2
    finally:
        inst.release()
    other = FakeLock(tmp_path / "m", held_by=777)   # PID reused by an unrelated program
    inst2, _ = make_instance(tmp_path, other, names={777: "notepad.exe"})
    try:
        assert inst2.acquire() is True and not inst2.took_over and other.tries == 2
    finally:
        inst2.release()


def test_allow_foreground_never_raises(monkeypatch):
    import ctypes
    if hasattr(ctypes, "windll"):
        monkeypatch.setattr(ctypes.windll, "user32", None)   # attribute lookup fails
    si.allow_foreground()


def test_second_instance_with_same_data_dir_hands_over_and_is_refused(qtbot, tmp_path):
    first = SingleInstance(tmp_path)
    try:
        assert first.acquire() is True
        with qtbot.waitSignal(first.activated, timeout=3000):
            second = SingleInstance(tmp_path)
            assert second.acquire() is False
    finally:
        first.release()


def test_different_data_dirs_do_not_collide(qtbot, tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    a, b = SingleInstance(tmp_path / "a"), SingleInstance(tmp_path / "b")
    try:
        assert a.acquire() is True
        assert b.acquire() is True
    finally:
        a.release()
        b.release()


def test_released_instance_lets_the_next_one_start(qtbot, tmp_path):
    first = SingleInstance(tmp_path)
    assert first.acquire() is True
    first.release()
    again = SingleInstance(tmp_path)
    try:
        assert again.acquire() is True
    finally:
        again.release()


def test_server_name_is_per_user_and_per_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("USERNAME", "owner")
    name = server_name(tmp_path)
    assert name.startswith("SmartCal-owner-")
    assert server_name(tmp_path) == name
    assert server_name(tmp_path / "other") != name
