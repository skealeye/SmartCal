"""v0.5.0: meeting details open in a pop-out (DetailsFlyout) on the left of the tray
popup. Offscreen Qt, real widgets and geometry, real Settings and Cache."""
import time

from PyQt6.QtCore import QEvent, QObject, QPoint, QRect, Qt
from PyQt6.QtWidgets import QApplication, QFrame, QLabel, QPushButton, QTextBrowser, QWidget

from smartcal.ui import meeting_menus, placement, theme
from smartcal.ui.flyout import WIDTH as FLY_WIDTH
from smartcal.ui.flyout import DetailsFlyout
from smartcal.ui.popup import BG_SELECTED, AgendaPopup
from smartcal.ui.popup import WIDTH as POPUP_WIDTH
from smartcal.ui.tray import TrayController
from test_meeting_actions_ui import (DESC, NOW, SERIES, _menu_action, click, details,  # noqa: F401
                                     ev, open_details, opened, row_menu, rows, settle_qt,
                                     store, titles, wait_active)   # opened: fixture

AREA = QRect(0, 0, 1000, 800)
TOP_TRAY = QRect(980, 0, 20, 20)        # taskbar at the top: rows sit near the top
BOTTOM_RIGHT = QRect(980, 780, 20, 20)


def popup_on(qtbot, tmp_path, events, tray=TOP_TRAY, area=AREA):
    settle_qt()                       # nothing left over from the previous test
    cache, settings = store(tmp_path, events)
    p = AgendaPopup(cache, settings)
    p.now = lambda: NOW
    p._area = lambda point=None: area
    qtbot.addWidget(p)
    p.refresh()
    p.show_near_tray(tray)
    qtbot.waitExposed(p)
    wait_active(qtbot, p)
    return p


def row_top(row) -> int:
    return row.mapToGlobal(QPoint(0, 0)).y()


def ours(p):
    """Top-level windows belonging to this popup (itself, the flyout, menus)."""
    return [w for w in QApplication.topLevelWidgets() if w is p or w.parentWidget() is p]


def flushed():
    """Run deferred deletes so leaked widgets would show up in counts."""
    for _ in range(3):
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
        QApplication.processEvents()


# -- placement ------------------------------------------------------------------------
def test_click_row_opens_flyout_left_of_popup_same_size_top_aligned(qtbot, tmp_path):
    p = popup_on(qtbot, tmp_path, [ev("1", "A", 10), ev("2", "B", 11)])
    b = rows(p)[1]
    click(qtbot, b)
    fly = p.flyout()
    assert isinstance(fly, DetailsFlyout) and fly.isVisible() and fly.isWindow()
    assert fly.windowFlags() & Qt.WindowType.Tool
    assert fly.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert fly.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    g, pg = fly.geometry(), p.geometry()
    assert p.flyout_side == "left"
    assert g.right() + 1 + placement.GAP == pg.left()        # 6 px gap, no overlap
    assert not g.intersects(pg)
    # v0.6.0: the popup's size, top aligned with it (no longer aligned with the row).
    assert g.top() == pg.top() and g.size() == pg.size()
    assert g.width() == FLY_WIDTH == POPUP_WIDTH
    assert AREA.contains(g)


def test_flyout_flips_right_when_no_room_on_left(qtbot, tmp_path):
    p = popup_on(qtbot, tmp_path, [ev("1", "A", 10)], tray=QRect(0, 0, 20, 20))
    click(qtbot, rows(p)[0])
    g, pg = p.flyout().geometry(), p.geometry()
    assert p.flyout_side == "right"
    assert g.left() == pg.right() + 1 + placement.GAP and not g.intersects(pg)
    assert AREA.contains(g)
    # Neither side fits: over the popup's left part, still inside the work area.
    narrow = QRect(0, 0, 560, 800)
    sub = tmp_path / "n"
    sub.mkdir()
    p2 = popup_on(qtbot, sub, [ev("1", "A", 10)], tray=QRect(270, 0, 20, 20), area=narrow)
    click(qtbot, rows(p2)[0])
    g2 = p2.flyout().geometry()
    assert p2.flyout_side == "over"
    assert narrow.contains(g2) and g2.left() == p2.geometry().left()


