import json
import logging
from datetime import datetime, timedelta, timezone

from smartcal.db import Cache
from smartcal.models import CalendarInfo, Event
from smartcal.notifier import Notifier
from smartcal.settings import Settings

TZ = timezone(timedelta(hours=-5))
T0 = datetime(2026, 11, 4, 9, 0, tzinfo=TZ)


def ev(id="e1", start=T0, minutes=60, cal="primary", **kw):
    base = dict(id=id, account="a@x.com", calendar_id=cal, calendar_name="Main",
                color="#112233", title=f"Event {id}", start=start,
                end=start + timedelta(minutes=minutes), all_day=False,
                meeting_url=None)
    base.update(kw)
    return Event(**base)


class Rig:
    def __init__(self, tmp_path, events, cals=None):
        self.cache = Cache(tmp_path / "c.db")
        self.cache.upsert_calendars("a@x.com", cals or [
            CalendarInfo("primary", "Main", "#111", True),
            CalendarInfo("work", "Work", "#222", False)])
        self.cache.upsert_events("a@x.com", events)
        self.settings = Settings(tmp_path / "s.json")
        if cals is None:
            self.settings.set_hidden("work", False)
        self.toasts = []
        self.fired_path = tmp_path / "notified.json"
        self.notifier = self.make()

    def make(self, send=None):
        return Notifier(self.cache, self.settings,
                        send or (lambda *a: self.toasts.append(a)),
                        fired_path=self.fired_path)


def test_toast_fires_at_lead_time_once(tmp_path):
    r = Rig(tmp_path, [ev(meeting_url="https://meet.example/x")])
    r.notifier.check(T0 - timedelta(minutes=11))
    assert r.toasts == []
    r.notifier.check(T0 - timedelta(minutes=10))
    assert len(r.toasts) == 1
    assert r.toasts[0][0] == "Event e1" and r.toasts[0][2] == "https://meet.example/x"
    assert r.toasts[0][1] == "Starts in 10 min · 9:00 AM · Main"
    r.notifier.check(T0 - timedelta(minutes=9, seconds=30))
    r.notifier.check(T0 - timedelta(minutes=2))
    assert len(r.toasts) == 1


def test_per_calendar_lead_time(tmp_path):
    r = Rig(tmp_path, [ev("a", cal="primary"), ev("b", cal="work")])
    r.settings.set_lead("work", 30)
    r.notifier.check(T0 - timedelta(minutes=30))
    assert [t[0] for t in r.toasts] == ["Event b"]
    r.notifier.check(T0 - timedelta(minutes=10))
    assert sorted(t[0] for t in r.toasts) == ["Event a", "Event b"]


def test_muted_calendar_never_notifies(tmp_path):
    r = Rig(tmp_path, [ev()])
    r.settings.set_muted("primary", True)
    r.notifier.check(T0 - timedelta(minutes=5))
    r.notifier.check(T0 + timedelta(minutes=5))
    assert r.toasts == []


def test_hidden_calendar_never_toasts(tmp_path):
    r = Rig(tmp_path, [ev("a", cal="primary"), ev("b", cal="work")])
    r.settings.set_hidden("work", True)
    r.notifier.check(T0 - timedelta(minutes=5))
    assert [t[0] for t in r.toasts] == ["Event a"]
    # a non-primary calendar never explicitly shown is hidden by default
    (tmp_path / "x").mkdir()
    r2 = Rig(tmp_path / "x", [ev("c", cal="other")],
             cals=[CalendarInfo("other", "Other", "#333", False)])
    r2.notifier.check(T0 - timedelta(minutes=5))
    assert r2.toasts == []


def test_all_day_event_never_toasts(tmp_path):
    midnight = datetime(2026, 11, 4, tzinfo=TZ)
    r = Rig(tmp_path, [ev(start=midnight, minutes=1440, all_day=True)])
    for m in (-5, 0, 60):
        r.notifier.check(midnight + timedelta(minutes=m))
    assert r.toasts == []


def test_after_wake_shows_missed_toast_for_event_in_progress_only(tmp_path):
    r = Rig(tmp_path, [ev("over", start=T0, minutes=30),
                       ev("going", start=T0 + timedelta(minutes=5), minutes=60)])
    r.notifier.check(T0 + timedelta(minutes=45))  # woke up: "over" ended, "going" in progress
    assert [t[0] for t in r.toasts] == ["Event going"]
    assert r.toasts[0][1].startswith("Started 9:05 AM")
    r.notifier.check(T0 + timedelta(minutes=46))
    assert len(r.toasts) == 1


