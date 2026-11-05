"""I1: only one SmartCal runs per data folder; a second launch shows the first's popup."""
from smartcal.single_instance import SingleInstance, server_name


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
