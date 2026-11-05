"""v0.7.0 quick actions: the popup list never collapses after a refresh (the "snaps down
to a single meeting" bug), account visibility, no email lines, right-click Hide/Color
menus, the notice's Undo, and the tray's Restore hidden menu. Offscreen Qt, real widgets,
real Settings and Cache."""
import json
from datetime import date, timedelta

import pytest
from PyQt6.QtCore import QEvent, QPoint, QRect, Qt
from PyQt6.QtGui import QContextMenuEvent
from PyQt6.QtWidgets import (QApplication, QCheckBox, QFrame, QLabel, QMessageBox,
                             QPushButton, QTabWidget)

from smartcal import agenda
from smartcal.actions import Actions
from smartcal.db import Cache
from smartcal.models import CalendarInfo, Event
from smartcal.notifier import Notifier
from smartcal.settings import Settings
from smartcal.textutil import calendar_label, looks_like_email
from smartcal.ui import meeting_menus, motion, theme
from smartcal.ui import popup as popup_mod
from smartcal.ui.popup import MAX_HEIGHT, AgendaPopup
from smartcal.ui.settings_dialog import SettingsDialog
from smartcal.ui.tray import (NOTHING_HIDDEN_TEXT, RESTORE_ALL_TEXT, RESTORE_TEXT,
                              TrayController)
from smartcal.ui.window import EventDetails
from smartcal.undo import UndoStore
from test_meeting_actions_ui import (NOW, click, details, ev, make_window, rows,
                                     settle_qt, store, titles, wait_active)

AREA = QRect(0, 0, 1000, 800)
TRAY = QRect(980, 780, 20, 20)          # bottom-right taskbar: the popup grows upward
LONG = "with a title long enough to wrap onto a second line here"


def eight(rid_for=()):
    """Eight meetings, two-line titles (a list taller than the height cap)."""
    return [ev(str(i), f"Meeting {i} {LONG}", 9 + i, rid=("s" + str(i)) if i in rid_for
               else None) for i in range(8)]


class FakeWorker:
    def sync_now(self):
        pass

    def stop(self):
        pass


def popup_at(qtbot, tmp_path, events, area=AREA, cache=None, settings=None):
    settle_qt()
    if cache is None:
        cache, settings = store(tmp_path, events)
    p = AgendaPopup(cache, settings)
    p.now = lambda: NOW
    p._area = lambda point=None: area
    qtbot.addWidget(p)
    p.refresh()
    p.show_near_tray(TRAY)
    qtbot.waitExposed(p)
    wait_active(qtbot, p)
    qtbot.waitUntil(lambda: not p.showing_animated())
    return p


def right_click(widget, pos=QPoint(5, 5)):
    """A real right press/release (as on Windows), then the context-menu event."""
    QApplication.sendEvent(widget, QContextMenuEvent(
        QContextMenuEvent.Reason.Mouse, pos, widget.mapToGlobal(pos)))
    QApplication.processEvents()


def menu_action(menu, text):
    act = meeting_menus.find_action(menu, text)
    assert act is not None, (text, [a.text() for a in menu.actions()])
    return act


def list_budget(p, area=AREA) -> int:
    """The most the list may take: the height cap minus everything else in the popup."""
    return min(MAX_HEIGHT, area.height() - 24) - (p.height() - p._scroll.height())


def assert_list_not_collapsed(p, h0, row_h, area=AREA):
    """All remaining rows shown up to the cap; the popup lost at most one row's height."""
    for _ in range(2):                  # right away, and after Qt's queued work too
        content = p._rows_box.sizeHint().height()
        assert content > 0, "rows measured as empty"
        want = min(content, list_budget(p, area))
        assert p._scroll.height() >= want - 1, (p._scroll.height(), content, want)
        assert p.height() >= h0 - row_h - p._rows_box.spacing() - 1, (p.height(), h0)
        QApplication.processEvents()


def hide_selected_via_flyout(qtbot, p, index):
    """Select the row (details open), then hide it from its right-click menu (v0.8.0:
    the details panel has no Hide… button any more)."""
    click(qtbot, rows(p)[index])
    right_click(rows(p)[index])
    menu_action(p.menu(), meeting_menus.HIDE_ONE).trigger()


def hide_via_right_click(p, index, text=meeting_menus.HIDE_ONE):
    right_click(rows(p)[index])
    menu_action(p.menu(), text).trigger()


# == 1. the collapse bug ==================================================================
def test_refresh_while_shown_keeps_the_list_height(qtbot, tmp_path):
    """Root cause: rows added to a shown popup were still hidden when _resize measured
    them, so the list was sized for no rows (40 px: one row with a scrollbar)."""
    p = popup_at(qtbot, tmp_path, eight())
    h0, list0 = p.height(), p._scroll.height()
    p.refresh()
    assert p._scroll.height() == list0 and p.height() == h0
    assert all(r.isVisible() for r in rows(p))


def test_hide_does_not_collapse_list(qtbot, tmp_path):
    p = popup_at(qtbot, tmp_path, eight())
    h0, row_h = p.height(), rows(p)[2].height()
    hide_selected_via_flyout(qtbot, p, 2)
    assert p.notice_text() == meeting_menus.HIDDEN_NOTICE
    assert len(rows(p)) == 7
    assert_list_not_collapsed(p, h0, row_h)
    p.refresh()
    assert_list_not_collapsed(p, h0, row_h)


