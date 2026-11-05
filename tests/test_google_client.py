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

    def insert(self, **kw):
        self.calls.append(("insert", kw))
        return _Req({"id": "new1", **kw["body"]})

    def patch(self, **kw):
        self.calls.append(("patch", kw))
        return _Req(_ev(id=kw["eventId"], **kw["body"]))

    def get(self, **kw):
        return _Req(_ev(id=kw["eventId"]))

    def delete(self, **kw):
        self.calls.append(("delete", kw))
        return _Req({})

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
    assert all(c["singleEvents"] for c in svc.calls if isinstance(c, dict) and "calendarId" in c)


def test_reauth_required_when_no_token(tmp_path, monkeypatch):
    from smartcal.google_auth import ReauthRequired
    monkeypatch.setenv("APPDATA", str(tmp_path))
    with pytest.raises(ReauthRequired):
        GoogleClient("nobody@x.com").list_calendars()


def _writes(svc, name):
    return [c[1] for c in svc.calls if isinstance(c, tuple) and c[0] == name]


def _local(*a):
    return datetime(*a).astimezone()


def test_create_event_timed_body():
    svc = FakeService([])
    c = GoogleClient("me@x.com", service=svc)
    e = c.create_event("me@x.com", "Lunch", _local(2026, 11, 4, 12), _local(2026, 11, 4, 13),
                       location="Cafe")
    body = _writes(svc, "insert")[0]["body"]
    assert body["summary"] == "Lunch" and body["location"] == "Cafe"
    assert "dateTime" in body["start"] and "date" not in body["start"]
    assert e.title == "Lunch" and not e.all_day


def test_create_event_all_day_uses_date_fields():
    svc = FakeService([])
    c = GoogleClient("me@x.com", service=svc)
    e = c.create_event("me@x.com", "Trip", _local(2026, 11, 5), _local(2026, 11, 6), all_day=True)
    body = _writes(svc, "insert")[0]["body"]
    assert body["start"] == {"date": "2026-11-05"} and body["end"] == {"date": "2026-11-06"}
    assert e.all_day


def test_update_recurring_instance_patches_instance_id_only():
    svc = FakeService([])
    c = GoogleClient("me@x.com", service=svc)
    c.update_event("e1_20261104", calendar_id="me@x.com", title="Moved")
    [p] = _writes(svc, "patch")
    assert p["eventId"] == "e1_20261104" and p["body"] == {"summary": "Moved"}


def test_update_start_without_all_day_raises():
    c = GoogleClient("me@x.com", service=FakeService([]))
    with pytest.raises(ValueError):
        c.update_event("e1", calendar_id="me@x.com", start=_local(2026, 11, 4, 9))
    with pytest.raises(ValueError):
        c.update_event("e1", calendar_id="me@x.com", end=_local(2026, 11, 4, 9))


def test_delete_event_calls_service():
    svc = FakeService([])
    GoogleClient("me@x.com", service=svc).delete_event("e1", calendar_id="me@x.com")
    assert _writes(svc, "delete") == [{"calendarId": "me@x.com", "eventId": "e1", "sendUpdates": "none"}]


def test_cancelled_events_are_skipped():
    got = _client([_ev(id="a"), _ev(id="b", status="cancelled")]).list_events(*_range())
    assert [e.id for e in got] == ["a"]


class _Boom:
    def __init__(self, exc):
        self.exc = exc

    def execute(self):
        raise self.exc


def _failing(exc):
    svc = FakeService([])
    svc.list = lambda **kw: _Boom(exc)
    return GoogleClient("me@x.com", service=svc)


def test_refresh_error_at_execute_maps_to_reauth_required():
    from google.auth.exceptions import RefreshError
    from smartcal.google_auth import ReauthRequired
    with pytest.raises(ReauthRequired):
        _failing(RefreshError("expired")).list_calendars()


def test_http_401_maps_to_reauth_required():
    import httplib2
    from googleapiclient.errors import HttpError
    from smartcal.google_auth import ReauthRequired
    with pytest.raises(ReauthRequired):
        _failing(HttpError(httplib2.Response({"status": 401}), b"")).list_calendars()
    with pytest.raises(HttpError):
        _failing(HttpError(httplib2.Response({"status": 500}), b"")).list_calendars()
    with pytest.raises(ConnectionError):
        _failing(ConnectionError("down")).list_calendars()


def test_zoom_lookalike_host_not_matched():
    evs = [_ev(id="a", location="https://evilzoom.us/j/1"),
           _ev(id="b", location="join https://us02web.zoom.us/j/9.")]
    got = {e.id: e.meeting_url for e in _client(evs).list_events(*_range())}
    assert got == {"a": None, "b": "https://us02web.zoom.us/j/9"}


def test_to_event_fills_updated():
    [e] = _client([_ev(updated="2026-11-04T10:00:00.000Z")]).list_events(*_range())
    assert e.updated == "2026-11-04T10:00:00.000Z"


def _before():
    from smartcal.models import Event
    return Event("e1", "me@x.com", "me@x.com", "Me", "#0", "Dentist",
                 _local(2026, 11, 5, 18), _local(2026, 11, 5, 19), False, "St", "d")