def test_fired_set_survives_restart(tmp_path):
    r = Rig(tmp_path, [ev()])
    r.notifier.check(T0 - timedelta(minutes=5))
    again = r.make()
    again.check(T0 - timedelta(minutes=4))
    assert len(r.toasts) == 1
    # rescheduled event (new start) is a new notification
    r.cache.replace_range("a@x.com", T0 - timedelta(days=1), T0 + timedelta(days=1),
                          [ev(start=T0 + timedelta(hours=2))])
    again.check(T0 + timedelta(hours=2) - timedelta(minutes=5))
    assert len(r.toasts) == 2


def test_fired_entries_pruned_after_two_days(tmp_path):
    r = Rig(tmp_path, [ev()])
    r.notifier.check(T0 - timedelta(minutes=5))
    assert len(json.loads(r.fired_path.read_text())) == 1
    r.notifier.check(T0 + timedelta(days=1, hours=23))
    assert len(json.loads(r.fired_path.read_text())) == 1
    r.notifier.check(T0 + timedelta(days=2, minutes=1))
    assert json.loads(r.fired_path.read_text()) == {}


def test_notifier_keeps_going_when_send_toast_raises(tmp_path, caplog):
    r = Rig(tmp_path, [ev("a"), ev("b")])
    calls = []

    def boom(title, body, url):
        calls.append(title)
        raise RuntimeError("no toast")

    n = r.make(boom)
    with caplog.at_level(logging.ERROR):
        n.check(T0 - timedelta(minutes=5))
        n.check(T0 - timedelta(minutes=4))
    assert sorted(calls) == ["Event a", "Event b"]  # both attempted, none retried
    assert "failed" in caplog.text


def test_settings_lead_clamped(tmp_path):
    s = Settings(tmp_path / "s.json")
    assert s.lead_for("x") == 10
    s.set_lead("x", 99999)
    assert s.lead_for("x") == 1440
    s.set_lead("x", -4)
    assert s.lead_for("x") == 0
    s.set_default_lead(15)
    assert Settings(tmp_path / "s.json").lead_for("y") == 15
    (tmp_path / "s.json").write_text(json.dumps({"lead_minutes": {"z": "soon", "w": 5000}}))
    s2 = Settings(tmp_path / "s.json")
    assert s2.lead_for("z") == 10 and s2.lead_for("w") == 1440


def test_settings_new_fields_roundtrip(tmp_path):
    s = Settings(tmp_path / "s.json")
    assert s.autostart is True and s.claude_model == "claude-sonnet-5-5"
    s.set_muted("m", True)
    s.set_autostart(False)
    s.set_claude_model("  other  ")
    s2 = Settings(tmp_path / "s.json")
    assert s2.is_muted("m") and s2.autostart is False and s2.claude_model == "other"


def test_long_running_event_started_days_ago_toasts_once(tmp_path):
    start = T0 - timedelta(hours=5)
    r = Rig(tmp_path, [ev(start=start, minutes=60 * 24 * 6)])
    r.notifier.check(T0)  # started 5 h ago, still running: one catch-up toast
    assert len(r.toasts) == 1 and r.toasts[0][1].startswith("Started 4:00 AM")
    for days in (1, 2, 3, 4, 5):
        r.notifier.check(T0 + timedelta(days=days))
        r.make().check(T0 + timedelta(days=days, minutes=1))  # also after a restart
    assert len(r.toasts) == 1


def test_event_started_more_than_three_days_ago_never_toasts_twice(tmp_path):
    start = T0 - timedelta(days=4)
    r = Rig(tmp_path, [ev(start=start, minutes=60 * 24 * 10)])
    for m in range(0, 600, 30):
        r.notifier.check(T0 + timedelta(minutes=m))
    assert r.toasts == []  # began more than 24 h before the first check: dropped silently
    # first seen within 24 h of the start: once, and never again while it runs
    r2 = Rig(tmp_path / "b", [ev(start=T0 - timedelta(hours=1), minutes=60 * 24 * 10)]) \
        if (tmp_path / "b").mkdir() is None else None
    for d in range(0, 8):
        r2.notifier.check(T0 + timedelta(days=d))
    assert len(r2.toasts) == 1