def test_hide_with_details_open_on_another_row_does_not_collapse(qtbot, tmp_path):
    p = popup_at(qtbot, tmp_path, eight())
    h0, row_h = p.height(), rows(p)[0].height()
    click(qtbot, rows(p)[5])
    assert p.flyout_open()
    hide_via_right_click(p, 1)
    assert p.notice_text() == meeting_menus.HIDDEN_NOTICE
    assert p.flyout_open() and p.selected_row().event.id == "5"   # still its details
    assert p.flyout().width() == p.width() and p.flyout().height() <= p.height()
    assert_list_not_collapsed(p, h0, row_h)


def test_hide_selected_with_chat_expanded_does_not_collapse(qtbot, tmp_path):
    p = popup_at(qtbot, tmp_path, eight())
    p.attach_chat(lambda: None)
    p.toggle_chat()
    QApplication.processEvents()
    h0, row_h = p.height(), rows(p)[3].height()
    hide_selected_via_flyout(qtbot, p, 3)
    assert not p.flyout_open()
    assert_list_not_collapsed(p, h0, row_h)


def test_hide_with_animations_on_does_not_collapse(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)
    p = popup_at(qtbot, tmp_path, eight())
    h0, row_h = p.height(), rows(p)[2].height()
    hide_selected_via_flyout(qtbot, p, 2)
    assert_list_not_collapsed(p, h0, row_h)
    qtbot.waitUntil(lambda: not p.flyout().isVisible())   # slid back
    assert_list_not_collapsed(p, h0, row_h)


def test_repeated_hide_and_undo_never_collapses(qtbot, tmp_path):
    p = popup_at(qtbot, tmp_path, eight())
    h0, row_h = p.height(), rows(p)[0].height()
    list0 = p._scroll.height()
    for i in range(10):
        hide_via_right_click(p, i % 8)
        assert len(rows(p)) == 7
        assert_list_not_collapsed(p, h0, row_h)
        p.notice_undo_button().click()
        assert len(rows(p)) == 8 and p.notice_text() == ""
        assert p._scroll.height() == list0 and p.height() == h0


def test_hide_in_small_work_area_does_not_collapse(qtbot, tmp_path):
    small = QRect(0, 0, 1000, 330)
    p = popup_at(qtbot, tmp_path, eight(), area=small)
    assert p.height() <= small.height() - 24
    h0, row_h = p.height(), rows(p)[0].height()
    hide_selected_via_flyout(qtbot, p, 0)
    assert_list_not_collapsed(p, h0, row_h, area=small)
    assert p.height() <= small.height() - 24 and small.contains(p.geometry())
    assert p._scroll.height() > row_h        # more than a single row still shows


def test_notice_appearing_and_clearing_never_collapses(qtbot, tmp_path):
    p = popup_at(qtbot, tmp_path, eight())
    h0, list0 = p.height(), p._scroll.height()
    row_h = rows(p)[0].height()
    p.show_notice("A long notice text that wraps onto a second line in the popup "
                  "and makes the header taller")
    assert_list_not_collapsed(p, h0, row_h)
    assert p.height() <= MAX_HEIGHT and p._footer.geometry().bottom() <= p.rect().bottom()
    p.notice_timer.timeout.emit()            # the notice clears itself
    assert p.notice_text() == ""
    assert p._scroll.height() == list0 and p.height() == h0


# == 2. account visibility ==============================================================
def two_accounts(tmp_path):
    cache = Cache(tmp_path / "c.db")
    cache.upsert_calendars("work@x.com", [CalendarInfo("work@x.com", "work@x.com", "#111", True)])
    cache.upsert_calendars("me@y.com", [CalendarInfo("me@y.com", "me@y.com", "#222", True)])
    start = NOW + timedelta(minutes=5)
    work = Event("w1", "work@x.com", "work@x.com", "work@x.com", "#111", "Work sync",
                 start, start + timedelta(minutes=30))
    personal = Event("p1", "me@y.com", "me@y.com", "me@y.com", "#222", "Dentist",
                     start + timedelta(minutes=1), start + timedelta(minutes=31))
    cache.upsert_events("work@x.com", [work])
    cache.upsert_events("me@y.com", [personal])
    return cache, Settings(tmp_path / "s.json"), work, personal


def test_settings_hidden_accounts_persist_and_ignore_wrong_types(tmp_path):
    path = tmp_path / "s.json"
    s = Settings(path)
    assert s.hidden_accounts == set() and not s.is_account_hidden("a@x.com")
    s.set_account_hidden("a@x.com", True)
    assert Settings(path).is_account_hidden("a@x.com")
    s.set_account_hidden("a@x.com", False)
    assert not Settings(path).is_account_hidden("a@x.com")
    data = json.loads(path.read_text(encoding="utf-8"))
    for bad in ("a@x.com", 5, {"a": 1}, None):
        data["hidden_accounts"] = bad
        path.write_text(json.dumps(data), encoding="utf-8")
        assert Settings(path).hidden_accounts == set()
    data["hidden_accounts"] = ["a@x.com", 3, None, "b@x.com"]
    path.write_text(json.dumps(data), encoding="utf-8")
    assert Settings(path).hidden_accounts == {"a@x.com", "b@x.com"}
    del data["hidden_accounts"]                  # older settings file: nothing hidden
    path.write_text(json.dumps(data), encoding="utf-8")
    assert Settings(path).hidden_accounts == set()
    assert not list(tmp_path.glob("*.tmp"))      # saved atomically


