"""Final-review minor fixes."""
import logging
from datetime import datetime, timedelta

import pytest

from smartcal import autostart
from smartcal.db import Cache
from smartcal.models import CalendarInfo, Event


def _ev(account, id="e1"):
    s = datetime.now().astimezone() + timedelta(hours=1)
    return Event(id, account, account, "Cal", "#1", "T", s, s + timedelta(hours=1))


# -- orphan purge -----------------------------------------------------------------
def test_purge_orphans_drops_rows_of_removed_accounts(tmp_path):
    cache = Cache(tmp_path / "c.db")
    for a in ("keep@x.com", "gone@x.com"):
        cache.upsert_calendars(a, [CalendarInfo(a, a, "#1", True)])
        cache.upsert_events(a, [_ev(a)])
    assert cache.purge_orphans(["keep@x.com"]) == 2
    assert [e.account for e in cache.events_by_id("e1")] == ["keep@x.com"]
    assert list(cache.calendars_by_account()) == ["keep@x.com"]
    cache.purge_orphans([])
    assert cache.events_by_id("e1") == [] and cache.calendars_by_account() == {}


# -- autostart only rewritten when it should be -----------------------------------
@pytest.mark.parametrize("enabled,current,frozen,expected", [
    (True, None, False, "enable"),           # missing: added, even from source
    (True, None, True, "enable"),
    (True, '"Z:\\x\\pythonw.exe" "run.py"', False, None),   # source run never repoints
    (True, '"C:\\old\\SmartCal.exe"', True, "enable"),      # exe moved: fixed
    (True, "WANTED", True, None),            # already right: no write
    (False, None, True, None),
    (False, "WANTED", False, None),
    (False, "WANTED", True, "disable"),
])
def test_startup_action(enabled, current, frozen, expected):
    assert autostart.startup_action(enabled, current, "WANTED", frozen) == expected


def test_source_run_does_not_rewrite_existing_run_key(tmp_path, monkeypatch):
    from smartcal.settings import Settings
    monkeypatch.delenv("SMARTCAL_NO_SYSTEM_WRITES", raising=False)
    s = Settings(tmp_path / "s.json")
    s.first_run_done = True
    calls = []
    autostart.apply_at_startup(s, read=lambda: '"C:\\Apps\\SmartCal.exe"',
                               enable_fn=lambda: calls.append("enable"),
                               disable_fn=lambda: calls.append("disable"), frozen=False)
    assert calls == []


def test_startup_never_touches_registry_with_no_system_writes(tmp_path, monkeypatch):
    from smartcal.settings import Settings
    monkeypatch.setenv("SMARTCAL_NO_SYSTEM_WRITES", "1")
    calls = []
    autostart.apply_at_startup(Settings(tmp_path / "s.json"), read=lambda: calls.append("read"),
                               enable_fn=lambda: calls.append("enable"))
    assert calls == []


# -- uncached delete lookup that needs a new sign-in ------------------------------
def test_uncached_delete_lookup_reauth_says_sign_in(tmp_path):
    from test_assistant import FakeClaude, make, text_msg, tool_msg, tool_results
    from smartcal.google_auth import ReauthRequired
    claude = FakeClaude(tool_msg(("delete_event", {"event_id": "x9", "account": "a@x.com",
                                                   "calendar_id": "a@x.com"})), text_msg("ok"))
    a, clients, *_ = make(tmp_path, claude)

    def boom(event_id, *, calendar_id):
        raise ReauthRequired("a@x.com")
    clients["a@x.com"].get_event = boom
    a.reply("delete x9")
    content = tool_results(claude.messages.calls[1])[0]["content"]
    assert "signed in again" in content and "not found" not in content


# -- logging ------------------------------------------------------------------------
def test_google_api_loggers_quiet_and_unwritable_log_dir_falls_back(tmp_path, monkeypatch):
    from smartcal import logsetup
    blocker = tmp_path / "file"
    blocker.write_text("x")                           # a file where the folder should be
    monkeypatch.setattr(logsetup.tempfile, "gettempdir", lambda: str(tmp_path / "tmp"))
    handler = logsetup.setup_logging(blocker / "sub" / "smartcal.log")
    try:
        assert getattr(handler, "baseFilename", "").endswith("smartcal.log")
        assert str(tmp_path / "tmp") in handler.baseFilename
        for name in ("googleapiclient.discovery", "googleapiclient.http"):
            assert not logging.getLogger(name).isEnabledFor(logging.INFO)
    finally:
        logging.getLogger().removeHandler(handler)
        handler.close()


def test_setup_logging_never_raises(tmp_path, monkeypatch):
    from smartcal import logsetup

    def nope(path):
        raise OSError("read-only")
    monkeypatch.setattr(logsetup, "_file_handler", nope)
    handler = logsetup.setup_logging(tmp_path / "x.log")
    try:
        assert isinstance(handler, logging.NullHandler)
    finally:
        logging.getLogger().removeHandler(handler)


# -- chat workers are freed ---------------------------------------------------------
def test_chat_worker_deleted_after_finish(qtbot):
    from PyQt6.QtCore import QCoreApplication, QEvent
    from test_chat import make, say
    from smartcal.ui.chat import ChatWorker
    w, _ = make(qtbot)
    say(qtbot, w)
    say(qtbot, w)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    assert w.findChildren(ChatWorker) == []


# -- a late destroyed() never clears a newer dialog ---------------------------------
def test_track_dialog_ignores_stale_destroyed(qtbot):
    from PyQt6.QtCore import QCoreApplication, QEvent
    from PyQt6.QtWidgets import QDialog
    from smartcal.ui.tray import track_dialog
    holder = []
    old, new = QDialog(), QDialog()
    track_dialog(holder, old)
    track_dialog(holder, new)
    old.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    assert holder == [new]
    new.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    assert holder == []


def test_reminders_dialog_ref_survives_old_dialog_deletion(qtbot, tmp_path):
    from PyQt6.QtCore import QCoreApplication, QEvent
    from smartcal.reminders import Reminders
    from test_tray import build
    tray, _ = build(qtbot, tmp_path, reminders=Reminders(tmp_path / "r.json"))
    tray.open_reminders()
    first = tray._reminders_dialog
    first.close()                          # WA_DeleteOnClose: deleted later
    tray.open_reminders()
    second = tray._reminders_dialog
    assert second is not first
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    assert tray._reminders_dialog is second
    second.close()


# -- one calendar list per sync ------------------------------------------------------
def test_sync_fetches_the_calendar_list_once(tmp_path):
    from test_google_client import FakeService
    from smartcal.google_client import GoogleClient
    from smartcal.live_sync import LiveSyncWorker
    svc = FakeService([])
    client = GoogleClient("me@x.com", service=svc)
    w = LiveSyncWorker(Cache(tmp_path / "c.db"), lambda a: client, accounts_fn=lambda: [])
    assert w.sync_account("me@x.com") == "ok"
    cal_lists = [c for c in svc.calls if isinstance(c, dict) and "calendarId" not in c]
    assert len(cal_lists) == 1


def test_reminder_late_body_uses_12_hour_clock():
    from smartcal.reminders import ReminderFirer
    from smartcal.agenda import clock_text
    due = datetime(2026, 11, 4, 14, 5).astimezone()

    class R:
        def claim_due(self, now):
            from smartcal.reminders import Reminder
            return [Reminder(1, "Call Sam", due, due)]
    sent = []
    ReminderFirer(R(), lambda *a: sent.append(a)).check(due + timedelta(hours=1))
    assert sent[0][1] == "(was due 2:05 PM) Call Sam" == f"(was due {clock_text(due)}) Call Sam"
