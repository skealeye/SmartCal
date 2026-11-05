"""Final-review fixes in the Day/Week/Month window."""
from datetime import date

from test_window import make


def test_unsynced_banner_only_when_view_leaves_the_synced_range(qtbot, tmp_path):
    w = make(qtbot, tmp_path, [], "week")
    assert not w.unsynced_shown()                 # this week: synced
    w.set_view("month")
    assert not w.unsynced_shown()                 # Nov 2026 grid ends Dec 12, inside
    w.go_to(date(2027, 1, 15))
    assert w.unsynced_shown()
    assert "Not synced for these dates" in w.unsynced_label.text()
    assert "Oct 28 to Jan 3, 2027" in w.unsynced_label.text()
    w.set_view("day")
    w.go_to(date(2026, 10, 27))                   # older than a week
    assert w.unsynced_shown()
    w.go_to(date(2026, 10, 28))
    assert not w.unsynced_shown()
    w.go_to(date(2027, 1, 3))
    assert not w.unsynced_shown()
