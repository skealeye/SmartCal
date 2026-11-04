from datetime import datetime, timezone

import pytest

from smartcal.google_client import GoogleClient


class _Req:
    def __init__(self, result):
        self._r = result

    def execute(self):
        return self._r


class FakeService:
    def __init__(self, events, calendars=None):
        self.cals = calendars or [
            {"id": "me@x.com", "summary": "Me", "backgroundColor": "#112233", "primary": True}]
        self._events = events
        self.calls = []

    def calendarList(self):
        return self

    def events(self):
        return self

    def list(self, **kw):
        self.calls.append(kw)
        if "calendarId" in kw:
            if kw.get("pageToken") is None and len(self._events) > 1:
                return _Req({"items": self._events[:1], "nextPageToken": "p2"})
            if kw.get("pageToken") == "p2":
                return _Req({"items": self._events[1:]})
            return _Req({"items": self._events})
        return _Req({"items": self.cals})


def _client(events, **kw):
    return GoogleClient("me@x.com", service=FakeService(events, **kw))


def _range():
    return (datetime(2026, 11, 1, tzinfo=timezone.utc), datetime(2026, 12, 1, tzinfo=timezone.utc))


def _ev(**over):
    d = {"id": "e1", "summary": "Standup",
         "start": {"dateTime": "2026-11-04T09:00:00-05:00"},
         "end": {"dateTime": "2026-11-04T09:30:00-05:00"},
         "location": "Room 1", "description": "daily"}
    d.update(over)
    return d


def test_maps_google_event_to_event():
    [e] = _client([_ev()]).list_events(*_range())
    assert (e.id, e.account, e.title) == ("e1", "me@x.com", "Standup")
    assert (e.calendar_id, e.calendar_name, e.color) == ("me@x.com", "Me", "#112233")
    assert e.start.utcoffset().total_seconds() == -5 * 3600
    assert e.end > e.start and not e.all_day
    assert e.location == "Room 1" and e.recurring_id is None and e.meeting_url is None


def test_meeting_url_prefers_meet_then_zoom_then_teams():
    zoom = "https://us02web.zoom.us/j/123"
    teams = "https://teams.microsoft.com/l/meetup-join/abc"
    meet = "https://meet.google.com/aaa-bbb"
    evs = [
        _ev(id="a", hangoutLink=meet, location=zoom),
        _ev(id="b", location=f"see {zoom} or {teams}"),
        _ev(id="c", description=f"join: {teams}"),
        _ev(id="d"),
    ]
    got = {e.id: e.meeting_url for e in _client(evs).list_events(*_range())}
    assert got == {"a": meet, "b": zoom, "c": teams, "d": None}


def test_all_day_event_uses_date_not_datetime():
    ev = _ev(start={"date": "2026-11-05"}, end={"date": "2026-11-06"})
    [e] = _client([ev]).list_events(*_range())
    assert e.all_day
    assert e.start.tzinfo is not None and e.end.tzinfo is not None
    assert (e.start.hour, e.start.minute) == (0, 0)
    assert (e.start.year, e.start.month, e.start.day) == (2026, 11, 5)
    assert (e.end - e.start).days == 1


def test_recurring_instance_keeps_recurring_id():
    [e] = _client([_ev(id="e1_20261104", recurringEventId="e1")]).list_events(*_range())
    assert e.recurring_id == "e1"


def test_list_events_pages_and_uses_single_events():
    svc = FakeService([_ev(id="a"), _ev(id="b")])
    got = GoogleClient("me@x.com", service=svc).list_events(*_range())
    assert [e.id for e in got] == ["a", "b"]
    assert all(c["singleEvents"] for c in svc.calls if "calendarId" in c)


def test_reauth_required_when_no_token(tmp_path, monkeypatch):
    from smartcal.google_auth import ReauthRequired
    monkeypatch.setenv("APPDATA", str(tmp_path))
    with pytest.raises(ReauthRequired):
        GoogleClient("nobody@x.com").list_calendars()