def test_restore_event_undeletes_by_patching_confirmed():
    svc = FakeService([])
    got = GoogleClient("me@x.com", service=svc).restore_event(_before())
    [p] = _writes(svc, "patch")
    assert p["eventId"] == "e1" and p["body"]["status"] == "confirmed"
    assert p["body"]["summary"] == "Dentist" and not _writes(svc, "insert")
    assert got.id == "e1"


def test_restore_event_falls_back_to_insert():
    from types import SimpleNamespace
    from googleapiclient.errors import HttpError
    svc = FakeService([])
    svc.patch = lambda **kw: _Boom(HttpError(SimpleNamespace(status=404, reason="x"), b""))
    got = GoogleClient("me@x.com", service=svc).restore_event(_before())
    [i] = _writes(svc, "insert")
    assert i["body"]["summary"] == "Dentist" and "status" not in i["body"]
    assert got.title == "Dentist"


def test_to_event_maps_series_attendees_and_free_flags():
    raw = _ev(recurrence=["RRULE:FREQ=WEEKLY"], transparency="transparent",
              organizer={"self": True},
              attendees=[{"email": "me@x.com", "self": True}, {"email": "o@y.com"}])
    [e] = _client([raw]).list_events(*_range())
    assert e.is_series_master and e.recurrence == ["RRULE:FREQ=WEEKLY"]
    assert e.attendee_count == 1 and e.organizer_self is True and e.busy is False
    [plain] = _client([_ev()]).list_events(*_range())
    assert (plain.is_series_master, plain.attendee_count, plain.organizer_self, plain.busy) == (
        False, 0, None, True)


def test_send_updates_none_on_every_write():
    svc = FakeService([])
    c = GoogleClient("me@x.com", service=svc)
    c.create_event("me@x.com", "A", _local(2026, 11, 4, 12), _local(2026, 11, 4, 13))
    c.update_event("e1", calendar_id="me@x.com", title="B")
    c.delete_event("e1", calendar_id="me@x.com")
    c.restore_event(_before())
    svc.patch = lambda **kw: _Boom(__import__("fakes").http_err(404))
    c.restore_event(_before())
    sent = [c[1] for c in svc.calls if isinstance(c, tuple)]
    assert len(sent) == 5 and all(kw["sendUpdates"] == "none" for kw in sent)


def test_restore_never_inserts_a_series_instance():
    from fakes import http_err
    svc = FakeService([])
    svc.patch = lambda **kw: _Boom(http_err(404))
    inst = _before()
    inst.recurring_id = "series1"
    with pytest.raises(Exception):
        GoogleClient("me@x.com", service=svc).restore_event(inst)
    assert not _writes(svc, "insert")


def test_restore_other_errors_propagate_without_insert():
    from fakes import http_err
    for status in (403, 500):
        svc = FakeService([])
        svc.patch = lambda **kw: _Boom(http_err(status))
        with pytest.raises(Exception):
            GoogleClient("me@x.com", service=svc).restore_event(_before())
        assert not _writes(svc, "insert")


def test_get_cancelled_event_without_times():
    svc = FakeService([])
    svc.get = lambda **kw: _Req({"id": "e1", "status": "cancelled"})
    e = GoogleClient("me@x.com", service=svc).get_event("e1", calendar_id="me@x.com")
    assert e.status == "cancelled"


def _urls(*evs):
    return {e.id: e.meeting_url for e in _client(list(evs)).list_events(*_range())}


def test_meeting_url_from_conference_data_entry_point():
    z = "https://us02web.zoom.us/j/999?pwd=xyz"
    cd = {"entryPoints": [{"entryPointType": "phone", "uri": "tel:+15551234"},
                          {"entryPointType": "video", "uri": z}]}
    assert _urls(_ev(id="a", description="", conferenceData=cd)) == {"a": z}
    assert _urls(_ev(id="b", conferenceData={"entryPoints": [{"entryPointType": "video",
                                                             "uri": "javascript:alert(1)"}]})) == {"b": None}


def test_meeting_url_zoom_in_href_with_query_kept():
    z = "https://us02web.zoom.us/j/123?pwd=abc"
    desc = f'<a href="{z}">Join Zoom Meeting</a><br>Meeting ID 123'
    assert _urls(_ev(id="a", description=desc)) == {"a": z}
    esc = '<a href="https://x.zoom.us/j/123?pwd=abc&amp;uname=me">here</a>'
    assert _urls(_ev(id="b", description=esc)) == {"b": "https://x.zoom.us/j/123?pwd=abc&uname=me"}
    plain = "Join: https://x.zoom.us/j/123?pwd=abc."
    assert _urls(_ev(id="c", description=plain)) == {"c": "https://x.zoom.us/j/123?pwd=abc"}


def test_meeting_url_prefers_conference_data_over_description():
    cd = {"entryPoints": [{"entryPointType": "video", "uri": "https://meet.google.com/aaa"}]}
    got = _urls(_ev(id="a", conferenceData=cd, hangoutLink="https://meet.google.com/zzz",
                    description="https://x.zoom.us/j/1"),
                _ev(id="b", hangoutLink="https://meet.google.com/zzz",
                    description="https://x.zoom.us/j/1"))
    assert got == {"a": "https://meet.google.com/aaa", "b": "https://meet.google.com/zzz"}
