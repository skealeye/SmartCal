"""Popups and dialogs open next to the tray icon (fake tray geometries, offscreen)."""
import pytest
from PyQt6.QtCore import QPoint, QRect, QSettings, QSize
from PyQt6.QtGui import QGuiApplication

from smartcal.db import Cache
from smartcal.settings import Settings
from smartcal.ui import placement
from smartcal.ui.placement import MARGIN, compute_position, place_near_tray


def _area():
    return QGuiApplication.primaryScreen().availableGeometry()


def trays(area):
    """Tray icon rects for a taskbar on each edge, plus 'hidden in the overflow'."""
    r, b = area.right(), area.bottom()
    return {
        "bottom": QRect(r - 60, b - 10, 20, 20),
        "top": QRect(r - 60, area.top() - 10, 20, 20),
        "left": QRect(area.left() - 10, b - 60, 20, 20),
        "right": QRect(r - 10, b - 60, 20, 20),
        "empty": QRect(),
    }


def inside(frame: QRect, area: QRect) -> bool:
    return (frame.left() >= area.left() + MARGIN and frame.top() >= area.top() + MARGIN
            and frame.right() <= area.right() - MARGIN + 1
            and frame.bottom() <= area.bottom() - MARGIN + 1)


def old_compute_position(w, h, tray, area):
    """The popup's rule before the refactor (copied verbatim)."""
    if tray is None or tray.isEmpty():
        x, y = area.right() + 1 - w - MARGIN, area.bottom() + 1 - h - MARGIN
    else:
        x = tray.center().x() - w // 2
        if tray.top() < area.center().y():
            y = tray.bottom() + 1 + 6
        else:
            y = tray.top() - h - 6
    x = max(area.left() + MARGIN, min(x, area.right() + 1 - w - MARGIN))
    y = max(area.top() + MARGIN, min(y, area.bottom() + 1 - h - MARGIN))
    return QPoint(x, y)


def test_popup_placement_unchanged_after_refactor(qtbot):
    from smartcal.ui.popup import AgendaPopup
    area = QRect(0, 0, 1920, 1040)
    popup = AgendaPopup(Cache.__new__(Cache), None)
    qtbot.addWidget(popup)
    for tray in list(trays(area).values()) + [None, QRect(900, 1045, 24, 24),
                                               QRect(5, 5, 24, 24)]:
        for size in (QSize(250, 300), QSize(250, 2000), QSize(3000, 200)):
            assert compute_position(size, tray, area) == old_compute_position(
                size.width(), size.height(), tray, area)
        popup.resize(250, 320)
        assert popup.compute_position(tray, area) == old_compute_position(
            popup.width(), popup.height(), tray, area)


@pytest.mark.parametrize("edge", ["bottom", "top", "left", "right", "empty"])
def test_dialog_clamped_to_work_area(qtbot, edge):
    from PyQt6.QtWidgets import QDialog
    area = _area()
    tray = trays(area)[edge]
    for size in (QSize(300, 200), QSize(area.width() * 2, area.height() * 2)):
        pos = compute_position(size, tray, area)
        if size.width() < area.width():
            assert inside(QRect(pos, size), area)
    dlg = QDialog()
    qtbot.addWidget(dlg)
    dlg.setMinimumSize(1, 1)
    dlg.resize(area.width() * 2, area.height() * 2)
    place_near_tray(dlg, tray)       # adjustSize, shrink to fit, then position
    frame = QRect(dlg.pos(), dlg.size() + placement._frame_extra(dlg))
    assert inside(frame, area)


def _near(widget, tray, area):
    """The widget's frame is inside the work area and touches the tray's corner."""
    frame = QRect(widget.pos(), widget.size() + placement._frame_extra(widget))
    assert inside(frame, area)
    if tray.isEmpty():
        target = QPoint(area.right(), area.bottom())
    else:
        target = tray.center()
    dx = max(frame.left() - target.x(), 0, target.x() - frame.right())
    dy = max(frame.top() - target.y(), 0, target.y() - frame.bottom())
    assert dx <= 40 and dy <= 40, (frame, target)


@pytest.mark.parametrize("edge", ["bottom", "top", "left", "right", "empty"])
def test_settings_dialog_opens_near_tray(qtbot, tmp_path, edge):
    from smartcal.ui.settings_dialog import SettingsDialog
    area = _area()
    tray = trays(area)[edge]
    dlg = SettingsDialog(Settings(tmp_path / "s.json"), Cache(tmp_path / "c.db"), lambda: [],
                         lambda: None, lambda a: None)
    qtbot.addWidget(dlg)
    placement.show_near_tray(dlg, tray)
    _near(dlg, tray, area)


@pytest.mark.parametrize("edge", ["bottom", "top", "left", "right", "empty"])
def test_reminders_dialog_opens_near_tray(qtbot, tmp_path, edge):
    from smartcal.reminders import Reminders
    from test_tray import build
    area = _area()
    tray_rect = trays(area)[edge]
    tray, _ = build(qtbot, tmp_path, reminders=Reminders(tmp_path / "r.json"))
    tray._tray_rect = lambda: tray_rect
    tray.open_reminders()
    dlg = tray._reminders_dialog
    qtbot.addWidget(dlg)
    _near(dlg, tray_rect, area)


def test_window_first_open_near_tray_then_restores_saved_geometry(qtbot, tmp_path):
    from smartcal.ui.window import CalendarWindow
    area = _area()
    tray = trays(area)["bottom"]
    qs = QSettings(str(tmp_path / "q.ini"), QSettings.Format.IniFormat)
    w = CalendarWindow(Cache(tmp_path / "c.db"), Settings(tmp_path / "s.json"), qs)
    qtbot.addWidget(w)
    w.tray_geometry = lambda: tray
    w.show_view("week")
    frame = QRect(w.pos(), w.size() + placement._frame_extra(w))
    assert inside(frame, area)
    assert w.width() <= 1000 and w.height() <= 700
    # bottom-right corner next to the tray icon (above it: bottom taskbar)
    assert abs(frame.bottom() - (tray.top() - placement.GAP)) <= MARGIN + 2 \
        or frame.bottom() == area.bottom() - MARGIN + 1
    assert abs(frame.right() - tray.right()) <= MARGIN + 2 \
        or frame.left() == area.left() + MARGIN          # too wide: clamped to the left
    w.move(area.left() + 20, area.top() + 20)
    w.resize(500, 400)                         # (kept at least the minimum size)
    saved = (w.width(), w.height())
    w.hide()                                   # saves the geometry
    again = CalendarWindow(Cache(tmp_path / "c.db"), Settings(tmp_path / "s.json"), qs)
    qtbot.addWidget(again)
    again.tray_geometry = lambda: tray
    again.show_view("week")
    assert (again.width(), again.height()) == saved
    # restored where it was (the offscreen platform's restoreGeometry may shift a few
    # pixels for the frame), not moved next to the tray again
    assert (again.pos() - w.pos()).manhattanLength() <= 10