def test_timezone_change_does_not_retoast(tmp_path):
    r = Rig(tmp_path, [ev()])
    r.notifier.check(T0 - timedelta(minutes=5))
    other = timezone(timedelta(hours=2))
    r.cache.replace_range("a@x.com", T0 - timedelta(days=1), T0 + timedelta(days=1),
                          [ev(start=T0.astimezone(other), minutes=60)])
    r.make().check(T0 - timedelta(minutes=4))
    assert len(r.toasts) == 1


def test_zero_lead_does_not_say_started_early(tmp_path):
    r = Rig(tmp_path, [ev()])
    r.settings.set_lead("primary", 0)
    r.notifier.check(T0 - timedelta(seconds=30))
    assert r.toasts == []
    r.notifier.check(T0)
    assert len(r.toasts) == 1 and r.toasts[0][1].startswith("Started 9:00 AM")


def test_corrupt_notified_json_loads_empty(tmp_path):
    r = Rig(tmp_path, [ev()])
    for junk in ("{not json", "[1,2]", '{"k": "x", "j": [1], "NaN": NaN}'):
        r.fired_path.write_text(junk)
        assert r.make().fired == {}
    r.notifier.check(T0 - timedelta(minutes=5))
    assert len(r.toasts) == 1


def test_canceled_event_not_toasted(tmp_path):
    # A cancelled event is dropped by the client mapping, so it never reaches the
    # cache (and the Notifier never sees it); the live one beside it does toast.
    from test_google_client import FakeService
    from smartcal.google_client import GoogleClient

    def raw(id, status):
        s = T0 + timedelta(minutes=5)
        return {"id": id, "summary": f"Event {id}", "status": status,
                "start": {"dateTime": s.isoformat()},
                "end": {"dateTime": (s + timedelta(hours=1)).isoformat()}}

    svc = FakeService([raw("gone", "cancelled"), raw("live", "confirmed")],
                      calendars=[{"id": "primary", "summary": "Main", "primary": True}])
    client = GoogleClient("a@x.com", service=svc)
    events = client.list_events(T0 - timedelta(days=1), T0 + timedelta(days=1))
    r = Rig(tmp_path, events)
    assert [e.id for e in r.cache.events_between(T0, T0 + timedelta(days=1))] == ["live"]
    r.notifier.check(T0)
    assert [t[0] for t in r.toasts] == ["Event live"]


def test_settings_nan_lead_ignored(tmp_path):
    p = tmp_path / "s.json"
    p.write_text('{"default_lead_minutes": NaN, "lead_minutes": {"a": Infinity, "b": -Infinity, "c": 7}}')
    s = Settings(p)
    assert s.default_lead_minutes == 10
    assert s.lead_for("a") == 10 and s.lead_for("b") == 10 and s.lead_for("c") == 7


def _startup(settings, current=None, frozen=False):
    from smartcal import autostart
    calls = []
    autostart.apply_at_startup(settings, read=lambda: current,
                               enable_fn=lambda: calls.append("enable"),
                               disable_fn=lambda: calls.append("disable"), frozen=frozen)
    return calls


def test_first_run_enables_autostart_once(tmp_path, monkeypatch):
    monkeypatch.delenv("SMARTCAL_NO_SYSTEM_WRITES", raising=False)
    s = Settings(tmp_path / "s.json")
    assert _startup(s) == ["enable"] and Settings(tmp_path / "s.json").first_run_done


def test_autostart_not_reenabled_after_user_disabled(tmp_path, monkeypatch):
    monkeypatch.delenv("SMARTCAL_NO_SYSTEM_WRITES", raising=False)
    s = Settings(tmp_path / "s.json")
    _startup(s)
    s.set_autostart(False)
    assert _startup(Settings(tmp_path / "s.json")) == []


def test_corrupt_settings_does_not_enable_autostart(tmp_path, monkeypatch):
    monkeypatch.delenv("SMARTCAL_NO_SYSTEM_WRITES", raising=False)
    p = tmp_path / "s.json"
    p.write_text("{broken")
    s = Settings(p)
    assert _startup(s) == [] and s.first_run_done and not s.autostart