def test_set_account_hidden_rolls_back_on_failed_save(tmp_path, monkeypatch):
    s = Settings(tmp_path / "s.json")
    monkeypatch.setattr(s, "save", lambda: (_ for _ in ()).throw(OSError("ro")))
    with pytest.raises(OSError):
        s.set_account_hidden("a@x.com", True)
    assert s.hidden_accounts == set()


def test_hidden_account_events_not_in_popup_window_next_toasts(qtbot, tmp_path):
    cache, settings, work, personal = two_accounts(tmp_path)
    settings.set_account_hidden("me@y.com", True)
    p = AgendaPopup(cache, settings)
    p.now = lambda: NOW
    qtbot.addWidget(p)
    p.refresh()
    assert titles(p) == ["Work sync"]
    assert agenda.next_event(cache, settings, NOW).id == "w1"
    w = make_window(qtbot, tmp_path, [], cache=cache, settings=settings)
    assert [c.event.id for col in w.time_view.columns for c in col.chips] == ["w1"]
    w.set_view("month")
    assert [c.event.id for c in w.month_view.cells[date(2026, 11, 4)].chips] == ["w1"]
    sent = []
    Notifier(cache, settings, lambda *a: sent.append(a[0]), clock=lambda: NOW,
             fired_path=tmp_path / "n.json").check(NOW)
    assert sent == ["Work sync"]
    settings.set_account_hidden("work@x.com", True)
    settings.set_account_hidden("me@y.com", False)
    p.refresh()
    assert titles(p) == ["Dentist"]


def test_hidden_account_still_busy_for_free_slots_and_assistant(tmp_path):
    from smartcal.assistant import Assistant
    from smartcal.reminders import Reminders
    from test_assistant import FakeClaude, text_msg
    cache, settings, work, personal = two_accounts(tmp_path)
    settings.set_account_hidden("me@y.com", True)
    actions = Actions(lambda a: None, cache, UndoStore(tmp_path / "u.json"), settings)
    day = agenda.local_date(NOW)
    slots = actions.free_slots(day, timedelta(minutes=30), (0, 24), now=NOW)
    pstart = personal.start
    assert all(not (s < personal.end and e > pstart) for s, e in slots)   # still busy
    assert [e.id for e in actions.find("Dentist")] == ["p1"]
    a = Assistant(FakeClaude(text_msg("ok")), actions, Reminders(tmp_path / "r.json"),
                  settings, cache, undo=UndoStore(tmp_path / "u.json"), clock=lambda: NOW,
                  accounts_fn=lambda: ["work@x.com", "me@y.com"])
    block = a.calendar_block(NOW)
    assert "Dentist" in block and "Work sync" in block


def tray_for(qtbot, p, cache, settings, accounts, changed=None):
    tray = TrayController(p, FakeWorker(), cache, settings, accounts_fn=lambda: accounts,
                          on_visibility_changed=(lambda: changed.append(1))
                          if changed is not None else None)
    tray._tray_rect = lambda: TRAY
    tray.now = lambda: NOW
    tray.update_tooltip()
    tray.showMessage = lambda *a, **k: None
    return tray


def test_tray_accounts_submenu_toggles_visibility_immediately(qtbot, tmp_path):
    cache, settings, work, personal = two_accounts(tmp_path)
    p = popup_at(qtbot, tmp_path, [], cache=cache, settings=settings)
    changed = []
    tray = tray_for(qtbot, p, cache, settings, ["me@y.com", "work@x.com"], changed)
    tray.contextMenu().aboutToShow.emit()
    acts = tray.accounts_menu.actions()
    assert [a.text() for a in acts] == ["me@y.com", "work@x.com"]
    assert all(a.isCheckable() and a.isChecked() for a in acts)
    assert titles(p) == ["Work sync", "Dentist"]
    assert "Work sync" in tray.toolTip()
    acts[0].trigger()                                 # uncheck: hide me@y.com
    assert settings.is_account_hidden("me@y.com")
    assert Settings(tmp_path / "s.json").is_account_hidden("me@y.com")
    assert titles(p) == ["Work sync"] and changed == [1]
    acts[1].trigger()                                 # hide work too
    assert titles(p) == [] and "Next" not in tray.toolTip()
    tray.contextMenu().aboutToShow.emit()             # rebuilt: reflects the state
    assert [a.isChecked() for a in tray.accounts_menu.actions()] == [False, False]
    tray.accounts_menu.actions()[1].trigger()         # show work again
    assert titles(p) == ["Work sync"] and tray.toolTip().startswith("Next: Work sync")


