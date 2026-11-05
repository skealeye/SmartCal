"""Where SmartCal's windows appear: near the tray icon, fully inside the work area.

`compute_position` is the agenda popup's original rule: centered on the tray
icon, just above it (or below it when the taskbar is at the top), clamped to
the work area so a taskbar on any edge works; with no tray geometry (icon in
the overflow flyout) it goes to the bottom-right of the work area.
"""
from __future__ import annotations

from PyQt6.QtCore import QPoint, QRect, QSize, Qt
from PyQt6.QtGui import QGuiApplication

MARGIN = 8
GAP = 6
# Title bar and borders of a normal top-level window before it is shown (after
# showing, the real frame is used).
DEFAULT_FRAME = QSize(16, 39)


def _empty(tray: QRect | None) -> bool:
    return tray is None or tray.isEmpty()


def compute_position(size: QSize, tray: QRect | None, area: QRect) -> QPoint:
    """Top-left for a window of `size` next to the tray icon, inside `area`."""
    w, h = size.width(), size.height()
    if _empty(tray):
        x, y = area.right() + 1 - w - MARGIN, area.bottom() + 1 - h - MARGIN
    else:
        x = tray.center().x() - w // 2
        if tray.top() < area.center().y():  # taskbar at top
            y = tray.bottom() + 1 + GAP
        else:
            y = tray.top() - h - GAP
    return clamp(QPoint(x, y), size, area)


def clamp(pos: QPoint, size: QSize, area: QRect) -> QPoint:
    x = max(area.left() + MARGIN, min(pos.x(), area.right() + 1 - size.width() - MARGIN))
    y = max(area.top() + MARGIN, min(pos.y(), area.bottom() + 1 - size.height() - MARGIN))
    return QPoint(x, y)


def area_for(tray: QRect | None) -> QRect:
    """Work area of the screen that holds the tray icon (else the primary screen)."""
    screen = None
    if not _empty(tray):
        screen = QGuiApplication.screenAt(tray.center())
    screen = screen or QGuiApplication.primaryScreen()
    return screen.availableGeometry()


def _frame_extra(widget) -> QSize:
    if widget.windowFlags() & Qt.WindowType.FramelessWindowHint:
        return QSize(0, 0)
    if widget.isVisible():
        return widget.frameGeometry().size() - widget.size()
    return QSize(DEFAULT_FRAME)


def _fit(widget, area: QRect, extra: QSize, want: QSize | None = None) -> QSize:
    """Resize so the whole frame fits the work area; returns the frame size."""
    want = want or widget.size()
    w = min(want.width(), area.width() - 2 * MARGIN - extra.width())
    h = min(want.height(), area.height() - 2 * MARGIN - extra.height())
    if (w, h) != (widget.width(), widget.height()):
        widget.resize(max(w, 1), max(h, 1))
    return widget.size() + extra


def place_near_tray(widget, tray_geometry: QRect | None) -> None:
    """Size the widget (adjustSize), then put it next to the tray icon on that
    icon's screen, fully inside the work area."""
    tray = QRect(tray_geometry) if tray_geometry is not None else None
    area = area_for(tray)
    widget.adjustSize()
    extra = _frame_extra(widget)
    frame = _fit(widget, area, extra)
    widget.move(compute_position(frame, tray, area))


def show_near_tray(widget, tray_geometry: QRect | None) -> None:
    """Place, show, then place again with the real window frame."""
    place_near_tray(widget, tray_geometry)
    widget.show()
    place_near_tray(widget, tray_geometry)
    widget.raise_()
    widget.activateWindow()


def place_window_near_tray(window, tray_geometry: QRect | None, size: QSize) -> None:
    """A big window (Day/Week/Month): `size` shrunk to fit, bottom-right corner
    next to the tray icon (above it, or below it for a top taskbar)."""
    tray = QRect(tray_geometry) if tray_geometry is not None else None
    area = area_for(tray)
    extra = _frame_extra(window)
    frame = _fit(window, area, extra, size)
    if _empty(tray):
        pos = QPoint(area.right() + 1 - frame.width() - MARGIN,
                     area.bottom() + 1 - frame.height() - MARGIN)
    else:
        x = tray.right() + 1 - frame.width()
        if tray.top() < area.center().y():
            y = tray.bottom() + 1 + GAP
        else:
            y = tray.top() - frame.height() - GAP
        pos = QPoint(x, y)
    window.move(clamp(pos, frame, area))


def center_over(widget, parent) -> None:
    """Center a dialog over its parent window (kept on the parent's screen)."""
    widget.adjustSize()
    pg = parent.frameGeometry()
    screen = QGuiApplication.screenAt(pg.center()) or QGuiApplication.primaryScreen()
    area = screen.availableGeometry()
    extra = _frame_extra(widget)
    frame = _fit(widget, area, extra)
    pos = QPoint(pg.center().x() - frame.width() // 2, pg.center().y() - frame.height() // 2)
    widget.move(clamp(pos, frame, area))
