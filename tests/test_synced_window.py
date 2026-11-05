"""The assistant trusts only the window each account actually synced (I1), and the
sync window keeps the whole current month grid (I2)."""
import sqlite3
from datetime import date, datetime, timedelta

from test_assistant import NOW, NY, FakeClaude, ev, make, text_msg, tool_msg, tool_results
from test_window import make as make_window

from fakes import cal
from smartcal import agenda, sync
from smartcal.db import Cache
from smartcal.live_sync import LiveSyncWorker


def free(day):
    return ("find_free_slots", {"date": day, "duration_minutes": 60})


def run(a, claude):
    a.reply("go")
    return tool_results(claude.messages.calls[1])


class SyncClient:
    def __init__(self, account, error=None):
        self.account, self.error = account, error

    def list_calendars(self):
        if self.error:
            raise self.error
        return [cal(self.account)]

    def list_events(self, start, end, calendars=None):
        return []


def sync_worker(cache, client, clock):
    w = LiveSyncWorker(cache, lambda a: client, accounts_fn=lambda: [client.account])
    w.clock = clock
    return w


# -- I1 ------------------------------------------------------------------------------
def test_free_slots_refused_when_account_never_synced(tmp_path):
    claude = FakeClaude(tool_msg(free("2026-12-15"), free("2026-11-05"),
                                 ("list_events", {"start": "2026-11-05", "end": "2026-11-06"})),
                        text_msg("ok"))
    a, clients, cache, _ = make(tmp_path, claude, [ev()], accounts=("a@x.com", "b@x.com"),
                                synced=False)
    lo, hi = sync.sync_window(NOW)
    cache.record_sync("a@x.com", lo, hi, NOW)          # b@x.com has never synced
    assert cache.synced_window(["a@x.com", "b@x.com"]) is None
    results = run(a, claude)
    assert len(results) == 3
    for r in results:
        assert r.get("is_error") and "b@x.com hasn't finished syncing yet" in r["content"]
        assert '"free"' not in r["content"]


def test_range_uses_narrowest_account_window(tmp_path):
    claude = FakeClaude(tool_msg(free("2026-12-15"), free("2026-11-20"), free("2026-11-21"),
                                 ("list_events", {"start": "2026-11-15", "end": "2026-11-25"})),
                        text_msg("ok"))
    a, _, cache, _ = make(tmp_path, claude, [ev()], accounts=("a@x.com", "b@x.com"))
    lo, _hi = sync.sync_window(NOW)
    narrow_end = agenda.midnight(date(2026, 11, 21), NOW)   # b's last good sync was long ago
    cache.record_sync("b@x.com", lo - timedelta(days=30), narrow_end, NOW - timedelta(days=40))
    assert cache.synced_window(["a@x.com", "b@x.com"]) == (lo, narrow_end)
    assert cache.synced_window(["a@x.com"]) == sync.sync_window(NOW)
    dec15, nov20, nov21, listed = run(a, claude)
    assert dec15["is_error"] and "outside the synced range (2026-10-28..2026-11-20)" in dec15["content"]
    assert not nov20.get("is_error") and '"free":[[' in nov20["content"]
    assert nov21["is_error"] and "2026-10-28..2026-11-20" in nov21["content"]
    assert listed["is_error"]


def test_failed_sync_does_not_advance_window(tmp_path):
    from smartcal.google_auth import ReauthRequired
    cache = Cache(tmp_path / "c.db")
    t1 = datetime(2026, 11, 4, 9, tzinfo=NY)
    assert sync_worker(cache, SyncClient("a"), lambda: t1).sync_account("a") == "ok"
    assert cache.synced_window(["a"]) == sync.sync_window(t1)
    t2 = t1 + timedelta(days=10)
    for err in (OSError("offline"), ReauthRequired("a"), ValueError("boom")):
        result = sync_worker(cache, SyncClient("a", err), lambda: t2).sync_account("a")
        assert result != "ok"
        assert cache.synced_window(["a"]) == sync.sync_window(t1)
    assert sync_worker(cache, SyncClient("a"), lambda: t2).sync_account("a") == "ok"
    assert cache.synced_window(["a"]) == sync.sync_window(t2)


def test_old_cache_without_sync_state_treated_as_unsynced_until_first_sync(tmp_path):
    # A v0.2.1 cache: events + calendars, no sync_state table at all.
    Cache(tmp_path / "c.db")
    with sqlite3.connect(tmp_path / "c.db") as con:
        con.execute("DROP TABLE sync_state")
    claude = FakeClaude(tool_msg(free("2026-11-05")), text_msg("ok"))
    a, _, cache, _ = make(tmp_path, claude, [ev()], synced=False)   # migrates the old file
    first = run(a, claude)[0]
    assert first["is_error"] and "a@x.com hasn't finished syncing yet" in first["content"]
    assert "hasn't finished syncing yet" in a.system_prompt(NOW)
    assert sync_worker(cache, SyncClient("a@x.com"), lambda: NOW).sync_account("a@x.com") == "ok"
    claude.then(tool_msg(free("2026-11-05")), text_msg("ok"))
    a.reply("again")
    again = tool_results(claude.messages.calls[-1])[0]
    assert not again.get("is_error") and '"free":[[' in again["content"]