def test_all_accounts_hidden_message(qtbot, tmp_path):
    cache, settings, *_ = two_accounts(tmp_path)
    p = AgendaPopup(cache, settings)
    p.now = lambda: NOW
    qtbot.addWidget(p)
    p.set_accounts(["work@x.com", "me@y.com"])
    settings.set_account_hidden("work@x.com", True)
    p.refresh()
    assert titles(p) == ["Dentist"]                   # one hidden: no special message
    settings.set_account_hidden("me@y.com", True)
    p.refresh()
    assert titles(p) == []
    assert p.empty_text() == "All accounts hidden — right-click the tray icon > Accounts"
    p.set_accounts([])
    p.refresh()
    assert p.empty_text().startswith("No calendars connected")


def test_settings_accounts_tab_checkbox_applies_on_ok_without_losing_tray_changes(
        qtbot, tmp_path):
    cache, settings, *_ = two_accounts(tmp_path)
    accounts = ["me@y.com", "work@x.com"]
    dlg = SettingsDialog(settings, cache, lambda: list(accounts), lambda: None,
                         lambda a: None)
    qtbot.addWidget(dlg)
    boxes = dlg.account_shown
    assert set(boxes) == set(accounts)
    assert all(isinstance(b, QCheckBox) and b.text() == "Show in SmartCal" and b.isChecked()
               for b in boxes.values())
    assert [dlg.account_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(2)] == accounts
    boxes["me@y.com"].setChecked(False)               # changed here
    assert not settings.is_account_hidden("me@y.com")   # nothing before OK
    settings.set_account_hidden("work@x.com", True)   # meanwhile, from the tray
    dlg.refresh_accounts()                            # e.g. a sync re-lists accounts
    assert not dlg.account_shown["me@y.com"].isChecked()   # pending change kept
    assert not dlg.account_shown["work@x.com"].isChecked()  # follows the tray
    dlg.accept()
    s2 = Settings(tmp_path / "s.json")
    assert s2.hidden_accounts == {"me@y.com", "work@x.com"}
    # An untouched box never overwrites a tray change made while the dialog is open.
    dlg2 = SettingsDialog(s2, cache, lambda: list(accounts), lambda: None, lambda a: None)
    qtbot.addWidget(dlg2)
    s2.set_account_hidden("me@y.com", False)
    dlg2.accept()
    assert not Settings(tmp_path / "s.json").is_account_hidden("me@y.com")
    # Cancel applies nothing.
    dlg3 = SettingsDialog(s2, cache, lambda: list(accounts), lambda: None, lambda a: None)
    qtbot.addWidget(dlg3)
    dlg3.account_shown["work@x.com"].setChecked(True)
    dlg3.reject()
    assert Settings(tmp_path / "s.json").is_account_hidden("work@x.com")


# == 3. no emails =========================================================================
def test_looks_like_email():
    assert looks_like_email("sean.kealey@epicgames.com") and looks_like_email(" a@b.co ")
    for name in ("Holidays", "Out of Office", "a@b", "@b.com", "Team @ HQ.x y", "", None):
        assert not looks_like_email(name)
    assert calendar_label("me@x.com") == "" and calendar_label("Holidays") == "Holidays"


def test_row_has_no_email_line(qtbot, tmp_path):
    e = ev("1", "Office", 10)
    e.calendar_name = "sean.kealey@epicgames.com"
    p = popup_at(qtbot, tmp_path, [e, ev("2", "Named", 11)])
    for r in rows(p):
        labels = r.findChildren(QLabel)
        assert [lab.objectName() for lab in labels] == ["title"]
        assert all("@" not in lab.text() and "Primary" not in lab.text() for lab in labels)
        assert r.findChild(QLabel, "calname") is None
        assert r.findChild(QLabel, "title").isVisible()
        assert r.findChild(QFrame, "stripe") is not None


def test_details_hides_email_calendar_name_but_shows_named_calendar(qtbot, tmp_path):
    mail, named = ev("1", "Office", 10), ev("2", "Day off", 11)
    mail.calendar_name = "sean.kealey@epicgames.com"
    named.calendar_name = "Out of Office"
    p = popup_at(qtbot, tmp_path, [mail, named])
    click(qtbot, rows(p)[0])
    assert details(p).findChild(QLabel, "calendar") is None
    assert "@" not in "".join(lab.text() for lab in details(p).findChildren(QLabel))
    click(qtbot, rows(p)[1])
    cal_label = details(p).findChild(QLabel, "calendar")
    assert cal_label is not None and "Out of Office" in cal_label.text()
    d = EventDetails(mail, NOW, settings=p.settings)
    qtbot.addWidget(d)
    assert d.calendar_label.isHidden() and "@" not in d.calendar_label.text()
    d2 = EventDetails(named, NOW, settings=p.settings)
    qtbot.addWidget(d2)
    assert not d2.calendar_label.isHidden() and "Out of Office" in d2.calendar_label.text()
    d.update_color()                                  # recolor keeps it hidden
    assert d.calendar_label.isHidden()


def test_toast_body_has_no_email_calendar_name(tmp_path):
    cache, settings, work, personal = two_accounts(tmp_path)
    sent = []
    Notifier(cache, settings, lambda *a: sent.append(a[1]), clock=lambda: NOW,
             fired_path=tmp_path / "n.json").check(NOW)
    assert len(sent) == 2 and all("@" not in b for b in sent)
    assert sent[0] == "Starts in 5 min · 9:05 AM"


