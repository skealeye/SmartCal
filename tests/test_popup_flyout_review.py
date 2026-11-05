"""v0.5.3 review follow-ups for the details flyout: no needless rebuilds on refresh, a
focus failsafe (watchdog, reclaim check), the production focus sequences (flyout shown
without activation), list scrolling and the narrow-screen overlay. Offscreen; focus is
faked through the popup's injectable hooks, after real activation has settled."""
from dataclasses import replace

from PyQt6.QtCore import QEvent, QPoint, QRect
from PyQt6.QtWidgets import QApplication, QLabel, QPushButton

from smartcal.ui import meeting_menus
from test_meeting_actions_ui import (DESC, _menu_action, click, details, ev,
                                     open_details, rows, settle_qt)
from test_popup_flyout import TOP_TRAY, popup_on, row_top

LONG = "\n".join(f"line {i} https://example.com/{i}" for i in range(80))


def deactivate(w):
    QApplication.sendEvent(w, QEvent(QEvent.Type.WindowDeactivate))


def global_rect(w) -> QRect:
    return QRect(w.mapToGlobal(QPoint(0, 0)), w.size())


# -- refresh does not rebuild unchanged details ---------------------------------------------
def test_refresh_with_same_event_does_not_rebuild_flyout(qtbot, tmp_path):
    p = popup_on(qtbot, tmp_path, [ev("1", "A", 10, description=DESC), ev("2", "B", 11)])
    fly = open_details(qtbot, p, rows(p)[0])
    body, builds = fly.body(), fly.builds
    calls = []
    orig = fly.set_event
    fly.set_event = lambda e: calls.append(e) or orig(e)
    for _ in range(3):                                # the 30 s tick, syncs
        p.refresh()
    assert len(calls) == 3                            # asked each time...
    assert fly.builds == builds and fly.body() is body   # ...rebuilt never
    assert fly.isVisible() and rows(p)[0].is_selected()
    assert fly.event_shown.id == "1"


def test_refresh_preserves_description_scroll_when_content_unchanged(qtbot, tmp_path):
    p = popup_on(qtbot, tmp_path, [ev("1", "A", 10, description=LONG)])
    fly = open_details(qtbot, p, rows(p)[0])
    box = fly.description_box()
    bar = box.verticalScrollBar()
    qtbot.waitUntil(lambda: bar.maximum() > 40)
    bar.setValue(40)
    p.refresh()
    assert fly.description_box() is box and bar.value() == 40
    # Content changed (new location), same meeting: rebuilt, scroll position kept.
    e = replace(rows(p)[0].event, location="Room 9")
    p.cache.upsert_events("a", [e])
    builds = fly.builds
    p.refresh()
    assert fly.builds == builds + 1 and fly.description_box() is not box
    new_bar = fly.description_box().verticalScrollBar()
    qtbot.waitUntil(lambda: new_bar.value() == 40)


def test_refresh_rebuilds_when_title_changes(qtbot, tmp_path):
    p = popup_on(qtbot, tmp_path, [ev("1", "Old title", 10)])
    fly = open_details(qtbot, p, rows(p)[0])
    builds = fly.builds
    p.cache.upsert_events("a", [replace(rows(p)[0].event, title="New title")])
    p.refresh()
    assert fly.builds == builds + 1
    assert details(p).findChild(QLabel, "title").text() == "New title"


def test_color_pick_rebuilds_once(qtbot, tmp_path):
    p = popup_on(qtbot, tmp_path, [ev("1", "A", 10)])
    p.meetings_changed.connect(p.refresh)             # even if a listener refreshes again
    fly = open_details(qtbot, p, rows(p)[0])
    builds = fly.builds
    click(qtbot, details(p).findChild(QPushButton, "color"))
    _menu_action(p, "Basil").trigger()
    assert fly.builds == builds + 1
    assert "#3fa364" in details(p).findChild(QLabel, "calendar").text()


# -- failsafe ------------------------------------------------------------------------------
def test_watchdog_hides_group_when_app_inactive(qtbot, tmp_path):
    p = popup_on(qtbot, tmp_path, [ev("1", "A", 10)])
    fly = open_details(qtbot, p, rows(p)[0])
    assert p.watchdog.isActive()
    p._watchdog_tick()                                # app active: nothing happens
    settle_qt()
    assert p.isVisible() and fly.isVisible()
    p.app_active = lambda: False                      # no deactivation ever arrived
    p.active_window = lambda: None
    p._watchdog_tick()
    qtbot.waitUntil(lambda: not p.isVisible())
    assert not fly.isVisible() and not p.watchdog.isActive()


def test_watchdog_does_nothing_while_menu_open(qtbot, tmp_path):
    p = popup_on(qtbot, tmp_path, [ev("1", "A", 10)])
    fly = open_details(qtbot, p, rows(p)[0])
    click(qtbot, details(p).findChild(QPushButton, "color"))
    QApplication.processEvents()
    assert meeting_menus.is_open(p.menu())
    p.app_active = lambda: False                      # the menu holds focus on purpose
    p.active_window = lambda: None
    p._watchdog_tick()
    settle_qt()
    assert p.isVisible() and fly.isVisible()
    p.menu().hide()                                   # closing it settles: now it closes
    qtbot.waitUntil(lambda: not p.isVisible())


def test_close_flyout_when_activation_refused_hides_popup(qtbot, tmp_path):
    p = popup_on(qtbot, tmp_path, [ev("1", "A", 10)])
    fly = open_details(qtbot, p, rows(p)[0])
    p.activateWindow = lambda: None                   # Windows refuses (foreground lock)
    p.active_window = lambda: fly if fly.isVisible() else None
    click(qtbot, fly.close_button)
    assert not fly.isVisible() and p.reclaim_check.isActive()
    qtbot.waitUntil(lambda: not p.isVisible())        # the reclaim check closes it


