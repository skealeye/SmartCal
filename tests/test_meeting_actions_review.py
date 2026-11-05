"""Review follow-ups (v0.4.2): focus after menus/modals, Esc order, Tab order, safe Join
buttons, failed saves, menu cleanup, Settings > Meetings refresh."""
from PyQt6.QtCore import QEvent, QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import QApplication, QPushButton

from smartcal.settings import Settings
from smartcal.ui import meeting_menus
from smartcal.ui.window import EventDetails
from test_meeting_actions_ui import (DESC, NOW, SERIES, _menu_action, _open_color_menu,
                                     build_dialog, chips_by_id, click, details, ev,
                                     make_popup, make_window, right_click, row_menu, rows,
                                     store, titles)


# -- after a menu / modal closes ----------------------------------------------------------
def test_menu_closed_by_clicking_another_app_hides_popup(qtbot, tmp_path):
    p = make_popup(qtbot, tmp_path, [ev("1", "A", 10)])
    activated = []
    p.activateWindow = lambda: activated.append(1)
    menu = _open_color_menu(qtbot, p)
    p.active_popup = lambda: menu
    QApplication.sendEvent(p, QEvent(QEvent.Type.WindowDeactivate))
    assert p.isVisible()
    p.active_popup = lambda: None
    p.active_window = lambda: None                    # the click went to another app
    p.app_active = lambda: False
    menu.hide()
    qtbot.waitUntil(lambda: not p.isVisible())
    assert activated == []                            # never forced back to the front


def test_modal_closed_while_another_app_is_active_hides_popup(qtbot, tmp_path):
    class FakeModal(QObject):                         # the chat's "Undo anyway" box
        finished = pyqtSignal(int)

    p = make_popup(qtbot, tmp_path, [ev("1", "A", 10)])
    modal = FakeModal()
    p.active_modal = lambda: modal
    QApplication.sendEvent(p, QEvent(QEvent.Type.WindowDeactivate))
    assert p.isVisible()
    p.active_modal = lambda: None
    p.active_window = lambda: None
    p.app_active = lambda: False
    modal.finished.emit(0)
    qtbot.waitUntil(lambda: not p.isVisible())


def test_menus_are_deleted_after_closing(qtbot, tmp_path):
    p = make_popup(qtbot, tmp_path, [ev("1", "A", 10)])
    menu = _open_color_menu(qtbot, p)
    gone = []
    menu.destroyed.connect(lambda *_: gone.append(1))
    p.active_window = lambda: p
    p.app_active = lambda: True
    menu.hide()
    qtbot.waitUntil(lambda: gone == [1])
    w = make_window(qtbot, tmp_path, SERIES)
    right_click(chips_by_id(w)["s1_1104"])
    first = w.menu()
    gone2, gone3 = [], []
    first.destroyed.connect(lambda *_: gone2.append(1))
    right_click(chips_by_id(w)["s1_1105"])            # replaced by a new menu
    second = w.menu()
    second.destroyed.connect(lambda *_: gone3.append(1))
    qtbot.waitUntil(lambda: gone2 == [1])             # the replaced menu is freed
    if not gone3:
        second.hide()                                 # closing frees the current one
    qtbot.waitUntil(lambda: gone3 == [1])


# -- Esc order: details flyout -> chat -> popup --------------------------------------------
def _popup_with_chat(qtbot, tmp_path):
    p = make_popup(qtbot, tmp_path, [ev("1", "A", 10), ev("2", "B", 11)])
    p.attach_chat(lambda: None)
    return p


def test_esc_from_chat_input_closes_flyout_then_chat_then_popup(qtbot, tmp_path):
    p = _popup_with_chat(qtbot, tmp_path)
    p.toggle_chat()
    assert p.chat_expanded()
    click(qtbot, rows(p)[0])
    assert p.flyout_open()
    chat_input = p.chat()._input
    qtbot.keyClick(chat_input, Qt.Key.Key_Escape)
    assert not p.flyout_open() and p.chat_expanded() and p.isVisible()
    qtbot.keyClick(chat_input, Qt.Key.Key_Escape)
    assert not p.chat_expanded() and p.isVisible()
    qtbot.keyClick(p, Qt.Key.Key_Escape)
    assert not p.isVisible()


def test_esc_from_row_with_chat_closed(qtbot, tmp_path):
    p = _popup_with_chat(qtbot, tmp_path)
    a = rows(p)[0]
    qtbot.keyClick(a, Qt.Key.Key_Return)
    assert a.is_selected() and p.flyout_open()
    qtbot.keyClick(a, Qt.Key.Key_Escape)
    assert not a.is_selected() and not p.flyout_open() and p.isVisible() \
        and not p.chat_expanded()
    qtbot.keyClick(a, Qt.Key.Key_Escape)
    assert not p.isVisible()