# == 4. right-click menus =================================================================
def test_right_click_row_shows_hide_and_color_menu_no_confirmation(qtbot, tmp_path,
                                                                   monkeypatch):
    asked = []
    monkeypatch.setattr(QMessageBox, "exec", lambda *a: asked.append(a) or 0)
    p = popup_at(qtbot, tmp_path, [ev("1", "A", 10), ev("2", "B", 11), ev("3", "C", 12)])
    changed = []
    p.meetings_changed.connect(lambda: changed.append(1))
    right_click(rows(p)[1])
    menu = p.menu()
    assert menu is not None and menu.isVisible()
    texts = [a.text() for a in menu.actions()]
    assert texts == [meeting_menus.HIDE_ONE, "", meeting_menus.COLOR_SUBMENU,
                     meeting_menus.STYLE_SUBMENU]
    assert menu.actions()[1].isSeparator()
    sub = meeting_menus.submenu(menu, meeting_menus.COLOR_SUBMENU)
    assert [a.text() for a in sub.actions() if a.data()] == [n for n, _ in theme.PALETTE]
    assert meeting_menus.find_action(sub, meeting_menus.RESET_COLOR) is not None
    meeting_menus.find_action(sub, "Basil").trigger()
    assert p.settings.event_color(rows(p)[1].event) == "#3fa364"
    assert "#3fa364" in rows(p)[1].findChild(QFrame, "stripe").styleSheet()
    right_click(rows(p)[1])
    sub = meeting_menus.submenu(p.menu(), meeting_menus.COLOR_SUBMENU)
    assert meeting_menus.find_action(sub, "Basil").isChecked()
    menu_action(sub, meeting_menus.RESET_COLOR).trigger()
    assert p.settings.event_color(rows(p)[1].event) is None
    right_click(rows(p)[1])
    menu_action(p.menu(), meeting_menus.HIDE_ONE).trigger()
    assert titles(p) == ["A", "C"] and asked == [] and changed == [1, 1, 1]
    assert p.isVisible()
    assert p.notice_text() == "Hidden. Restore from the tray icon."
    assert QApplication.activeModalWidget() is None


def test_right_click_hide_series_only_for_recurring(qtbot, tmp_path):
    series = [ev("s_1104", "Standup", 10, rid="s"), ev("s_1105", "Standup", 10, day=5, rid="s")]
    p = popup_at(qtbot, tmp_path, series + [ev("x", "Lunch", 12)])
    right_click(rows(p)[1])                            # Lunch: one-off
    assert meeting_menus.find_action(p.menu(), meeting_menus.HIDE_SERIES) is None
    right_click(rows(p)[0])                            # Standup: recurring
    assert [a.text() for a in p.menu().actions()][:2] == [meeting_menus.HIDE_ONE,
                                                          meeting_menus.HIDE_SERIES]
    menu_action(p.menu(), meeting_menus.HIDE_SERIES).trigger()
    assert titles(p) == ["Lunch"]
    assert p.settings.is_event_hidden(series[1])       # the whole series


def test_right_click_does_not_toggle_selection_or_close_popup(qtbot, tmp_path):
    p = popup_at(qtbot, tmp_path, [ev("1", "A", 10), ev("2", "B", 11)])
    a, b = rows(p)
    qtbot.mouseClick(a, Qt.MouseButton.RightButton)
    right_click(a)
    assert not a.is_selected() and p.flyout() is None and p.isVisible()
    p.menu().hide()
    click(qtbot, b)                                    # details open on B
    assert b.is_selected() and p.flyout_open()
    qtbot.mouseClick(b, Qt.MouseButton.RightButton)
    right_click(b)                                     # on the selected row
    assert b.is_selected() and p.flyout_open()
    menu = p.menu()
    # The menu belongs to the group: deactivation while it is up keeps both open.
    p.active_popup = lambda: menu
    QApplication.sendEvent(p, QEvent(QEvent.Type.WindowDeactivate))
    assert p.isVisible() and p.flyout_open()
    assert p._owns(menu) and p._group_menu_or_modal_open()
    sub = meeting_menus.submenu(menu, meeting_menus.COLOR_SUBMENU)
    assert p._owns(sub)                                # the Color ▸ submenu too
    p.active_popup = lambda: None
    right_click(a)                                     # on another row, details open
    assert b.is_selected() and not a.is_selected() and p.flyout().event_shown.id == "2"
    assert not meeting_menus.is_open(menu)             # one menu at a time
    menu_action(p.menu(), meeting_menus.HIDE_ONE).trigger()   # hides A, details stay
    assert titles(p) == ["B"] and p.flyout_open() and rows(p)[0].is_selected()


