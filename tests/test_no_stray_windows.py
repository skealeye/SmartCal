"""v0.11.1: no widget ever becomes a window of its own by accident.

The owner saw "a flash of a blue border on the other side of my screen" and "the
meeting details flash while it animates on" (v0.11.0). Cause: EventDetails made its
calendar line as a parentless QLabel and called setVisible(True) on it before it was put
in the layout: for one frame that label was a real top-level window (title bar, white
body, Windows' border) at the primary screen's center, wherever the big window was.
These offscreen tests record every top-level Show and Window-creation of every flow
that opens a window and fail on any window that is not one of ours."""
from datetime import timedelta

import pytest
from PyQt6 import sip
from PyQt6.QtCore import QEvent, QObject, QRect
from PyQt6.QtWidgets import QApplication, QMenu, QWidget

from smartcal.ui import motion, placement
from smartcal.ui.window import CalendarWindow, EventDetails
from test_meeting_actions_ui import DESC, ev, settle_qt, store, wait_active
from test_window import NOW as WNOW
from test_window import ev as wev
from test_window import make


class TopLevelShows(QObject):
    """Every top-level widget shown, and every one whose native window is created,
    noted when it happens (never touched later: it may be deleted by then)."""

    def __init__(self):
        super().__init__()
        # windows left over by earlier tests (their own timers may show them): not ours
        self.before = {sip.unwrapinstance(w) for w in QApplication.topLevelWidgets()}
        self.shown: list[tuple[type, str]] = []
        self.created: list[tuple[type, str]] = []

    def eventFilter(self, obj, e) -> bool:
        if e.type() in (QEvent.Type.Show, QEvent.Type.WinIdChange) and                 isinstance(obj, QWidget) and not sip.isdeleted(obj) and obj.isWindow()                 and sip.unwrapinstance(obj) not in self.before:
            note = (type(obj), f"{type(obj).__name__} {obj.objectName()!r} {obj.geometry()}")
            (self.shown if e.type() == QEvent.Type.Show else self.created).append(note)
        return False

    def strays(self, allowed: tuple[type, ...]) -> list[str]:
        return [text for cls, text in self.shown + self.created
                if not issubclass(cls, allowed)]


@pytest.fixture
def shows():
    settle_qt()
    rec = TopLevelShows()
    QApplication.instance().installEventFilter(rec)
    yield rec
    QApplication.instance().removeEventFilter(rec)


def _details_events():
    long = wev("p", "Quarterly planning review", WNOW, WNOW + timedelta(hours=1),
               location="Room 4", description="Notes https://example.com/x",
               meeting_url="https://meet.example/abc")
    plain = wev("q", "No calendar line", WNOW + timedelta(hours=2), WNOW + timedelta(hours=3),
                cal="someone@example.com")
    declined = wev("d", "Declined", WNOW + timedelta(hours=4), WNOW + timedelta(hours=5),
                   declined=True)
    return [long, plain, declined]


@pytest.mark.parametrize("animated", [False, True])
def test_window_details_never_show_a_stray_window(qtbot, tmp_path, shows, monkeypatch,
                                                  animated):
    monkeypatch.setattr(motion, "animations_enabled", lambda: animated)
    events = _details_events()
    w = make(qtbot, tmp_path, events, "week")
    for e in events + events:                       # open, re-open, switch meeting
        w.show_details(e)
        qtbot.waitUntil(lambda: not w.details.building(), timeout=3000)
    w.details.close()
    assert shows.strays((CalendarWindow, EventDetails)) == []


def test_event_details_alone_never_shows_a_window_before_show(qtbot, tmp_path, shows):
    """Built, placed, prepared: still nothing on screen until show()."""
    w = make(qtbot, tmp_path, [], "week")
    shows.shown.clear(); shows.created.clear()
    for e in _details_events():
        d = EventDetails(e, WNOW, w)
        qtbot.addWidget(d)
        placement.center_over(d, w)
        d.prepare_build()
        assert shows.shown == [] and shows.created == [], shows.strays(())
        assert not d.isVisible() and d.calendar_label.parentWidget() is d
        d.deleteLater()


def test_popup_flows_never_show_a_stray_window(qtbot, tmp_path, shows):
    """The popup, its details flyout, the chat drawer, a right-click menu, then the big
    window from the popup's view buttons and its details: only our windows."""
    from smartcal.ui.drawer import ChatDrawer
    from smartcal.ui.flyout import DetailsFlyout
    from smartcal.ui.popup import AgendaPopup
    from test_meeting_actions_ui import click, rows

    settle_qt()
    events = [ev("1", "A", 10, description=DESC, location="Room 4",
                 meeting_url="https://meet.example/abc"), ev("2", "B", 11)]
    cache, settings = store(tmp_path, events)
    p = AgendaPopup(cache, settings)
    p.now = lambda: WNOW
    p._area = lambda point=None: QRect(0, 0, 1000, 800)
    qtbot.addWidget(p)
    p.refresh()
    p.show_near_tray(QRect(980, 780, 20, 20))
    qtbot.waitExposed(p)
    wait_active(qtbot, p)
    click(qtbot, rows(p)[0])
    qtbot.waitUntil(lambda: p.flyout() is not None and p.flyout().isVisible(), timeout=3000)
    click(qtbot, rows(p)[1])
    p.toggle_chat()
    qtbot.wait(50)
    p.toggle_chat()
    win = CalendarWindow(cache, settings, None)
    win.now = lambda: WNOW
    qtbot.addWidget(win)
    p.view_requested.connect(lambda v: (p.dismiss(), win.show_view(v)))
    p.request_view("week")
    qtbot.waitUntil(win.isVisible, timeout=3000)
    tile = next(iter(win.time_view.tiles()), None)
    if tile is not None:
        win.show_details(tile.event)
    win.hide()
    assert shows.strays((AgendaPopup, DetailsFlyout, ChatDrawer, CalendarWindow,
                         EventDetails, QMenu)) == []
