from datetime import datetime, timedelta, timezone

from smartcal import google_auth
from smartcal.db import Cache
from smartcal.models import CalendarInfo, Event
from smartcal.settings import Settings
from smartcal.ui.settings_dialog import SettingsDialog

T = datetime(2026, 11, 4, 9, tzinfo=timezone(timedelta(hours=-5)))


def build(qtbot, tmp_path):
    cache = Cache(tmp_path / "c.db")
    cache.upsert_calendars("a@x.com", [CalendarInfo("p", "Main", "#111", True),
                                       CalendarInfo("w", "Work & Co", "#222", False)])
    settings = Settings(tmp_path / "s.json")
    accounts, removed = ["a@x.com"], []
    dlg = SettingsDialog(settings, cache, lambda: list(accounts), lambda: None,
                         lambda acct: (removed.append(acct), accounts.remove(acct)))
    qtbot.addWidget(dlg)
    return dlg, settings, cache, accounts, removed


def test_dialog_changes_persist_on_ok(qtbot, tmp_path):
    dlg, settings, *_ = build(qtbot, tmp_path)
    show_w, mute_w, spin_w, *_ = dlg._rows["w"]
    assert not show_w.isChecked()  # non-primary defaults hidden
    show_w.setChecked(True)
    mute_w.setChecked(True)
    spin_w.setValue(25)
    dlg._rows["p"][1].setChecked(True)
    dlg.autostart_box.setChecked(False)
    dlg.model_edit.setText("claude-x")
    dlg.default_lead.setValue(12)
    with qtbot.waitSignal(dlg.applied):
        dlg.accept()
    s2 = Settings(tmp_path / "s.json")
    assert not s2.is_hidden("w", False) and s2.is_muted("w") and s2.is_muted("p")
    assert s2.lead_for("w") == 25 and s2.lead_for("p") == 12  # untouched follows default
    assert s2.autostart is False and s2.claude_model == "claude-x"


def test_dialog_cancel_changes_nothing(qtbot, tmp_path):
    dlg, settings, *_ = build(qtbot, tmp_path)
    dlg._rows["w"][0].setChecked(True)
    dlg._rows["w"][1].setChecked(True)
    dlg._rows["w"][2].setValue(99)
    dlg.autostart_box.setChecked(False)
    dlg.reject()
    assert not (tmp_path / "s.json").exists()
    assert settings.is_hidden("w", False) and not settings.muted and settings.autostart is True


def test_dialog_remove_account_asks_confirmation(qtbot, tmp_path):
    dlg, settings, cache, accounts, removed = build(qtbot, tmp_path)
    dlg.account_list.setCurrentRow(0)
    dlg.confirm = lambda a: False
    dlg.remove_selected()
    assert removed == [] and dlg.account_list.count() == 1
    dlg.confirm = lambda a: True
    dlg.remove_selected()
    assert removed == ["a@x.com"] and dlg.account_list.count() == 0


def test_remove_account_deletes_token_and_cache_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    cache = Cache(tmp_path / "c.db")
    for acct in ("a@x.com", "b@x.com"):
        cache.upsert_calendars(acct, [CalendarInfo("c-" + acct, "C", "#1", True)])
        cache.upsert_events(acct, [Event("e", acct, "c-" + acct, "C", "#1", "t", T,
                                         T + timedelta(hours=1))])
        google_auth.token_path(acct).write_text("{}")
    google_auth.remove_account("a@x.com", cache)
    assert not google_auth.token_path("a@x.com").exists()
    assert google_auth.list_accounts() == ["b@x.com"]
    assert list(cache.calendars_by_account()) == ["b@x.com"]
    got = cache.events_between(T - timedelta(days=1), T + timedelta(days=1))
    assert [e.account for e in got] == ["b@x.com"]
    google_auth.remove_account("a@x.com", cache)  # already gone: no error