def test_right_click_in_window_view_has_same_menu(qtbot, tmp_path):
    series = [ev("s_1104", "Standup", 10, rid="s"), ev("s_1105", "Standup", 10, day=5, rid="s"),
              ev("x", "Lunch", 12)]
    w = make_window(qtbot, tmp_path, series)
    changed = []
    w.meetings_changed.connect(lambda: changed.append(1))

    def chip(eid):
        return next(c for col in w.time_view.columns for c in col.chips if c.event.id == eid)

    right_click(chip("x"))
    assert [a.text() for a in w.menu().actions()] == [meeting_menus.HIDE_ONE, "",
                                                      meeting_menus.COLOR_SUBMENU,
                                                      meeting_menus.STYLE_SUBMENU]
    assert w.details is None                           # right-click never opens details
    right_click(chip("s_1104"))
    assert [a.text() for a in w.menu().actions()] == [meeting_menus.HIDE_ONE,
                                                      meeting_menus.HIDE_SERIES, "",
                                                      meeting_menus.COLOR_SUBMENU,
                                                      meeting_menus.STYLE_SUBMENU]
    sub = meeting_menus.submenu(w.menu(), meeting_menus.COLOR_SUBMENU)
    menu_action(sub, "Peacock").trigger()
    assert chip("s_1104").color == "#2aa3d8" and chip("s_1105").color == "#2aa3d8"
    right_click(chip("x"))
    menu_action(w.menu(), meeting_menus.HIDE_ONE).trigger()   # no confirmation
    assert [c.event.id for col in w.time_view.columns for c in col.chips] == ["s_1104",
                                                                              "s_1105"]
    assert w.notice_text() == meeting_menus.HIDDEN_NOTICE and changed == [1, 1]
    w.set_view("month")
    mchip = w.month_view.cells[date(2026, 11, 4)].chips[0]
    right_click(mchip)
    assert meeting_menus.find_action(w.menu(), meeting_menus.HIDE_SERIES) is not None


def test_notice_undo_button_restores_and_autoclears(qtbot, tmp_path):
    p = popup_at(qtbot, tmp_path, [ev("1", "A", 10), ev("2", "B", 11)])
    assert p.notice_undo_button() is None
    assert popup_mod.NOTICE_MS == 8000 and p.notice_timer.interval() == 8000
    hide_via_right_click(p, 0)
    assert titles(p) == ["B"]
    undo = p.notice_undo_button()
    assert undo is not None and undo.text() == "Undo" and undo.isVisible()
    assert p.notice_timer.isActive()
    changed = []
    p.meetings_changed.connect(lambda: changed.append(1))
    click(qtbot, undo)
    assert titles(p) == ["A", "B"] and not p.settings.hidden_events
    assert p.notice_text() == "" and p.notice_undo_button() is None and changed == [1]
    hide_via_right_click(p, 1)
    p.notice_timer.setInterval(30)                     # auto-clear, sped up
    p.notice_timer.start()
    qtbot.waitUntil(lambda: p.notice_text() == "", timeout=2000)
    assert p.notice_undo_button() is None and titles(p) == ["A"]   # still hidden
    p.show_notice(meeting_menus.SAVE_FAILED)           # a plain notice: no Undo
    assert p.notice_undo_button() is None
    p.hide()
    assert p.notice_text() == "" and not p.notice_timer.isActive()


# == 5. Restore hidden from the tray =======================================================
def tray_with_hidden(qtbot, tmp_path, events, hide):
    cache, settings = store(tmp_path, events)
    p = popup_at(qtbot, tmp_path, [], cache=cache, settings=settings)
    for e, scope in hide:
        settings.hide_event(e, scope)
    p.refresh()
    changed = []
    tray = tray_for(qtbot, p, cache, settings, ["a"], changed)
    tray.contextMenu().aboutToShow.emit()
    return tray, p, settings, changed


def restore_entries(tray):
    return [a for a in tray.restore_menu.actions()
            if not a.isSeparator() and a.text() != RESTORE_ALL_TEXT]


def test_tray_restore_hidden_submenu_lists_hidden_and_restores(qtbot, tmp_path):
    std = ev("s_1104", "Standup", 10, rid="s")
    long = ev("l", "A very long meeting title that goes on and on and on forever", 11)
    lunch = ev("x", "Lunch", 12)
    tray, p, settings, changed = tray_with_hidden(
        qtbot, tmp_path, [std, long, lunch], [(std, "series"), (long, "event"), (lunch, "event")])
    assert titles(p) == []
    menu = tray.restore_menu
    assert menu.menuAction().isEnabled() and menu.title() == RESTORE_TEXT
    assert menu.menuAction() in tray.contextMenu().actions()
    entries = restore_entries(tray)
    assert [a.text() for a in entries] == [
        "Lunch (Nov 4)",
        "A very long meeting title that goes on… (Nov 4)",
        "Standup (series)"]
    assert len(entries[1].text().split(" (")[0]) <= 40
    assert menu.actions()[-1].text() == RESTORE_ALL_TEXT and menu.actions()[-2].isSeparator()
    entries[0].trigger()                               # restores at once
    assert titles(p) == ["Lunch"] and changed == [1]
    assert not Settings(tmp_path / "s.json").is_event_hidden(lunch)
    assert "Lunch" in tray.toolTip()
    menu.aboutToShow.emit()                            # always current
    assert [a.text() for a in restore_entries(tray)] == [
        "A very long meeting title that goes on… (Nov 4)", "Standup (series)"]
    restore_entries(tray)[1].trigger()
    assert titles(p) == ["Standup", "Lunch"]