def test_close_flyout_keeps_popup_when_focus_comes_back(qtbot, tmp_path):
    p = popup_on(qtbot, tmp_path, [ev("1", "A", 10)])
    fly = open_details(qtbot, p, rows(p)[0])
    p.active_window = lambda: fly if fly.isVisible() else p
    click(qtbot, fly.close_button)
    qtbot.waitUntil(lambda: not p.reclaim_check.isActive())
    settle_qt()
    assert p.isVisible() and not fly.isVisible()


def test_watchdog_stops_when_popup_hidden(qtbot, tmp_path):
    p = popup_on(qtbot, tmp_path, [ev("1", "A", 10)])
    assert p.watchdog.isActive() and p.watchdog.interval() == 1500
    p.hide()
    assert not p.watchdog.isActive()
    p.show_near_tray(TOP_TRAY)
    assert p.watchdog.isActive()


# -- production focus sequences (flyout shown WITHOUT activation) ----------------------------
def test_production_click_into_flyout_after_mouse_open_keeps_both(qtbot, tmp_path):
    p = popup_on(qtbot, tmp_path, [ev("1", "A", 10, description=DESC)])
    p.active_window = lambda: p                       # the popup is active
    p.toggle_row(rows(p)[0])                          # mouse: flyout shown, not activated
    fly = p.flyout()
    assert fly.isVisible() and p.isVisible()
    p.active_window = lambda: fly                     # the owner clicks into the flyout
    deactivate(p)
    settle_qt()
    assert p.isVisible() and fly.isVisible() and rows(p)[0].is_selected()


def test_production_no_active_window_twice_closes_group(qtbot, tmp_path):
    """By design the group errs toward closing: "no active window" at the deactivation
    and still one event-loop pass later closes it, even if an activation of a group
    window would only arrive after that."""
    p = popup_on(qtbot, tmp_path, [ev("1", "A", 10)])
    p.active_window = lambda: p
    p.toggle_row(rows(p)[0])
    fly = p.flyout()
    p.active_window = lambda: None
    deactivate(p)
    assert p.isVisible()                              # not decided yet...
    qtbot.waitUntil(lambda: not p.isVisible())        # ...still None one pass later: closed
    assert not fly.isVisible()
    p.active_window = lambda: fly                     # the late activation changes nothing
    settle_qt()
    assert not p.isVisible() and not fly.isVisible()


def test_production_activation_within_the_same_pass_keeps_group(qtbot, tmp_path):
    p = popup_on(qtbot, tmp_path, [ev("1", "A", 10)])
    p.active_window = lambda: p
    p.toggle_row(rows(p)[0])
    fly = p.flyout()
    p.active_window = lambda: None
    deactivate(p)
    p.active_window = lambda: fly                     # arrives before the pass ends
    activated = []
    fly.activateWindow = lambda: activated.append(1)
    qtbot.waitUntil(lambda: activated == [1])
    assert p.isVisible() and fly.isVisible()


def test_production_focus_ping_pong_never_hides(qtbot, tmp_path):
    p = popup_on(qtbot, tmp_path, [ev("1", "A", 10)])
    p.active_window = lambda: p
    p.toggle_row(rows(p)[0])
    fly = p.flyout()
    for _ in range(10):
        p.active_window = lambda: fly                 # popup -> flyout
        deactivate(p)
        settle_qt()
        p.active_window = lambda: p                   # flyout -> popup
        deactivate(fly)
        settle_qt()
        assert p.isVisible() and fly.isVisible()
    assert rows(p)[0].is_selected()


# -- list scrolling and the narrow-screen overlay ---------------------------------------------
def test_flyout_follows_list_scrolling_and_closes_when_row_scrolls_away(qtbot, tmp_path):
    area = QRect(0, 0, 1000, 400)
    events = [ev(str(i), f"Event {i}", 8 + i // 3, (i % 3) * 20) for i in range(20)]
    p = popup_on(qtbot, tmp_path, events, tray=QRect(980, 0, 20, 20), area=area)
    bar = p._scroll.verticalScrollBar()
    assert bar.maximum() > 100
    fly = open_details(qtbot, p, rows(p)[1])
    y0 = fly.y()
    assert y0 == row_top(rows(p)[1])
    bar.setValue(10)                                  # row 1 still in view: follows it
    assert fly.isVisible()
    assert fly.y() == max(area.top() + 8, row_top(rows(p)[1])) < y0
    bar.setValue(bar.maximum())                       # scrolled out of view: closes
    assert not fly.isVisible() and p.selected_row() is None and p.isVisible()


def test_overlay_on_narrow_screen_keeps_selected_row_clickable(qtbot, tmp_path):
    narrow = QRect(0, 0, 560, 800)
    p = popup_on(qtbot, tmp_path, [ev("1", "A", 10), ev("2", "B", 11), ev("3", "C", 12)],
                 tray=QRect(270, 0, 20, 20), area=narrow)
    for i in range(3):
        row = rows(p)[i]
        fly = open_details(qtbot, p, row) if i == 0 else (p.toggle_row(row) or p.flyout())
        assert p.flyout_side == "over"
        g = fly.geometry()
        assert narrow.contains(g)
        assert not g.intersects(global_rect(row)), (i, g, global_rect(row))
    click(qtbot, rows(p)[2])                          # the row is not covered: closes
    assert not p.flyout().isVisible()