def test_prompt_states_actual_window(tmp_path):
    a, _, cache, _ = make(tmp_path / "1", FakeClaude(text_msg("hi")),
                          accounts=("a@x.com", "b@x.com"))
    lo, _ = sync.sync_window(NOW)
    cache.record_sync("b@x.com", lo, agenda.midnight(date(2026, 11, 21), NOW), NOW)
    prompt = a.system_prompt(NOW)
    assert "events from 2026-10-28 to 2026-11-20" in prompt
    assert "never assume they are free" in prompt
    none, *_ = make(tmp_path / "2", FakeClaude(text_msg("hi")), accounts=())
    assert "no calendars synced yet" in none.system_prompt(NOW)


def test_update_end_exactly_at_window_end_is_allowed(tmp_path):
    claude = FakeClaude(tool_msg(
        ("update_event", {"event_id": "e1", "fields": {"end": "2027-01-04"}}),
        ("update_event", {"event_id": "e1", "fields": {"start": "2027-01-04"}})),
        text_msg("ok"))
    a, *_ = make(tmp_path, claude, [ev()])
    end_ok, start_bad = run(a, claude)
    assert "outside the synced range" not in end_ok["content"]
    assert start_bad["is_error"] and "outside the synced range" in start_bad["content"]


# -- I2 ------------------------------------------------------------------------------
def test_sync_window_covers_current_month_grid_late_in_month(tmp_path):
    from smartcal.ui.layout import month_grid
    late = datetime(2026, 11, 25, 9, tzinfo=NY)          # Nov 2026 grid starts Sun Nov 1
    first, last = sync.synced_days(late)
    assert first == month_grid(2026, 11)[0] == date(2026, 11, 1)
    assert last == date(2026, 11, 25) + timedelta(days=60)
    assert sync.sync_window(late)[0] == agenda.midnight(date(2026, 11, 1), late)
    early = datetime(2026, 11, 4, 9, tzinfo=NY)          # a week back is still earlier
    assert sync.synced_days(early)[0] == date(2026, 10, 28)
    # Sync and prune use that same window: rows from Nov 2 survive a sync on Nov 25.
    cache = Cache(tmp_path / "c.db")
    kept = ev(account="a", start=datetime(2026, 11, 2, 10, tzinfo=NY))
    gone = ev(account="a", id="old", start=datetime(2026, 10, 30, 10, tzinfo=NY))
    cache.upsert_events("a", [kept, gone])
    seen = []

    class Recording(SyncClient):
        def list_events(self, start, end, calendars=None):
            seen.append((start, end))
            return [kept]

    assert sync_worker(cache, Recording("a"), lambda: late).sync_account("a") == "ok"
    assert seen == [sync.sync_window(late)]
    ids = {e.id for e in cache.events_between(late - timedelta(days=90), late)}
    assert ids == {"e1"}


def test_month_view_late_in_month_has_no_banner_for_current_month(qtbot, tmp_path):
    w = make_window(qtbot, tmp_path, [], "month")
    w.now = lambda: datetime(2026, 11, 25, 9, tzinfo=NY)
    w.go_to(date(2026, 11, 25))
    assert not w.unsynced_shown()
    w.go_to(date(2026, 10, 15))                          # last month is still not synced
    assert w.unsynced_shown()


# -- connected accounts (token files) count as active -------------------------------
def test_new_account_without_sync_blocks_free_slots(tmp_path):
    claude = FakeClaude(tool_msg(free("2026-11-05")), text_msg("ok"))
    # new@x.com was just added: token file, no calendars cached, no sync yet.
    a, _, cache, _ = make(tmp_path, claude, [ev()], tokens=("a@x.com", "new@x.com"))
    res = run(a, claude)[0]
    assert res["is_error"] and "new@x.com hasn't finished syncing yet" in res["content"]
    assert "new@x.com hasn't finished syncing yet" in a.system_prompt(NOW)


def test_removed_account_does_not_block(tmp_path):
    claude = FakeClaude(tool_msg(free("2026-11-05")), text_msg("ok"))
    # gone@x.com still has cached calendars (e.g. rows a sync in flight re-added)
    # but no token and no sync state: it was removed and must not block.
    a, _, cache, _ = make(tmp_path, claude, [ev()], accounts=("a@x.com", "gone@x.com"),
                          tokens=("a@x.com",), synced=False)
    lo, hi = sync.sync_window(NOW)
    cache.record_sync("a@x.com", lo, hi, NOW)
    res = run(a, claude)[0]
    assert not res.get("is_error") and '"free":[[' in res["content"]
    assert "events from 2026-10-28 to 2027-01-03" in a.system_prompt(NOW)


def test_token_only_account_counts_as_active(tmp_path):
    # b@x.com's only calendar is hidden, so it has no shown calendars; it is still
    # connected, so its sync must have finished before dates are answered.
    claude = FakeClaude(tool_msg(free("2026-11-05")), text_msg("ok"))
    a, _, cache, _ = make(tmp_path, claude, [ev()], accounts=("a@x.com", "b@x.com"),
                          hidden=("b@x.com",), synced=False)
    lo, hi = sync.sync_window(NOW)
    cache.record_sync("a@x.com", lo, hi, NOW)
    assert a._sync_accounts() == ["a@x.com", "b@x.com"]
    res = run(a, claude)[0]
    assert res["is_error"] and "b@x.com hasn't finished syncing yet" in res["content"]
    cache.record_sync("b@x.com", lo, hi, NOW)
    claude.then(tool_msg(free("2026-11-05")), text_msg("ok"))
    a.reply("again")
    assert '"free":[[' in tool_results(claude.messages.calls[-1])[0]["content"]
