"""v0.12.0 review pass on Settings, meeting toasts and reminders."""
from datetime import date, datetime, timedelta, timezone

from smartcal.notifier import Notifier, WAKE_MIN_MS, next_wake_ms
from smartcal.reminders import MAX_TOASTS_PER_CHECK, ReminderFirer, Reminders, due_text
from smartcal.settings import Settings
from smartcal.ui import theme
from smartcal.ui.reminders_dialog import EMPTY, RemindersDialog, when_text
from smartcal.ui.settings_dialog import DarkCombo, LEAD_AT_START, friendly_date
from test_notifier import T0, Rig, ev
from test_settings_dialog import build

TZ = timezone(timedelta(hours=-5))


# -- Settings dialog ---------------------------------------------------------------------
def test_every_dropdown_is_the_dark_rounded_one(qtbot, tmp_path):
    dlg, *_ = build(qtbot, tmp_path)
    for box in (dlg.model_box, dlg.tile_style_box, dlg.animations_box):
        assert isinstance(box, DarkCombo) and not box.isEditable()
        assert box.styleSheet() == theme.combo_style()
    assert "#ffffff" not in dlg.styleSheet().lower()


def test_lead_zero_reads_at_start_and_range_matches_settings(qtbot, tmp_path):
    dlg, settings, *_ = build(qtbot, tmp_path)
    spin = dlg.default_lead
    assert (spin.minimum(), spin.maximum()) == (0, 1440)
    spin.setValue(0)
    assert spin.text() == LEAD_AT_START
    spin.setValue(5000)
    assert spin.value() == 1440                      # clamped like settings.clamp_lead


def test_mute_and_lead_follow_show_and_mute(qtbot, tmp_path):
    dlg, *_ = build(qtbot, tmp_path)
    show, mute, spin, *_ = dlg._rows["w"]           # "Work & Co": hidden by default
    assert not show.isChecked() and not mute.isEnabled() and not spin.isEnabled()
    show.setChecked(True)
    assert mute.isEnabled() and spin.isEnabled()
    mute.setChecked(True)
    assert mute.isEnabled() and not spin.isEnabled()


def test_rows_on_the_default_follow_it_and_stay_defaults(qtbot, tmp_path):
    _dlg, settings, *_ = build(qtbot, tmp_path)
    settings.set_lead("w", 30)                      # Work has its own time
    dlg, settings, *_ = build(qtbot, tmp_path)
    dlg.default_lead.setValue(15)
    assert dlg._rows["p"][2].value() == 15          # follows the default as it is edited
    assert dlg._rows["w"][2].value() == 30          # its own time is kept
    dlg.accept()
    s2 = Settings(tmp_path / "s.json")
    assert s2.default_lead_minutes == 15 and "p" not in s2.lead_minutes
    assert s2.lead_for("p") == 15 and s2.lead_for("w") == 30


def test_an_edited_row_keeps_its_own_time(qtbot, tmp_path):
    dlg, settings, *_ = build(qtbot, tmp_path)
    dlg._rows["p"][2].setValue(5)
    dlg.default_lead.setValue(20)
    assert dlg._rows["p"][2].value() == 5
    dlg.accept()
    assert Settings(tmp_path / "s.json").lead_minutes == {"p": 5}


def test_remove_needs_a_selected_account(qtbot, tmp_path):
    dlg, *_ = build(qtbot, tmp_path)
    assert not dlg.remove_button.isEnabled()
    dlg.account_list.setCurrentRow(0)
    assert dlg.remove_button.isEnabled()


def test_friendly_dates_and_autostart_wording(qtbot, tmp_path):
    assert friendly_date("2026-11-09", date(2026, 11, 6)) == "Mon, Nov 9"
    assert friendly_date("2027-01-02", date(2026, 11, 6)) == "Sat, Jan 2, 2027"
    assert friendly_date("bad") == "bad"
    dlg, *_ = build(qtbot, tmp_path)
    assert dlg.autostart_box.text() == "Start SmartCal when I sign in to Windows"


