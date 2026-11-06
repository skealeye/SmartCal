"""I3 (a calendar shared between two accounts counts once) and I4 (declined meetings)."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fakes import FakeClient
from smartcal import agenda
from smartcal.actions import Actions
from smartcal.db import Cache
from smartcal.models import CalendarInfo, Event
from smartcal.notifier import Notifier
from smartcal.settings import Settings
from smartcal.undo import UndoStore

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 4, 9, 0, tzinfo=NY)
P, W = "p@x.com", "w@x.com"   # personal and work accounts


def ev(account, cal, id="e1", start=None, minutes=60, **kw):
    start = start or NOW + timedelta(hours=2)
    return Event(id, account, cal, "Cal " + cal, "#123", f"Event {id}", start,
                 start + timedelta(minutes=minutes), **kw)


def shared_cache(tmp_path, events):
    """The personal calendar P is also subscribed (non-primary) in the work account."""
    cache = Cache(tmp_path / "c.db")
    cache.upsert_calendars(P, [CalendarInfo(P, "Personal", "#1", True)])
    cache.upsert_calendars(W, [CalendarInfo(W, "Work", "#2", True),
                               CalendarInfo(P, "Personal (shared)", "#3", False)])
    for e in events:
        cache.upsert_events(e.account, [e])
    return cache


def both_copies(**kw):
    return [ev(P, P, **kw), ev(W, P, **kw)]


# -- I3 ------------------------------------------------------------------------------
def test_shared_calendar_event_appears_once_from_the_owning_account(tmp_path):
    cache = shared_cache(tmp_path, both_copies())
    s = Settings(tmp_path / "s.json")
    got = agenda.today(cache, s, NOW)
    assert [(e.account, e.id) for e in got] == [(P, "e1")]
    assert agenda.next_event(cache, s, NOW).account == P


def test_primary_is_computed_per_account(tmp_path):
    cache = shared_cache(tmp_path, [])
    assert agenda.primary_ids(cache) == {(P, P), (W, W)}
    assert agenda.calendar_owners(cache) == {P: P, W: W}


def test_shared_calendar_owned_by_neither_account_uses_first_alphabetically(tmp_path):
    cache = Cache(tmp_path / "c.db")
    cache.upsert_calendars(W, [CalendarInfo("team", "Team", "#1", False)])
    cache.upsert_calendars(P, [CalendarInfo("team", "Team", "#1", False)])
    assert agenda.calendar_owners(cache) == {"team": P}
    s = Settings(tmp_path / "s.json")
    s.set_hidden("team", False)
    cache.upsert_events(W, [ev(W, "team")])
    cache.upsert_events(P, [ev(P, "team")])
    assert [e.account for e in agenda.today(cache, s, NOW)] == [P]


def test_shared_calendar_toasts_once(tmp_path):
    cache = shared_cache(tmp_path, both_copies(start=NOW + timedelta(minutes=5)))
    toasts = []
    n = Notifier(cache, Settings(tmp_path / "s.json"), lambda *a: toasts.append(a),
                 fired_path=tmp_path / "n.json")
    n.check(NOW)
    n.check(NOW + timedelta(minutes=1))
    assert len(toasts) == 1


def test_unticking_the_shared_calendar_hides_it(qtbot, tmp_path):
    from PyQt6.QtWidgets import QLabel
    from smartcal.ui.settings_dialog import SettingsDialog
    cache = shared_cache(tmp_path, both_copies())
    s = Settings(tmp_path / "s.json")
    dlg = SettingsDialog(s, cache, lambda: [P, W], lambda: None, lambda a: None)
    qtbot.addWidget(dlg)
    assert sorted(dlg._rows) == [P, W]            # one row for the shared calendar
    show = dlg._rows[P][0]
    assert show.isChecked()
    notes = [lab.text() for lab in dlg.findChildren(QLabel)
             if "same calendar as in" in lab.text()]
    assert notes == [f"Personal (shared) (same calendar as in {P})"]
    show.setChecked(False)
    dlg.accept()
    assert agenda.today(cache, s, NOW) == []


def test_deduped_shared_event_is_not_ambiguous(tmp_path):
    cache = shared_cache(tmp_path, both_copies())
    clients = {P: FakeClient(P, [ev(P, P)]), W: FakeClient(W, [ev(W, P)])}
    act = Actions(lambda a: clients[a], cache, UndoStore(tmp_path / "u.json"),
                  Settings(tmp_path / "s.json"))
    assert act.locate("e1") == (P, P)
    moved = act.move("e1", NOW + timedelta(hours=5), NOW + timedelta(hours=6))
    assert moved.account == P and clients[P].log and not clients[W].log


def test_free_slots_counts_a_shared_meeting_once_and_still_busy(tmp_path):
    start = datetime(2026, 11, 5, 10, tzinfo=NY)
    cache = shared_cache(tmp_path, both_copies(start=start))
    act = Actions(lambda a: None, cache, UndoStore(tmp_path / "u.json"),
                  Settings(tmp_path / "s.json"))
    slots = act.free_slots(date(2026, 11, 5), timedelta(minutes=30), now=NOW)
    assert slots[0] == (datetime(2026, 11, 5, 9, tzinfo=NY), start)


# -- I4 ------------------------------------------------------------------------------
def _rig(tmp_path, events):
    cache = Cache(tmp_path / "c.db")
    cache.upsert_calendars(P, [CalendarInfo(P, "Personal", "#1", True)])
    cache.upsert_events(P, events)
    return cache, Settings(tmp_path / "s.json")


def test_client_maps_own_declined_response():
    from test_google_client import _client, _ev, _range
    raws = [_ev(id="no", attendees=[{"email": "me@x.com", "self": True,
                                     "responseStatus": "declined"},
                                    {"email": "o@y.com", "responseStatus": "accepted"}]),
            _ev(id="yes", attendees=[{"email": "me@x.com", "self": True,
                                      "responseStatus": "accepted"},
                                     {"email": "o@y.com", "responseStatus": "declined"}]),
            _ev(id="plain")]
    got = {e.id: e.declined for e in _client(raws).list_events(*_range())}
    assert got == {"no": True, "yes": False, "plain": False}


def test_declined_survives_cache_roundtrip_and_old_db_migrates(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)   # a v0.2.4 database: no declined column
    con.execute("CREATE TABLE events (account TEXT NOT NULL, calendar_id TEXT NOT NULL, "
                "id TEXT NOT NULL, calendar_name TEXT NOT NULL, color TEXT NOT NULL, "
                "title TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL, "
                "start_ts REAL NOT NULL, end_ts REAL NOT NULL, all_day INTEGER NOT NULL, "
                "location TEXT NOT NULL, description TEXT NOT NULL, meeting_url TEXT, "
                "recurring_id TEXT, busy INTEGER NOT NULL DEFAULT 1, "
                "PRIMARY KEY (account, calendar_id, id))")
    con.commit()
    con.close()
    cache = Cache(path)
    cache.upsert_events(P, [ev(P, P, "d", declined=True), ev(P, P, "a")])
    got = {e.id: e.declined for e in cache.events_by_id("d") + cache.events_by_id("a")}
    assert got == {"d": True, "a": False}


def test_declined_meeting_hidden_from_agenda_and_next_event(tmp_path):
    cache, s = _rig(tmp_path, [ev(P, P, "d", declined=True),
                               ev(P, P, "a", start=NOW + timedelta(hours=4))])
    assert [e.id for e in agenda.today(cache, s, NOW)] == ["a"]
    assert agenda.next_event(cache, s, NOW).id == "a"


def test_declined_meeting_never_toasts(tmp_path):
    cache, s = _rig(tmp_path, [ev(P, P, "d", start=NOW + timedelta(minutes=5), declined=True)])
    toasts = []
    Notifier(cache, s, lambda *a: toasts.append(a), fired_path=tmp_path / "n.json").check(NOW)
    assert toasts == []


def test_declined_meeting_is_not_busy_for_free_slots(tmp_path):
    day = date(2026, 11, 5)
    cache, s = _rig(tmp_path, [ev(P, P, "d", start=datetime(2026, 11, 5, 9, tzinfo=NY),
                                  minutes=8 * 60, declined=True)])
    act = Actions(lambda a: None, cache, UndoStore(tmp_path / "u.json"), s)
    assert act.free_slots(day, timedelta(hours=1), now=NOW) == [
        (datetime(2026, 11, 5, 9, tzinfo=NY), datetime(2026, 11, 5, 17, tzinfo=NY))]


def test_window_shows_declined_meeting_dimmed_and_struck_through(qtbot, tmp_path):
    from test_window import ev as wev
    from test_window import make
    d = wev("d", "Skipped", datetime(2026, 11, 4, 10, tzinfo=NY),
            datetime(2026, 11, 4, 11, tzinfo=NY), declined=True)
    a = wev("a", "Kept", datetime(2026, 11, 4, 12, tzinfo=NY),
            datetime(2026, 11, 4, 13, tzinfo=NY))
    w = make(qtbot, tmp_path, [d, a], "day")
    chips = {c.event.id: c for c in w.time_view.columns[0].chips}
    assert set(chips) == {"d", "a"}
    assert chips["d"].declined and chips["d"].font().strikeOut()
    # v0.11.0: dimmed as it is painted (no opacity effect: the tile can still build in)
    from smartcal.ui.window import DECLINED_OPACITY
    assert chips["d"].opacity() == DECLINED_OPACITY and chips["a"].opacity() == 1.0
    assert not chips["a"].declined and not chips["a"].font().strikeOut()
    w.set_view("month")
    cell = w.month_view.cells[date(2026, 11, 4)]
    assert [c.declined for c in cell.chips] == [True, False]


def test_assistant_lists_declined_meetings_marked(tmp_path):
    from test_assistant import FakeClaude, make, text_msg, tool_msg, tool_results
    from test_assistant import ev as aev
    claude = FakeClaude(tool_msg(("list_events", {"start": "2026-11-05", "end": "2026-11-06"})),
                        text_msg("ok"))
    a, *_ = make(tmp_path, claude, [aev(declined=True)])
    a.reply("what's tomorrow")
    content = tool_results(claude.messages.calls[1])[0]["content"]
    assert '"declined":true' in content
    assert "declined by the owner" in a.calendar_block(NOW)
