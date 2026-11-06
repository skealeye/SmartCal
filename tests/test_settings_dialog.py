from datetime import datetime, timedelta, timezone

import pytest

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
    dlg.model_box.set_model_id("claude-haiku-5-5")
    dlg.default_lead.setValue(12)
    with qtbot.waitSignal(dlg.applied):
        dlg.accept()
    s2 = Settings(tmp_path / "s.json")
    assert not s2.is_hidden("w", False) and s2.is_muted("w") and s2.is_muted("p")
    assert s2.lead_for("w") == 25 and s2.lead_for("p") == 12  # untouched follows default
    assert s2.autostart is False and s2.claude_model == "claude-haiku-5-5"


# -- v0.10.0: the Claude model dropdown ----------------------------------------------------
def test_model_dropdown_lists_known_models_with_ids(qtbot, tmp_path):
    from smartcal.settings import CLAUDE_MODELS
    dlg, settings, *_ = build(qtbot, tmp_path)
    box = dlg.model_box
    assert not box.isEditable()
    items = [(box.itemData(i), box.itemText(i)) for i in range(box.count())]
    assert items == list(CLAUDE_MODELS)
    assert items == [("claude-fable-5-1", "Fable 5.1"), ("claude-opus-5-5", "Opus 5.5"),
                     ("claude-sonnet-5-5", "Sonnet 5.5"), ("claude-haiku-5-5", "Haiku 5.5")]
    from PyQt6.QtCore import Qt
    assert box.itemData(1, Qt.ItemDataRole.ToolTipRole) == "claude-opus-5-5"
    assert box.model_id() == "claude-sonnet-5-5" == settings.claude_model   # the default
    assert box.currentText() == "Sonnet 5.5" and box.toolTip() == "claude-sonnet-5-5"
    assert "QAbstractItemView" in box.styleSheet() and "#ffffff" not in box.styleSheet()


def test_model_dropdown_keeps_a_custom_id(qtbot, tmp_path):
    settings = Settings(tmp_path / "s.json")
    settings.set_claude_model("claude-old-4")
    dlg, s, *_ = build(qtbot, tmp_path)
    box = dlg.model_box
    assert box.count() == 5 and box.currentText() == "Custom: claude-old-4"
    assert box.model_id() == "claude-old-4"
    dlg.accept()                                    # untouched: nothing silently changed
    assert Settings(tmp_path / "s.json").claude_model == "claude-old-4"
    dlg2, *_ = build(qtbot, tmp_path)
    dlg2.model_box.set_model_id("claude-opus-5-5")
    dlg2.accept()
    assert Settings(tmp_path / "s.json").claude_model == "claude-opus-5-5"


def test_model_choices_helper():
    from smartcal.settings import CLAUDE_MODELS, claude_model_choices
    assert claude_model_choices("claude-opus-5-5") == list(CLAUDE_MODELS)
    assert claude_model_choices("") == list(CLAUDE_MODELS)
    assert claude_model_choices(" x-1 ")[-1] == ("x-1", "Custom: x-1")


@pytest.mark.parametrize("mid", ["claude-fable-5-1", "claude-old-4"])
def test_assistant_uses_the_selected_model(tmp_path, mid):
    from test_assistant import FakeClaude, make, text_msg
    Settings(tmp_path / "s.json").set_claude_model(mid)   # what the dialog saves
    claude = FakeClaude(text_msg("Hi."))
    a, *_ = make(tmp_path, claude)
    a.reply("hello")
    assert claude.messages.calls[0]["model"] == mid


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


def test_dialog_save_error_keeps_dialog_open_and_unapplied(qtbot, tmp_path, monkeypatch):
    dlg, settings, *_ = build(qtbot, tmp_path)
    warnings = []
    dlg._warn = lambda t, m: warnings.append(m)
    dlg._rows["p"][1].setChecked(True)
    dlg.autostart_box.setChecked(False)
    dlg.default_lead.setValue(30)
    monkeypatch.setattr(settings, "save", lambda: (_ for _ in ()).throw(OSError("disk full")))
    dlg.show()
    dlg.accept()
    assert dlg.isVisible() and warnings and "disk full" in warnings[0]
    assert not settings.muted and settings.autostart is True and settings.default_lead_minutes == 10