# -- Reminders dialog -------------------------------------------------------------------
def test_when_text():
    now = datetime(2026, 11, 6, 15, 0, tzinfo=TZ)
    assert when_text(now.replace(hour=16, minute=5), now) == "Today 4:05 PM"
    assert when_text(now + timedelta(days=1), now) == "Tomorrow 3:00 PM"
    assert when_text(datetime(2026, 11, 9, 9, tzinfo=TZ), now) == "Mon, Nov 9 9:00 AM"
    assert when_text(datetime(2027, 1, 4, 9, tzinfo=TZ), now) == "Mon, Jan 4, 2027 9:00 AM"


def test_reminders_dialog_empty_state_and_hint(qtbot, tmp_path):
    dlg = RemindersDialog(Reminders(tmp_path / "r.json"))
    qtbot.addWidget(dlg)
    assert [w.text() for w in dlg.rows()] == [EMPTY]
    assert "Ask Claude" in dlg.hint.text() and "remind me" in dlg.hint.text()


def test_tray_refreshes_an_open_list_after_chat(qtbot, tmp_path):
    from test_tray import build as build_tray
    r = Reminders(tmp_path / "r.json")
    tray, _ = build_tray(qtbot, tmp_path, reminders=r)
    tray.open_reminders()
    dlg = tray._reminders_dialog
    assert len(dlg.rows()) == 1 and not hasattr(dlg.rows()[0], "button")
    r.add("Call Sam", datetime.now().astimezone() + timedelta(minutes=20))
    tray.refresh_reminders()                         # run.py: after every chat change
    assert [w.label.text() for w in dlg.rows()] == ["Call Sam"]
    dlg.close()


# -- meeting toasts ---------------------------------------------------------------------
def test_no_toast_for_hidden_declined_cancelled_all_day_or_muted(tmp_path):
    events = [ev("hidden"), ev("declined", declined=True), ev("allday", all_day=True),
              ev("muted", cal="work"), ev("ok")]
    r = Rig(tmp_path, events)
    r.settings.hide_event(ev("hidden"))
    r.settings.set_muted("work", True)
    for m in range(15, -5, -1):
        r.notifier.check(T0 - timedelta(minutes=m))
    assert [t[0] for t in r.toasts] == ["Event ok"]
    # hidden account: nothing at all
    r2 = Rig(tmp_path / "b", [ev("acct")]) if (tmp_path / "b").mkdir() is None else None
    r2.settings.set_account_hidden("a@x.com", True)
    r2.notifier.check(T0 - timedelta(minutes=5))
    assert r2.toasts == [] and r2.notifier.next_fire_at(T0 - timedelta(minutes=5)) is None


def test_next_fire_at_follows_lead_time_mute_and_fired(tmp_path):
    r = Rig(tmp_path, [ev("a"), ev("b", cal="work", start=T0 + timedelta(minutes=30))])
    now = T0 - timedelta(minutes=30)
    assert r.notifier.next_fire_at(now) == T0 - timedelta(minutes=10)   # default lead
    r.settings.set_lead("work", 60)                 # applies at once, nothing to reschedule
    assert r.notifier.next_fire_at(now) == now      # b is due now (start - 60 min)
    r.notifier.check(now)
    assert [t[0] for t in r.toasts] == ["Event b"]
    assert r.notifier.next_fire_at(now) == T0 - timedelta(minutes=10)   # b is done
    r.settings.set_muted("primary", True)
    assert r.notifier.next_fire_at(now) is None


def test_next_wake_ms():
    now = datetime(2026, 11, 6, 9, tzinfo=TZ)
    assert next_wake_ms(now, []) is None and next_wake_ms(now, [None]) is None
    assert next_wake_ms(now, [now + timedelta(seconds=40)]) is None   # the 30 s tick
    assert next_wake_ms(now, [now + timedelta(seconds=12), None]) == 12_050
    assert next_wake_ms(now, [now - timedelta(seconds=5)]) == WAKE_MIN_MS


def test_toast_is_on_time_with_the_precise_wake(tmp_path):
    """The check at the moment next_fire_at gives sends the toast (not up to 30 s late)."""
    r = Rig(tmp_path, [ev()])
    now = T0 - timedelta(minutes=10, seconds=20)
    due = r.notifier.next_fire_at(now)
    assert due == T0 - timedelta(minutes=10)
    r.notifier.check(due)
    assert len(r.toasts) == 1 and r.toasts[0][1].startswith("Starts in 10 min")