def test_tray_restore_all(qtbot, tmp_path):
    a, b = ev("1", "A", 10), ev("2", "B", 11)
    tray, p, settings, changed = tray_with_hidden(qtbot, tmp_path, [a, b],
                                                  [(a, "event"), (b, "event")])
    menu_action(tray.restore_menu, RESTORE_ALL_TEXT).trigger()
    assert titles(p) == ["A", "B"] and settings.hidden_events == {} and changed == [1]
    assert Settings(tmp_path / "s.json").hidden_events == {}
    tray.contextMenu().aboutToShow.emit()
    assert not tray.restore_menu.menuAction().isEnabled()


def test_tray_restore_submenu_disabled_when_nothing_hidden(qtbot, tmp_path):
    tray, p, settings, _ = tray_with_hidden(qtbot, tmp_path, [ev("1", "A", 10)], [])
    act = tray.restore_menu.menuAction()
    assert not act.isEnabled() and act.text() == NOTHING_HIDDEN_TEXT == "Nothing hidden"
    assert tray.restore_menu.actions() == []
    settings.hide_event(ev("1", "A", 10), "event")     # hidden from the popup meanwhile
    tray.contextMenu().aboutToShow.emit()
    assert act.isEnabled() and act.text() == RESTORE_TEXT


def test_tray_restore_titles_escape_ampersand(qtbot, tmp_path):
    e = ev("1", "R&D <b>sync</b>\tnow", 10)
    tray, *_ = tray_with_hidden(qtbot, tmp_path, [e], [(e, "event")])
    [entry] = restore_entries(tray)
    assert entry.text() == "R&&D <b>sync</b> now (Nov 4)"
    assert tray.restore_menu.menuAction().isEnabled()


def test_tray_restore_save_failure_says_so(qtbot, tmp_path, monkeypatch):
    a = ev("1", "A", 10)
    tray, p, settings, changed = tray_with_hidden(qtbot, tmp_path, [a], [(a, "event")])
    messages = []
    tray.showMessage = lambda *args, **k: messages.append(args[1])
    monkeypatch.setattr(settings, "save", lambda: (_ for _ in ()).throw(OSError("ro")))
    restore_entries(tray)[0].trigger()
    assert settings.is_event_hidden(a) and changed == [] and messages


# == v0.7.1 review fixes ===================================================================
TEAM = "team123@group.calendar.google.com"


def shared_setup(tmp_path):
    """a@x.com and b@x.com: each its own primary calendar, both subscribe to a team
    calendar, and b also subscribes to a's primary calendar."""
    cache = Cache(tmp_path / "c.db")
    cache.upsert_calendars("a@x.com", [CalendarInfo("a@x.com", "a@x.com", "#111", True),
                                       CalendarInfo(TEAM, "Team", "#333", False)])
    cache.upsert_calendars("b@x.com", [CalendarInfo("b@x.com", "b@x.com", "#222", True),
                                       CalendarInfo(TEAM, "Team", "#333", False),
                                       CalendarInfo("a@x.com", "a@x.com", "#111", False)])
    start = NOW + timedelta(hours=1)

    def e(account, cal_id, eid, title, h=0):
        s0 = start + timedelta(hours=h)
        return Event(eid, account, cal_id, cal_id, "#123", title, s0,
                     s0 + timedelta(minutes=30))
    team = [e(acct, TEAM, "t1", "Team standup") for acct in ("a@x.com", "b@x.com")]
    a_own = [e("a@x.com", "a@x.com", "a1", "A private", 1),
             e("b@x.com", "a@x.com", "a1", "A private", 1)]    # a's calendar, also in b
    b_own = [e("b@x.com", "b@x.com", "b1", "B only", 2)]
    for item in team + a_own + b_own:
        cache.upsert_events(item.account, [item])
    settings = Settings(tmp_path / "s.json")
    settings.set_hidden_nosave(TEAM, False)                    # the team calendar is shown
    return cache, settings


def shown(cache, settings):
    return [(e.id, e.account) for e in agenda.today(cache, settings, NOW)]


@pytest.mark.parametrize("hidden", ["a@x.com", "b@x.com"])   # sorts first, and last
def test_shared_third_party_calendar_stays_visible_when_other_account_hidden(tmp_path,
                                                                            hidden):
    cache, settings = shared_setup(tmp_path)
    other = "b@x.com" if hidden == "a@x.com" else "a@x.com"
    settings.set_account_hidden(hidden, True)
    got = shown(cache, settings)
    assert ("t1", other) in got and [i for i, _ in got].count("t1") == 1


def test_owned_calendar_subscribed_in_visible_account_still_shown(tmp_path):
    cache, settings = shared_setup(tmp_path)
    settings.set_account_hidden("a@x.com", True)
    got = shown(cache, settings)
    assert ("a1", "b@x.com") in got                  # through b's subscription
    assert [i for i, _ in got].count("a1") == 1


def test_hide_account_with_own_calendar_removes_only_its_own(tmp_path):
    cache, settings = shared_setup(tmp_path)
    settings.set_account_hidden("b@x.com", True)
    got = shown(cache, settings)
    assert "b1" not in [i for i, _ in got]           # only in b: gone
    assert ("t1", "a@x.com") in got and ("a1", "a@x.com") in got
    settings.set_account_hidden("a@x.com", True)
    assert shown(cache, settings) == []
    settings.set_account_hidden("a@x.com", False)
    settings.set_account_hidden("b@x.com", False)
    assert sorted(i for i, _ in shown(cache, settings)) == ["a1", "b1", "t1"]