# -- Tab order -----------------------------------------------------------------------
def test_tab_order_in_popup_rows_with_flyout_open(qtbot, tmp_path):
    z = "https://zoom.us/j/1"
    p = make_popup(qtbot, tmp_path, [ev("1", "A", 10, meeting_url=z, description=DESC),
                                     ev("2", "B", 11)])
    p.activateWindow()
    qtbot.waitActive(p)
    a, b = rows(p)
    p.toggle_row(a)                    # mouse-style selection: focus stays in the popup
    p.activateWindow()
    qtbot.waitActive(p)
    a.setFocus()
    qtbot.waitUntil(lambda: QApplication.focusWidget() is a)
    seen = []
    for _ in range(2):                 # the compact Join stays reachable, then the next row
        p.focusNextChild()
        seen.append(QApplication.focusWidget())
    assert seen == [a.join_button, b]


# -- Join buttons use safe_http_url ------------------------------------------------------
def test_unsafe_meeting_urls_show_no_join_button(qtbot, tmp_path):
    for i, bad in enumerate(("https://user:pw@evil.com/j/1", "https://zoom.us\\@evil.com/",
                             "https://a b.com/")):
        sub = tmp_path / str(i)
        sub.mkdir()
        p = make_popup(qtbot, sub, [ev("1", "A", 10, meeting_url=bad)])
        row = rows(p)[0]
        assert row.join_button is None and row.findChild(QPushButton, "join") is None
        p.toggle_row(row)
        assert details(p).findChild(QPushButton, "bigjoin") is None
        p.hide()
        d = EventDetails(ev("1", "A", 10, meeting_url=bad), NOW)
        qtbot.addWidget(d)
        assert d.join_button is None


# -- save failures --------------------------------------------------------------------
def _failing_save():
    raise PermissionError("read-only")


def test_failed_save_rolls_back_and_says_so(qtbot, tmp_path):
    p = make_popup(qtbot, tmp_path, SERIES + [ev("x", "Lunch", 12)])
    p.settings.set_event_color(SERIES[0], "#3fa364")
    p.settings.save = _failing_save
    changed = []
    p.meetings_changed.connect(lambda: changed.append(1))
    click(qtbot, rows(p)[0])
    row_menu(p, rows(p)[0])
    _menu_action(p, meeting_menus.HIDE_SERIES).trigger()
    assert titles(p) == ["Standup", "Lunch"]
    assert p.settings.hidden_events == {}
    assert p.notice_text() == "Couldn't save — your settings file is not writable"
    row_menu(p, rows(p)[0])
    _menu_action(p, meeting_menus.RESET_COLOR).trigger()
    assert p.settings.event_color(SERIES[0]) == "#3fa364"
    assert changed == []
    w = make_window(qtbot, tmp_path, [], cache=p.cache, settings=p.settings)
    w.hide_meeting(SERIES[0], "event")
    assert w.notice_text() == meeting_menus.SAVE_FAILED
    assert set(chips_by_id(w)) == {"s1_1104", "s1_1105", "x"}   # nothing hidden
    w.color_meeting(SERIES[0], "#e8594f")
    assert p.settings.event_color(SERIES[0]) == "#3fa364"


# -- Settings > Meetings refresh ----------------------------------------------------------
def test_meetings_tab_refreshes_when_dialog_is_activated(qtbot, tmp_path):
    a, b, c = ev("a", "Alpha", 10), ev("b", "Beta", 11), ev("c", "Gamma", 12)
    cache, settings = store(tmp_path, [a, b, c])
    settings.set_event_color(a, "#e8594f")
    dlg = build_dialog(qtbot, tmp_path, settings, cache)
    dlg.color_rows["e:primary:a"][1].click()          # pending Reset on Alpha
    settings.hide_event(b, "event")                   # made from the popup meanwhile
    settings.set_event_color(c, "#3fa364")
    QApplication.sendEvent(dlg, QEvent(QEvent.Type.WindowActivate))
    assert set(dlg.hidden_rows) == {"e:primary:b"}
    assert set(dlg.color_rows) == {"e:primary:a", "e:primary:c"}
    label, button = dlg.color_rows["e:primary:a"]
    assert button.text() == "Undo reset" and label.font().strikeOut()   # mark kept
    assert dlg.color_rows["e:primary:c"][1].text() == "Reset"
    dlg.accept()
    s2 = Settings(tmp_path / "s.json")
    assert s2.event_colors == {"e:primary:c": "#3fa364"}
    assert s2.is_event_hidden(b)