def test_lead_change_reaches_an_already_due_meeting(tmp_path):
    r = Rig(tmp_path, [ev()])
    r.notifier.check(T0 - timedelta(minutes=12))
    assert r.toasts == []
    r.settings.set_lead("primary", 15)             # Settings OK: run.py checks at once
    r.notifier.check(T0 - timedelta(minutes=12))
    assert len(r.toasts) == 1 and r.toasts[0][1].startswith("Starts in 12 min")


# -- reminders --------------------------------------------------------------------------
def test_reminder_next_fire_at(tmp_path):
    r = Reminders(tmp_path / "r.json", clock=lambda: T0)
    assert r.next_fire_at() is None
    a = r.add("a", T0 + timedelta(minutes=20))
    r.add("b", T0 + timedelta(minutes=5))
    assert r.next_fire_at() == T0 + timedelta(minutes=5)
    r.claim_due(T0 + timedelta(minutes=6))
    assert r.next_fire_at() == T0 + timedelta(minutes=20)
    r.cancel(a)
    assert r.next_fire_at() is None


def test_many_overdue_reminders_send_few_toasts(tmp_path):
    r = Reminders(tmp_path / "r.json", clock=lambda: T0)
    for k in range(8):
        r.add(f"Task {k}", T0 + timedelta(minutes=k))
    sent = []
    ReminderFirer(r, lambda *a: sent.append(a)).check(T0 + timedelta(days=3))
    assert len(sent) == MAX_TOASTS_PER_CHECK
    assert [s[0] for s in sent] == ["Reminder", "Reminder", "Reminders"]
    assert sent[2][1].startswith("6 more were due: Task 2; Task 3; Task 4; …")
    assert r.list() == [] and r.due(T0 + timedelta(days=3)) == []    # all claimed once
    # exactly the cap: each its own toast
    r2 = Reminders(tmp_path / "r2.json", clock=lambda: T0)
    for k in range(MAX_TOASTS_PER_CHECK):
        r2.add(f"T{k}", T0)
    sent.clear()
    ReminderFirer(r2, lambda *a: sent.append(a)).check(T0)
    assert [s[1] for s in sent] == ["T0", "T1", "T2"]


def test_overdue_text_says_which_day(tmp_path):
    now = datetime(2026, 12, 10, 15, 0, tzinfo=TZ)     # (no DST change in between)
    assert due_text(now - timedelta(hours=2), now) == "1:00 PM"
    assert due_text(now - timedelta(days=2), now) == "Tue 3:00 PM"
    assert due_text(now - timedelta(days=9), now) == "Dec 1, 3:00 PM"
    r = Reminders(tmp_path / "r.json", clock=lambda: now)
    r.add("Call Sam", now - timedelta(days=1))
    sent = []
    ReminderFirer(r, lambda *a: sent.append(a)).check(now)
    assert sent == [("Reminder", "(was due Wed 3:00 PM) Call Sam", None)]


def test_reminders_survive_restart_across_dst(tmp_path):
    """Stored as absolute times: the fall-back night does not move a reminder."""
    from zoneinfo import ZoneInfo
    ny = ZoneInfo("America/New_York")
    before = datetime(2026, 11, 1, 1, 30, tzinfo=ny)        # EDT, before the change
    r = Reminders(tmp_path / "r.json", clock=lambda: before)
    r.add("Check the oven", before + timedelta(hours=1))   # 1:30 EST (the second 1:30)
    r2 = Reminders(tmp_path / "r.json")
    fire = r2.list()[0].fire_at
    assert fire == before + timedelta(hours=1)
    assert r2.due(before + timedelta(minutes=59)) == []
    assert len(r2.due(before + timedelta(hours=1))) == 1


def test_notifier_dedupes_across_restart(tmp_path):
    r = Rig(tmp_path, [ev()])
    r.notifier.check(T0 - timedelta(minutes=10))
    n2 = Notifier(r.cache, r.settings, lambda *a: r.toasts.append(a),
                  fired_path=r.fired_path)
    n2.check(T0 - timedelta(minutes=9))
    assert len(r.toasts) == 1
