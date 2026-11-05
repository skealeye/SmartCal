"""The right-click menu of a meeting, shared by the popup rows and the Day/Week/Month
window: Hide, Hide series, Color ▸ and Color style ▸.

All of it only changes SmartCal's own settings (hidden meetings, meeting colors, the tile
style); nothing is sent to Google."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QMenu

from . import theme

HIDE_ONE = "Hide this meeting"
HIDE_SERIES = "Hide all in this series"
RESET_COLOR = "Reset to calendar color"
COLOR_SUBMENU = "Color"
STYLE_SUBMENU = "Color style"
STYLE_NAMES = (("stripe", "Side stripe"), ("fill", "Whole tile"))
HIDDEN_NOTICE = "Hidden. Restore from the tray icon."
SAVE_FAILED = "Couldn't save — your settings file is not writable"


def is_recurring(event) -> bool:
    return bool(getattr(event, "recurring_id", None))


def swatch_icon(color: str, size: int = 14) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QColor(theme.BORDER))
    p.setBrush(QColor(theme.stripe_color(color)))
    p.drawRoundedRect(1, 1, size - 2, size - 2, 3, 3)
    p.end()
    return QIcon(pm)


def _menu(parent) -> QMenu:
    menu = QMenu(parent)
    menu.setStyleSheet(f"QMenu{{background:{theme.BG};color:{theme.TEXT};"
                       f"border:1px solid {theme.BORDER};}}"
                       f"QMenu::item:selected{{background:{theme.BG_NEXT};}}"
                       f"QMenu::item:disabled{{color:{theme.MUTED};}}")
    return menu


def context_menu(parent, event, current: str | None, on_hide, on_pick,
                 style: str | None = None, on_style=None) -> QMenu:
    """The right-click menu of a meeting (popup row, window event): the Hide actions
    (on_hide(scope), scope "event"/"series"; hiding asks nothing), a separator, "Color" ▸
    (on_pick(hex), or on_pick(None) to reset) and, with on_style, "Color style" ▸ (Side
    stripe / Whole tile; on_style(style), one click)."""
    menu = _menu(parent)
    _add_hide_actions(menu, event, on_hide)
    menu.addSeparator()
    sub = color_menu(menu, event, current, on_pick)
    sub.setTitle(COLOR_SUBMENU)
    menu.addMenu(sub)
    if on_style is not None:
        styles = _menu(menu)
        styles.setTitle(STYLE_SUBMENU)
        for key, name in STYLE_NAMES:
            act = QAction(name, styles)
            act.setData(key)
            act.setCheckable(True)
            act.setChecked(key == (style or "stripe"))
            act.triggered.connect(lambda _c=False, k=key: on_style(k))
            styles.addAction(act)
        menu.addMenu(styles)
    return menu


def submenu(menu: QMenu, title: str) -> QMenu | None:
    return next((a.menu() for a in menu.actions()
                 if a.menu() is not None and a.text() == title), None)


def _add_hide_actions(menu: QMenu, event, on_hide) -> None:
    one = QAction(HIDE_ONE, menu)
    one.triggered.connect(lambda _c=False: on_hide("event"))
    menu.addAction(one)
    if is_recurring(event):
        series = QAction(HIDE_SERIES, menu)
        series.triggered.connect(lambda _c=False: on_hide("series"))
        menu.addAction(series)


def color_menu(parent, event, current: str | None, on_pick) -> QMenu:
    """on_pick(hex) for a swatch, on_pick(None) for "Reset to calendar color"."""
    menu = _menu(parent)
    head = QAction("Color for the whole series" if is_recurring(event)
                   else "Color for this meeting", menu)
    head.setEnabled(False)
    menu.addAction(head)
    for name, color in theme.PALETTE:
        act = QAction(swatch_icon(color), name, menu)
        act.setData(color)
        act.setCheckable(True)
        act.setChecked(current is not None and current.lower() == color)
        act.triggered.connect(lambda _c=False, c=color: on_pick(c))
        menu.addAction(act)
    menu.addSeparator()
    reset = QAction(RESET_COLOR, menu)
    reset.setEnabled(current is not None)
    reset.triggered.connect(lambda _c=False: on_pick(None))
    menu.addAction(reset)
    return menu


def delete_when_closed(menu: QMenu) -> None:
    """Free the menu once it closes. deleteLater runs on a later event-loop pass, after
    the triggered action's handler, so a chosen action still runs."""
    menu.aboutToHide.connect(menu.deleteLater)


def is_open(menu) -> bool:
    """True for a live, visible menu (False for None or an already deleted one)."""
    if menu is None:
        return False
    try:
        return bool(menu.isVisible())
    except RuntimeError:
        return False


def discard(menu) -> None:
    """Delete an older menu that is being replaced (safe if it is already gone)."""
    if menu is None:
        return
    try:
        menu.hide()
        menu.deleteLater()
    except RuntimeError:
        pass


def find_action(menu: QMenu, text: str) -> QAction | None:
    """The action labeled `text` in `menu` or (depth first) in its submenus."""
    for a in menu.actions():
        if a.text() == text and a.menu() is None:
            return a
    for a in menu.actions():
        if a.menu() is not None:
            found = find_action(a.menu(), text)
            if found is not None:
                return found
    return None
