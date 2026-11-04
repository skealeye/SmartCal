import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from spike_google import format_event  # noqa: E402


def test_timed_event():
    ev = {"summary": "Standup", "calendar_name": "Work",
          "start": {"dateTime": "2026-11-04T09:30:00-05:00"}}
    assert format_event(ev) == "09:30    Standup  [Work]"


def test_all_day_event_without_title():
    ev = {"calendar_name": "Personal", "start": {"date": "2026-11-04"}}
    assert format_event(ev) == "All day  (no title)  [Personal]"