def test_removed_account_not_left_hidden(tmp_path, monkeypatch):
    from smartcal import google_auth
    monkeypatch.setenv("SMARTCAL_DATA_DIR", str(tmp_path / "data"))
    for acct in ("a@x.com", "b@x.com"):
        google_auth.token_path(acct).write_text("{}")
    settings = Settings(tmp_path / "s.json")
    settings.set_account_hidden("a@x.com", True)
    settings.set_account_hidden("b@x.com", True)
    google_auth.remove_account("a@x.com", None, settings)
    assert Settings(tmp_path / "s.json").hidden_accounts == {"b@x.com"}
    # At startup: marks for accounts with no token left are dropped.
    google_auth.token_path("b@x.com").unlink()
    s2 = Settings(tmp_path / "s.json")
    s2.prune_hidden_accounts(google_auth.list_accounts())
    assert Settings(tmp_path / "s.json").hidden_accounts == set()


def test_menu_and_notice_text_strip_bidi_and_zero_width(qtbot, tmp_path):
    from smartcal.ui.tray import menu_text
    evil = "Pay‮roll​ review now & co\x07"
    assert menu_text(evil) == "Payroll review now && co"
    e = ev("1", evil, 10)
    tray, p, *_ = tray_with_hidden(qtbot, tmp_path, [e], [(e, "event")])
    [entry] = restore_entries(tray)
    assert entry.text() == "Payroll review now && co (Nov 4)"
    p.show_notice("Hidden ‮​x⁦")
    assert p.notice_text() == "Hidden x"


def test_nothing_scheduled_says_how_many_accounts_hidden(qtbot, tmp_path):
    cache, settings, work, personal = two_accounts(tmp_path)
    cache.delete_account("work@x.com")
    cache.upsert_calendars("work@x.com", [CalendarInfo("work@x.com", "work@x.com", "#111",
                                                       True)])
    p = AgendaPopup(cache, settings)
    p.now = lambda: NOW
    qtbot.addWidget(p)
    p.set_accounts(["work@x.com", "me@y.com", "z@z.com"])
    settings.set_account_hidden("me@y.com", True)
    p.refresh()
    assert p.empty_text() == "Nothing scheduled today (1 account hidden)"
    settings.set_account_hidden("z@z.com", True)
    p.refresh()
    assert p.empty_text() == "Nothing scheduled today (2 accounts hidden)"
    settings.set_account_hidden("me@y.com", False)
    settings.set_account_hidden("z@z.com", False)
    p.refresh()
    assert titles(p) == ["Dentist"]


def test_short_list_has_no_blank_band(qtbot, tmp_path):
    p = popup_at(qtbot, tmp_path, [ev("1", "A", 10), ev("2", "B", 11), ev("3", "C", 12)])
    rs = rows(p)
    want = sum(r.height() for r in rs) + p._rows_box.spacing() * (len(rs) - 1)
    assert abs(p._scroll.height() - want) <= 1
    assert p._scroll.verticalScrollBar().maximum() == 0
    last = rs[-1]
    bottom = last.mapTo(p._scroll.viewport(), QPoint(0, last.height())).y()
    assert p._scroll.viewport().height() - bottom <= 1   # nothing blank under the list


def test_mark_in_progress_suppresses_late_started_toast(tmp_path):
    cache, settings, work, personal = two_accounts(tmp_path)
    later = NOW + timedelta(minutes=7)               # both meetings in progress
    settings.set_account_hidden("me@y.com", True)
    sent = []
    n = Notifier(cache, settings, lambda *a: sent.append(a[0]), clock=lambda: later,
                 fired_path=tmp_path / "n.json")
    n.check(later)
    assert sent == ["Work sync"]                     # catch-up toast for the shown one
    settings.set_account_hidden("me@y.com", False)   # shown again mid-meeting
    n.mark_in_progress(later)
    n.check(later)
    assert sent == ["Work sync"]                     # no late "Started" for Dentist


def test_accounts_tab_elides_long_emails(qtbot, tmp_path):
    cache, settings, *_ = two_accounts(tmp_path)
    long_mail = "a.very.long.first.and.last.name.for.testing@subdomain.example-company.com"
    dlg = SettingsDialog(settings, cache, lambda: [long_mail], lambda: None, lambda a: None)
    qtbot.addWidget(dlg)
    row = dlg.account_list.itemWidget(dlg.account_list.item(0))
    label = row.findChild(QLabel, "account")
    box = dlg.account_shown[long_mail]
    tabs = dlg.findChild(QTabWidget)
    tabs.setCurrentIndex(tabs.count() - 1)                # Accounts
    dlg.resize(420, 400)
    dlg.show()
    qtbot.waitExposed(dlg)
    QApplication.processEvents()
    assert label.full_text() == long_mail and "…" in label.text()
    assert label.text() != long_mail and label.textFormat() == Qt.TextFormat.PlainText
    assert label.fontMetrics().horizontalAdvance(label.text()) <= label.width()
    # Never runs under the checkbox.
    assert label.geometry().right() < box.geometry().left()
    assert long_mail in label.toolTip()
    dlg.resize(1600, 400)
    QApplication.processEvents()
    assert label.text() == long_mail