def test_flyout_clamped_inside_work_area(qtbot, tmp_path):
    small = QRect(0, 0, 1000, 400)
    events = [ev(str(i), f"Event {i}", 8 + i // 3, (i % 3) * 20) for i in range(8)]
    events[-1].description = "\n".join(f"line {i}" for i in range(200))
    events[-1].location = "Room " * 60
    p = popup_on(qtbot, tmp_path, events, tray=QRect(980, 380, 20, 20), area=small)
    last = rows(p)[-1]
    p.toggle_row(last)
    fly = p.flyout()
    g = fly.geometry()
    inner = small.adjusted(placement.MARGIN, placement.MARGIN, -placement.MARGIN,
                           -placement.MARGIN)
    assert inner.contains(g), (g, inner)
    assert g.height() <= small.height() - 2 * placement.MARGIN
    assert g.top() == p.geometry().top() and g.height() == p.height()
    # The popup moving (e.g. it grows) moves the flyout with it.
    p.show_notice("A notice that makes the popup taller than before")
    assert inner.contains(fly.geometry())
    assert fly.geometry().right() + 1 + placement.GAP == p.geometry().left()
    assert fly.geometry().top() == p.geometry().top() and fly.height() == p.height()


# -- selection ------------------------------------------------------------------------
def test_second_click_same_row_closes_flyout(qtbot, tmp_path):
    p = popup_on(qtbot, tmp_path, [ev("1", "A", 10), ev("2", "B", 11)])
    a = rows(p)[0]
    click(qtbot, a)
    assert p.flyout_open() and a.is_selected()
    assert a.target_background() == BG_SELECTED     # v0.8.0: painted, may fade
    click(qtbot, a)
    assert not p.flyout_open() and not a.is_selected() and p.selected_row() is None
    assert p.isVisible()
    click(qtbot, a)                                   # opens again, same instance
    fly = p.flyout()
    assert not hasattr(fly, "close_button")           # v0.6.0: no ✕; Esc closes it too
    qtbot.keyClick(fly, Qt.Key.Key_Escape)
    assert not fly.isVisible() and not a.is_selected() and p.isVisible()


def test_selected_highlight_distinct_from_next_up(qtbot, tmp_path):
    p = popup_on(qtbot, tmp_path, [ev("1", "Soon", 9, 10), ev("2", "Later", 11)])
    soon, later = rows(p)
    assert soon.is_next and soon.target_background() == theme.BG_NEXT
    click(qtbot, later)
    assert later.target_background() == BG_SELECTED and BG_SELECTED != theme.BG_NEXT
    assert later.background().name() == BG_SELECTED     # animations off: at once
    assert soon.target_background() == theme.BG_NEXT   # next-up keeps its own look
    click(qtbot, soon)
    assert soon.target_background() == BG_SELECTED
    assert later.target_background() == "transparent"


def test_clicking_other_row_swaps_content_single_flyout_instance(qtbot, tmp_path):
    p = popup_on(qtbot, tmp_path, [ev("1", "Alpha", 10), ev("2", "Beta", 11)])
    a, b = rows(p)
    click(qtbot, a)
    fly = p.flyout()
    flushed()
    tops = len(ours(p))
    assert details(p).findChild(QLabel, "title").text() == "Alpha"
    click(qtbot, b)
    assert p.flyout() is fly and fly.isVisible()
    assert details(p).findChild(QLabel, "title").text() == "Beta"
    assert fly.geometry().top() == p.geometry().top()
    assert [r.is_selected() for r in rows(p)] == [False, True]
    flushed()
    assert len(ours(p)) == tops
    assert len(fly.findChildren(QWidget, "details")) == 1


def test_flyout_content_matches_event(qtbot, tmp_path, opened):
    z = "https://us02web.zoom.us/j/123?pwd=abc"
    e = ev("1", "Sync <b>&</b> co", 10, location="Room <4>", description=DESC,
           meeting_url=z)
    e.calendar_name = "Work <cal>"
    p = popup_on(qtbot, tmp_path, [e])
    p.settings.set_event_color(e, "#b36ad6")          # effective color, not the calendar's
    p.refresh()
    click(qtbot, rows(p)[0])
    fly = p.flyout()
    assert fly.findChild(QLabel, "header").text() == "Meeting details"
    d = details(p)
    title = d.findChild(QLabel, "title")
    assert title.textFormat() == Qt.TextFormat.PlainText and title.text() == "Sync <b>&</b> co"
    assert d.findChild(QLabel, "when").text() == "Wed, Nov 4, 2026 · 10:00 AM – 10:30 AM"
    cal = d.findChild(QLabel, "calendar")             # plain text now; the color is
    assert cal.textFormat() == Qt.TextFormat.PlainText and cal.text() == "Work <cal>"
    assert "#b36ad6" in fly.findChild(QFrame, "colorbar").styleSheet()   # the header bar
    loc = d.findChild(QLabel, "location")
    assert loc.textFormat() == Qt.TextFormat.PlainText and loc.text() == "Room <4>"
    box = d.findChild(QTextBrowser, "description")
    assert box.anchors() == ["https://docs.example.com/a?x=1&y=2"]
    assert "<b>" not in box.toPlainText() and "not bold" in box.toPlainText()
    big = d.findChild(QPushButton, "bigjoin")
    assert big.text() == "Join Zoom meeting"
    assert big.width() <= FLY_WIDTH
    assert d.findChild(QPushButton, "color") is None   # v0.8.0: right-click only
    big.click()
    assert opened.urls == [z]
    assert not p.isVisible() and not fly.isVisible()


def test_compact_join_does_not_open_flyout(qtbot, tmp_path, opened):
    z = "https://meet.google.com/abc-defg-hij"
    p = popup_on(qtbot, tmp_path, [ev("1", "Meet", 10, meeting_url=z)])
    row = rows(p)[0]
    click(qtbot, row.join_button)
    assert opened.urls == [z]
    assert p.flyout() is None and not row.is_selected()


def test_inline_expansion_removed_rows_keep_height(qtbot, tmp_path):
    p = popup_on(qtbot, tmp_path, [ev("1", "A", 10, description=DESC, location="Room"),
                                   ev("2", "B", 11)])
    heights = [r.height() for r in rows(p)]
    h0 = p.height()
    click(qtbot, rows(p)[0])
    QApplication.processEvents()
    assert [r.height() for r in rows(p)] == heights and p.height() == h0
    for r in rows(p):
        assert r.findChild(QWidget, "details") is None
        assert r.findChild(QTextBrowser) is None
        assert not hasattr(r, "set_expanded") and not hasattr(r, "details")
    assert not hasattr(p, "expanded_row") and not hasattr(p, "_build_details")


# -- focus / lifetime (popup + flyout are one group) -------------------------------------
def test_clicking_inside_flyout_does_not_hide_popup(qtbot, tmp_path):
    p = popup_on(qtbot, tmp_path, [ev("1", "A", 10, description=DESC)])
    fly = open_details(qtbot, p, rows(p)[0])
    p.active_window = lambda: fly                     # the click activated the flyout
    QApplication.sendEvent(p, QEvent(QEvent.Type.WindowDeactivate))
    click(qtbot, details(p).findChild(QLabel, "title"))
    click(qtbot, details(p).findChild(QTextBrowser, "description").viewport())
    QApplication.processEvents()
    assert p.isVisible() and fly.isVisible() and rows(p)[0].is_selected()
    p.active_window = lambda: p                       # and back into the popup
    QApplication.sendEvent(fly, QEvent(QEvent.Type.WindowDeactivate))
    QApplication.processEvents()
    assert p.isVisible() and fly.isVisible()


def test_group_hides_when_neither_window_active(qtbot, tmp_path):
    p = popup_on(qtbot, tmp_path, [ev("1", "A", 10)])
    fly = open_details(qtbot, p, rows(p)[0])
    # No active window at all (another app took focus): closes one pass later.
    p.active_window = lambda: None
    p.app_active = lambda: False
    QApplication.sendEvent(fly, QEvent(QEvent.Type.WindowDeactivate))
    qtbot.waitUntil(lambda: not p.isVisible())
    assert not fly.isVisible() and p.selected_row() is None
    # Another window (not the group's) is active: closes at once.
    p.active_window = QApplication.activeWindow       # real again while reopening
    p.show_near_tray(TOP_TRAY)
    open_details(qtbot, p, rows(p)[0])                # real activation settles first
    other = QWidget()
    qtbot.addWidget(other)
    p.active_window = lambda: other
    p.app_active = lambda: True
    QApplication.sendEvent(p, QEvent(QEvent.Type.WindowDeactivate))
    assert not p.isVisible() and not fly.isVisible()
    # A transient "no active window" while focus moves popup -> flyout: stays open.
    p.active_window = QApplication.activeWindow
    p.show_near_tray(TOP_TRAY)
    open_details(qtbot, p, rows(p)[0])
    p.active_window = lambda: None
    QApplication.sendEvent(p, QEvent(QEvent.Type.WindowDeactivate))
    p.active_window = lambda: fly
    activated = []
    fly.activateWindow = lambda: activated.append(1)
    qtbot.waitUntil(lambda: activated == [1])
    assert p.isVisible() and fly.isVisible()


def test_flyout_hides_when_popup_hides(qtbot, tmp_path, opened):
    z = "https://zoom.us/j/1"
    p = popup_on(qtbot, tmp_path, [ev("1", "A", 10, meeting_url=z), ev("2", "B", 11)])
    def focus_lost():                                 # another app took focus
        p.active_window = lambda: None
        QApplication.sendEvent(p, QEvent(QEvent.Type.WindowDeactivate))

    for close in (lambda: p.hide(), focus_lost, lambda: rows(p)[0].join_button.click()):
        p.active_window = QApplication.activeWindow
        p.show_near_tray(TOP_TRAY)
        fly = open_details(qtbot, p, rows(p)[1])
        assert fly.isVisible()
        close()
        qtbot.waitUntil(lambda: not p.isVisible())
        assert not fly.isVisible()
        assert p.selected_row() is None and not any(r.is_selected() for r in rows(p))
    p.show_near_tray(TOP_TRAY)
    p.refresh()
    assert not p.flyout().isVisible()                 # reopening never reopens the flyout


def test_flyout_closes_when_selected_event_disappears_on_refresh(qtbot, tmp_path):
    p = popup_on(qtbot, tmp_path, [ev("1", "A", 10), ev("2", "B", 11), ev("3", "C", 12)])
    click(qtbot, rows(p)[1])
    fly = p.flyout()
    p.cache.delete_event("a", "primary", "2")         # e.g. a sync removed it
    p.refresh()
    assert not fly.isVisible() and p.selected_row() is None and p.isVisible()
    click(qtbot, rows(p)[0])
    p.settings.hide_event(rows(p)[0].event, "event")  # hidden from elsewhere
    p.refresh()
    assert not fly.isVisible() and titles(p) == ["C"]


def test_hide_from_flyout_closes_it(qtbot, tmp_path):
    p = popup_on(qtbot, tmp_path, SERIES + [ev("x", "Lunch", 12)])
    changed = []
    p.meetings_changed.connect(lambda: changed.append(1))
    click(qtbot, rows(p)[0])
    fly = p.flyout()
    menu = row_menu(p, rows(p)[0])                    # right-click the selected meeting
    assert [a.text() for a in menu.actions()][:2] == [meeting_menus.HIDE_ONE,
                                                      meeting_menus.HIDE_SERIES]
    _menu_action(p, meeting_menus.HIDE_SERIES).trigger()
    assert not fly.isVisible() and p.selected_row() is None
    assert titles(p) == ["Lunch"] and changed == [1] and p.isVisible()
    assert p.notice_text() == meeting_menus.HIDDEN_NOTICE


def test_color_menu_with_flyout_open_keeps_group_open_and_recolors_row_and_flyout(
        qtbot, tmp_path):
    p = popup_on(qtbot, tmp_path, SERIES)
    fly = open_details(qtbot, p, rows(p)[0])
    row_menu(p, rows(p)[0])
    QApplication.processEvents()
    menu = p.menu()
    assert menu is not None and meeting_menus.is_open(menu)
    p.active_popup = lambda: menu
    QApplication.sendEvent(fly, QEvent(QEvent.Type.WindowDeactivate))
    QApplication.sendEvent(p, QEvent(QEvent.Type.WindowDeactivate))
    assert p.isVisible() and fly.isVisible()
    _menu_action(p, "Basil").trigger()
    assert p.flyout() is fly and fly.isVisible() and rows(p)[0].is_selected()
    assert "#3fa364" in rows(p)[0].findChild(QFrame, "stripe").styleSheet()
    assert "#3fa364" in fly.findChild(QFrame, "colorbar").styleSheet()
    assert p.settings.event_color(SERIES[1]) == "#3fa364"
    gone = []
    menu.destroyed.connect(lambda *_: gone.append(1))
    p.active_popup = lambda: None
    p.active_window = lambda: fly                     # the flyout still has focus
    p.app_active = lambda: True
    activated = []
    fly.activateWindow = lambda: activated.append(1)
    menu.hide()
    qtbot.waitUntil(lambda: activated == [1] and gone == [1])
    assert p.isVisible() and fly.isVisible()


# -- keyboard ---------------------------------------------------------------------------
def test_esc_order_flyout_chat_popup(qtbot, tmp_path):
    p = popup_on(qtbot, tmp_path, [ev("1", "A", 10), ev("2", "B", 11)])
    p.attach_chat(lambda: None)
    p.toggle_chat()
    a = rows(p)[0]
    qtbot.keyClick(a, Qt.Key.Key_Return)
    fly = p.flyout()
    assert fly.isVisible() and p.chat_expanded()
    qtbot.waitUntil(fly.isActiveWindow)               # keyboard open: focus is in the flyout
    qtbot.keyClick(QApplication.focusWidget(), Qt.Key.Key_Escape)   # Esc inside the flyout
    assert not fly.isVisible() and p.chat_expanded() and p.isVisible()
    qtbot.waitUntil(lambda: QApplication.focusWidget() is a)   # back on the row
    qtbot.keyClick(a, Qt.Key.Key_Escape)
    assert not p.chat_expanded() and p.isVisible()
    qtbot.keyClick(p, Qt.Key.Key_Escape)
    assert not p.isVisible()


def test_keyboard_enter_opens_flyout_and_focuses_it(qtbot, tmp_path):
    z = "https://zoom.us/j/1"
    p = popup_on(qtbot, tmp_path, [ev("1", "A", 10, meeting_url=z), ev("2", "B", 11)])
    a, b = rows(p)
    qtbot.keyClick(a, Qt.Key.Key_Enter)
    fly = p.flyout()
    assert fly.isVisible() and a.is_selected()
    big = details(p).findChild(QPushButton, "bigjoin")
    qtbot.waitUntil(lambda: QApplication.focusWidget() is big)
    qtbot.keyClick(b, Qt.Key.Key_Space)               # no meeting link: the panel itself
    qtbot.waitUntil(lambda: QApplication.focusWidget() is fly)
    assert p.flyout() is fly and b.is_selected()


def test_tab_order_in_flyout_without_close_button(qtbot, tmp_path):
    # v0.8.0: Join is the panel's only stop (no Hide…/Color…); Tab stays on it.
    z = "https://zoom.us/j/1"
    p = popup_on(qtbot, tmp_path, [ev("1", "A", 10, meeting_url=z, description=DESC)])
    qtbot.keyClick(rows(p)[0], Qt.Key.Key_Return)
    fly = p.flyout()
    d = details(p)
    big = d.findChild(QPushButton, "bigjoin")
    assert fly.tab_chain() == [big]
    assert fly.findChild(QPushButton, "close") is None and not hasattr(fly, "close_button")
    qtbot.waitUntil(lambda: QApplication.focusWidget() is big)
    for key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
        qtbot.keyClick(QApplication.focusWidget(), key)
        assert QApplication.focusWidget() is big       # the description is skipped
    assert d.findChild(QTextBrowser, "description").focusPolicy() == Qt.FocusPolicy.ClickFocus


# -- tray -------------------------------------------------------------------------------
class _Worker:
    def sync_now(self):
        pass

    def stop(self):
        pass


class _Clock:
    t = 100.0

    def __call__(self):
        return self.t


def test_tray_toggle_guard_with_flyout(qtbot, tmp_path):
    tray_rect = QRect(980, 780, 20, 20)
    cache, settings = store(tmp_path, [ev("1", "A", 10), ev("2", "B", 11)])
    p = AgendaPopup(cache, settings)
    qtbot.addWidget(p)
    p.now = lambda: NOW
    p._area = lambda point=None: AREA
    p.clock = _Clock()
    p.cursor_pos = lambda: QPoint(985, 785)           # the click is on the tray icon
    tray = TrayController(p, _Worker(), cache, settings)
    tray._tray_rect = lambda: tray_rect
    settle_qt()
    tray.toggle_popup()
    qtbot.waitExposed(p)
    wait_active(qtbot, p)
    fly = open_details(qtbot, p, rows(p)[0])
    # The flyout has focus; clicking the tray icon deactivates it: the group hides...
    p.active_window = lambda: None
    p.app_active = lambda: False
    QApplication.sendEvent(fly, QEvent(QEvent.Type.WindowDeactivate))
    qtbot.waitUntil(lambda: not p.isVisible())
    assert not fly.isVisible()
    p.clock.t += 0.2
    tray.toggle_popup()                               # ...and that same click is ignored
    assert not p.isVisible() and not fly.isVisible()
    p.clock.t += 5
    tray.toggle_popup()                               # a later click opens it, no flyout
    assert p.isVisible() and not fly.isVisible()
    click(qtbot, rows(p)[0])
    assert fly.isVisible()
    tray.toggle_popup()                               # tray click while open: both hide
    assert not p.isVisible() and not fly.isVisible()


# -- budgets -----------------------------------------------------------------------------
def test_popup_height_cap_unchanged(qtbot, tmp_path):
    area = QRect(0, 0, 1000, 600)
    events = [ev(str(i), f"Event {i}", 8 + i // 3, (i % 3) * 20) for i in range(30)]
    events[5].description = "\n".join(f"line {i}" for i in range(300))
    p = popup_on(qtbot, tmp_path, events, tray=QRect(980, 580, 20, 20), area=area)
    h0 = p.height()
    assert h0 <= min(480, 600 - 24)
    for i in (0, 5, 29):
        p.toggle_row(rows(p)[i])
        assert p.height() == h0 and area.contains(p.geometry())
        assert p.flyout().height() <= area.height() - 2 * placement.MARGIN
        assert area.contains(p.flyout().geometry())
    assert p._footer.isVisible() and p._footer.geometry().bottom() <= p.rect().bottom()


def test_popup_show_under_200ms(qtbot, tmp_path):
    events = [ev(str(i), f"Event {i}", 8 + i // 3, (i % 3) * 20, description=DESC)
              for i in range(30)]
    cache, settings = store(tmp_path, events)
    p = AgendaPopup(cache, settings)
    p.now = lambda: NOW
    qtbot.addWidget(p)
    t0 = time.perf_counter()
    p.refresh()
    p.show_near_tray(QRect(700, 570, 20, 20))
    qtbot.waitExposed(p)
    QApplication.processEvents()
    assert (time.perf_counter() - t0) * 1000 < 200
    assert p.flyout() is None                          # built lazily, on first selection


def test_rapid_row_selection_no_widget_leak(qtbot, tmp_path):
    events = [ev(str(i), f"Event {i}", 9 + i, description=DESC, meeting_url="https://zoom.us/j/1")
              for i in range(5)]
    p = popup_on(qtbot, tmp_path, events)
    all_rows = rows(p)
    p.toggle_row(all_rows[0])
    fly = p.flyout()
    flushed()
    base_children = len(fly.findChildren(QObject))
    base_tops = len(ours(p))
    for _ in range(20):
        for r in all_rows[1:] + all_rows[:1]:
            p.toggle_row(r)
    assert p.flyout() is fly and fly.event_shown.id == "0"
    flushed()
    assert len(fly.findChildren(QObject)) == base_children
    assert len(ours(p)) == base_tops == 2             # the popup and one flyout
    assert [w for w in ours(p) if isinstance(w, DetailsFlyout)] == [fly]

