"""The tray agenda popup ("A. Agenda list" layout). Reads only from the cache.

Clicking a meeting selects it and shows its details in a pop-out (DetailsFlyout) on
the left of the popup. Popup + flyout (+ their menus and modal boxes) are one group:
focus moving between them keeps both open; the group closes when neither is active."""
from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass
from datetime import datetime

from PyQt6.QtCore import (QAbstractAnimation, QEasingCurve, QEvent, QParallelAnimationGroup,
                          QPoint, QPropertyAnimation, QRect, QSize, Qt, QTimer, QUrl,
                          pyqtSignal)
from PyQt6.QtGui import QCursor, QDesktopServices, QGuiApplication
from PyQt6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QPushButton,
                             QScrollArea, QVBoxLayout, QWidget)

from .. import __version__, agenda
from ..textutil import meeting_label, safe_http_url
from . import meeting_menus, motion, placement, theme

log = logging.getLogger(__name__)

WIDTH = 250
MARGIN = 8
MAX_HEIGHT = 480
WATCHDOG_MS = 1500            # visible but inactive with no menu/modal up: close the group
RECLAIM_CHECK_MS = 250        # after taking focus back from the flyout: did it work?
BG_SELECTED = "#3a3f4b"       # the selected meeting (the next-up one stays BG_NEXT blue)


@dataclass
class RowInfo:
    title: str
    is_next: bool
    time_text: str


class AgendaPopup(QWidget):
    """Frameless tool window. Hides when it loses activation (WindowDeactivate)."""

    view_requested = pyqtSignal(str)
    chat_changed = pyqtSignal()   # the chat changed calendars (write or undo)
    chat_attention = pyqtSignal(str)
    meetings_changed = pyqtSignal()   # a meeting was hidden or recolored here

    def __init__(self, cache, settings, parent=None):
        super().__init__(parent)
        self.cache, self.settings = cache, settings
        # Injectable for tests.
        self.clock = time.monotonic
        self.now = lambda: datetime.now().astimezone()
        self.cursor_pos = lambda: QCursor.pos()
        self.active_modal = QApplication.activeModalWidget
        self.active_popup = QApplication.activePopupWidget
        self.active_window = QApplication.activeWindow
        self.app_active = lambda: (QGuiApplication.applicationState()
                                   == Qt.ApplicationState.ApplicationActive)
        # Does the OS foreground window belong to the group? True / False, or None for
        # "unknown" (no opinion). Qt can report the popup active while another app owns
        # the foreground (a refused SetForegroundWindow). The check only ever hides the
        # group after the group was seen in front since it was shown (_seen_foreground):
        # a popup Windows never let to the front, or a wrong handle comparison, keeps the
        # old Qt-only behavior instead of closing a popup the owner just opened.
        self.foreground_is_group = self._os_foreground_is_group
        self._foreground_strikes = 0
        self._seen_foreground = False
        self._logged_foreground_close = False
        self._settle_pending = False
        self._menu = None
        self._selected_key: tuple | None = None
        self._flyout = None             # DetailsFlyout, built on the first selection
        self.flyout_side = ""           # where it went last: "left", "right" or "over"
        self._watched_modals: list = []
        self._refreshing = False
        # Failsafe: if the group is ever left up while the app is not active (e.g. Windows
        # refused to give the popup focus back), nothing would ever deactivate it.
        self.watchdog = QTimer(self)
        self.watchdog.setInterval(WATCHDOG_MS)
        self.watchdog.timeout.connect(self._watchdog_tick)
        # After close_flyout asks for focus back. Not one event-loop pass: Qt learns about
        # the activation asynchronously, so right after hiding the active flyout there is
        # briefly no active window at all, which must not count as "focus went elsewhere".
        self.reclaim_check = QTimer(self)
        self.reclaim_check.setSingleShot(True)
        self.reclaim_check.setInterval(RECLAIM_CHECK_MS)
        self.reclaim_check.timeout.connect(self._settle_after_close)
        # Opening: slide in from the tray side while fading in (only on show; hiding is
        # immediate). Purely visual: geometry decisions use docked_geometry().
        self._show_anim = QParallelAnimationGroup(self)
        self._show_slide = QPropertyAnimation(self, b"pos")
        self._show_fade = QPropertyAnimation(self, b"windowOpacity")
        for a in (self._show_slide, self._show_fade):
            a.setEasingCurve(QEasingCurve(QEasingCurve.Type.OutCubic))
            self._show_anim.addAnimation(a)
        self.last_hidden_at = -1e9
        self.last_hidden_cursor = QPoint()
        self._anchored = False
        self._tray_geom: QRect | None = None
        self._statuses: dict[str, str] = {}
        self._rows: list[RowInfo] = []
        self._next_line = ""
        self._no_accounts = False
        self._chat = None
        self._chat_factory = None
        self._ask = None
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint)
        self.setFixedWidth(WIDTH)
        self.setObjectName("popup")
        self.setStyleSheet(
            f"#popup{{background:{theme.BG};border:1px solid {theme.BORDER};}}"
            f"QLabel{{color:{theme.TEXT};font-size:12px;}}")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 8)
        outer.setSpacing(6)
        self._header = QLabel()
        self._header.setStyleSheet("font-weight:bold;font-size:13px;")
        self._status = QLabel()
        self._status.setTextFormat(Qt.TextFormat.PlainText)
        self._status.setStyleSheet(f"color:{theme.WARN};font-size:11px;")
        self._status.setWordWrap(True)
        self._status.hide()
        self._notice = QLabel()
        self._notice.setObjectName("notice")
        self._notice.setTextFormat(Qt.TextFormat.PlainText)
        self._notice.setStyleSheet(f"color:{theme.MUTED};font-size:11px;")
        self._notice.setWordWrap(True)
        self._notice.hide()
        self._next = QLabel()
        self._next.setTextFormat(Qt.TextFormat.PlainText)
        self._next.setStyleSheet(f"color:{theme.LINK};")
        self._next.setWordWrap(True)
        self._content = QWidget()
        self._content.setStyleSheet("background:transparent;")
        self._rows_box = QVBoxLayout(self._content)
        self._rows_box.setContentsMargins(0, 0, 0, 0)
        self._rows_box.setSpacing(4)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet("QScrollArea{background:transparent;}")
        self._scroll.setWidget(self._content)
        self._scroll.verticalScrollBar().valueChanged.connect(self._list_scrolled)
        self._scroll.hide()
        self._empty = QLabel()
        self._empty.setTextFormat(Qt.TextFormat.PlainText)
        self._empty.setStyleSheet(f"color:{theme.MUTED};")
        self._empty.setWordWrap(True)
        self._footer = QLabel(
            " · ".join(f'<a href="{n.lower()}" style="color:{theme.LINK};'
                            f'text-decoration:none;">{n}</a>' for n in ("Day", "Week", "Month"))
            + f' &nbsp;&nbsp; <span style="color:{theme.MUTED};font-size:10px;">'
              f'v{__version__}</span>')
        self._footer.setObjectName("footer")
        self._footer.setTextFormat(Qt.TextFormat.RichText)
        self._footer.linkActivated.connect(self.request_view)
        for w in (self._header, self._status, self._notice, self._next, self._scroll,
                  self._empty, self._footer):
            outer.addWidget(w)
        self._outer = outer

    # -- chat (built lazily on first expand) --------------------------------
    def attach_chat(self, assistant_factory) -> None:
        """Show the collapsed "Ask Claude…" row; the chat is built on first expand."""
        self._chat_factory = assistant_factory
        if self._ask is None:
            self._ask = QPushButton("Ask Claude…")
            self._ask.setObjectName("ask")
            self._ask.setFlat(True)
            self._ask.setCursor(Qt.CursorShape.PointingHandCursor)
            self._ask.setStyleSheet(f"text-align:left;color:{theme.LINK};padding:2px 0;")
            self._ask.clicked.connect(lambda _c=False: self.toggle_chat())
            self._outer.insertWidget(self._outer.indexOf(self._footer), self._ask)

    def chat(self):
        return self._chat

    def chat_expanded(self) -> bool:
        return self._chat is not None and not self._chat.isHidden()

    def _collapse_chat(self) -> None:
        if self.chat_expanded():
            self.toggle_chat()
            self.setFocus()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.handle_escape()
            return
        super().keyPressEvent(e)

    def handle_escape(self) -> None:
        """The one place Esc is decided (rows, the flyout, the chat and the popup all end
        up here): close the details flyout, else collapse the chat, else close the popup."""
        if self.flyout_open():
            in_chat = self._chat is not None and self._chat.isAncestorOf(
                QApplication.focusWidget())
            self.close_flyout(restore_focus=not in_chat)
        elif self.chat_expanded():
            self._collapse_chat()
        else:
            self.hide()

    def toggle_chat(self) -> None:
        if self._chat_factory is None:
            return
        if self._chat is None:
            from .chat import ChatWidget
            self._chat = ChatWidget(self._chat_factory)
            self._chat.changed.connect(self.chat_changed)
            self._chat.attention.connect(self.chat_attention)
            self._chat.collapse_requested.connect(self.handle_escape)   # Esc in the chat
            self._outer.insertWidget(self._outer.indexOf(self._footer), self._chat)
            expand = True
        else:
            expand = self._chat.isHidden()
        self._chat.setVisible(expand)
        self._ask.setText("Hide chat" if expand else "Ask Claude…")
        self._resize()
        if expand:
            self._chat.focus_input()

    # -- signals / state ------------------------------------------------
    def request_view(self, name: str) -> None:
        self.view_requested.emit(name)

    def set_account_status(self, account: str, status: str) -> None:
        self._statuses[account] = status
        self._update_status()

    def prune_accounts(self, accounts) -> None:
        """Forget statuses of accounts that no longer exist."""
        keep = set(accounts)
        self._statuses = {a: s for a, s in self._statuses.items() if a in keep}
        self._update_status()

    def set_no_accounts(self, value: bool) -> None:
        self._no_accounts = value

    def _update_status(self) -> None:
        notes = []
        if any(s == "offline" for s in self._statuses.values()):
            notes.append("Offline — showing saved events")
        notes += [f"Sign in again for {a}" for a, s in self._statuses.items() if s == "reauth"]
        if any(s == "error" for s in self._statuses.values()):
            notes.append("Sync problem")
        self._status.setText("\n".join(notes))
        self._status.setVisible(bool(notes))
        self._resize()

    # -- accessors (used by tests and tooltip) ---------------------------
    def rows(self) -> list[RowInfo]:
        return list(self._rows)

    def row_widgets(self) -> list[QWidget]:
        return [self._rows_box.itemAt(i).widget() for i in range(self._rows_box.count())]

    def header_text(self) -> str:
        return self._header.text()

    def next_text(self) -> str:
        return self._next_line

    def empty_text(self) -> str:
        return self._empty.text()

    def status_text(self) -> str:
        return self._status.text()

    # -- rendering ---------------------------------------------------------
    def _area(self) -> QRect:
        return placement.area_for(self._tray_geom)

    def refresh(self, now: datetime | None = None) -> None:
        self._refreshing = True        # rows are rebuilt: list scrolling means nothing yet
        try:
            self._refresh(now)
        finally:
            self._refreshing = False
        self._place_flyout()

    def _refresh(self, now: datetime | None) -> None:
        now = now or self.now()
        events = agenda.today(self.cache, self.settings, now)
        nxt = agenda.next_event(self.cache, self.settings, now)
        self._header.setText(f"Today · {now:%a, %b} {now.day}")
        self._next_line = f"Next: {agenda.describe_next(nxt, now)}" if nxt else ""
        self._next.setText(self._next_line)
        self._next.setVisible(bool(nxt))

        while self._rows_box.count():
            item = self._rows_box.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        self._rows = []
        selected = None
        for ev in events:
            is_next = nxt is not None and ev.id == nxt.id and ev.account == nxt.account
            time_text = "All day" if ev.all_day else f"{ev.start:%I:%M %p}".lstrip("0")
            self._rows.append(RowInfo(ev.title, is_next, time_text))
            row = self._make_row(ev, time_text, is_next)
            self._rows_box.addWidget(row)
            if self._selected_key is not None and _row_key(ev) == self._selected_key:
                selected = row
        if selected is not None:   # a refresh keeps the selected meeting's details open
            selected.set_selected(True)
            if self.flyout_open():
                # Rebuilt only if what it shows changed (the 30 s tick and syncs mostly
                # change nothing: scroll position, selection and presses survive).
                self._flyout.set_event(selected.event)
        elif self._selected_key is not None:
            self.close_flyout(restore_focus=False)   # gone: hidden, deleted, synced away
        if events:
            self._empty.hide()
        else:
            self._empty.setText("No calendars connected — right-click the tray icon "
                                "> Add Google account…" if self._no_accounts
                                else "Nothing scheduled today")
            self._empty.show()
        self._scroll.setVisible(bool(events))
        self._resize()

    def _resize(self) -> None:
        """Cap the list height (header/footer stay visible), then re-anchor if shown."""
        lay = self.layout()
        if not (self._scroll.isHidden() and not self._rows):
            max_total = min(MAX_HEIGHT, self._area().height() - 24)
            self._scroll.setFixedHeight(0)
            lay.activate()
            chrome = lay.sizeHint().height()
            inner_w = WIDTH - 20
            rows_lay = self._rows_box
            content_h = max(rows_lay.sizeHint().height(),
                            rows_lay.totalHeightForWidth(inner_w) if rows_lay.hasHeightForWidth()
                            else 0)
            self._scroll.setFixedHeight(max(40, min(content_h, max_total - chrome)))
        lay.activate()
        self.adjustSize()
        if self.isVisible() and self._anchored:
            self._place()

    def _make_row(self, ev, time_text: str, is_next: bool) -> "AgendaRow":
        row = AgendaRow(ev, time_text, is_next, theme.effective_color(ev, self.settings))
        url = getattr(ev, "meeting_url", None)
        if safe_http_url(url) is not None:     # same check as the click handler
            join = QPushButton("Join")
            join.setObjectName("join")
            join.setToolTip(meeting_label(url))
            join.setCursor(Qt.CursorShape.PointingHandCursor)
            join.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            join.setStyleSheet(
                f"QPushButton{{background:{theme.LINK};color:#0b0d10;font-size:11px;"
                "font-weight:bold;padding:2px 8px;border:none;border-radius:3px;}"
                "QPushButton:hover{background:#a9c8ff;}")
            join.clicked.connect(lambda _c=False, u=url: self._join(u))
            row.head_layout.addWidget(join, 0, Qt.AlignmentFlag.AlignVCenter)
            row.join_button = join
        row.toggle_requested.connect(self.toggle_row)
        return row

    def _join(self, url: str) -> None:
        if safe_http_url(url) is not None:
            QDesktopServices.openUrl(QUrl(url))
            self.hide()

    # -- selection + details flyout ----------------------------------------------
    def flyout(self):
        """The details flyout (None until the first selection)."""
        return self._flyout

    def flyout_open(self) -> bool:
        """The details are open (shown and not sliding back: a panel sliding back is
        already closed, though still on screen and still part of the group)."""
        return self._flyout_shown() and not self._flyout.closing()

    def _flyout_shown(self) -> bool:
        """The panel is on screen (open, or sliding back)."""
        return self._flyout is not None and not self._flyout.isHidden()

    def selected_row(self) -> "AgendaRow | None":
        if self._selected_key is None:
            return None
        return next((r for r in self.row_widgets() if isinstance(r, AgendaRow)
                     and _row_key(r.event) == self._selected_key), None)

    def toggle_row(self, row: "AgendaRow", keyboard: bool = False) -> None:
        """Select `row` and show its details in the flyout; the selected row again
        closes the flyout. `keyboard`: Enter/Space, so focus moves into the flyout."""
        if self.flyout_open() and self._selected_key == _row_key(row.event):
            self.close_flyout(restore_focus=keyboard)
            return
        self.select_row(row, keyboard)

    def select_row(self, row: "AgendaRow", keyboard: bool = False) -> None:
        if not self.isVisible():
            return                      # the flyout never outlives (or precedes) the popup
        self._selected_key = _row_key(row.event)
        for r in self.row_widgets():
            if isinstance(r, AgendaRow):
                r.set_selected(r is row)
        self._scroll.ensureWidgetVisible(row, 0, 0)
        fly = self._ensure_flyout()
        fly.set_event(row.event)        # one instance; the content swaps in place
        opening = not self.flyout_open()    # hidden, or sliding back (turns around)
        self._place_flyout(force=True)
        if opening:
            # WA_ShowWithoutActivating: the popup keeps focus. Over the popup (narrow
            # screen) there is nothing to slide out from behind: it appears at once.
            fly.slide_open(0 if self.flyout_side == "over"
                           else motion.duration(motion.FLYOUT_OPEN_MS))
            self._place_flyout(force=True)
        fly.raise_()
        if keyboard:
            fly.activateWindow()
            fly.focus_first()

    def close_flyout(self, restore_focus: bool = True, animate: bool = True) -> None:
        """Close the details and clear the selection. If the flyout had focus, the popup
        takes it back right after (so activation never stays with some other app's
        window), and the formerly selected row gets keyboard focus.
        The panel slides back behind the popup and hides when that finishes (at once
        when animations are off, the popup is hidden, or `animate` is False). Hidden at
        once: the flyout's own deactivation then no longer counts for the group; sliding
        back: the reclaim grace starts first, so its deactivation is not a focus loss."""
        row = self.selected_row()
        self._selected_key = None
        for r in self.row_widgets():
            if isinstance(r, AgendaRow):
                r.set_selected(False)
        fly = self._flyout
        if fly is None or fly.isHidden():
            return
        ms = motion.duration(motion.FLYOUT_CLOSE_MS) \
            if animate and self.isVisible() and self.flyout_side != "over" else 0
        if fly.closing():
            if not ms:
                fly.hide_now()          # already sliding back: the popup went away
            return
        active = self.active_window()
        reclaim = active is fly or (restore_focus and active is not self)
        if ms and reclaim and self.isVisible():
            self.reclaim_check.start()
        fly.slide_close(ms)             # ms 0: hidden now
        if reclaim and self.isVisible():
            self.raise_()
            self.activateWindow()
            # If Windows refuses the activation (foreground lock), the popup would stay
            # up inactive with no WindowDeactivate ever to come: check shortly.
            self.reclaim_check.start()
        if restore_focus and self.isVisible():
            (row or self).setFocus()

    def _flyout_slid_back(self, was_active: bool) -> None:
        """The panel finished sliding back and hid. If it had focus then (clicked into
        while sliding back), the popup takes focus back, as close_flyout does."""
        if was_active and self.isVisible() and not self.reclaim_check.isActive():
            self.raise_()
            self.activateWindow()
            self.reclaim_check.start()

    def _ensure_flyout(self):
        if self._flyout is None:
            from .flyout import DetailsFlyout
            self._flyout = DetailsFlyout(self)
        return self._flyout

    def _place_flyout(self, force: bool = False) -> None:
        """The popup's size, top aligned with it: left of the popup inside the work area
        of its screen (right of it, or over it, when the left has no room). Uses the
        popup's docked geometry, never a position mid-animation. While sliding, only the
        slide's end moves (the slide owns the panel's position)."""
        fly = self._flyout
        if fly is None or (fly.isHidden() and not force):
            return
        anchor = self.docked_geometry()
        area = self._area()
        size = QSize(WIDTH, anchor.height())
        pos, side = placement.beside(size, anchor, anchor.top(), area)
        if fly.closing():
            if side != "over":          # sliding back: follow the popup
                fly.place(size, pos, QPoint(anchor.left(), pos.y()), anchor)
            return
        row = self.selected_row()
        if row is None:
            return
        self.flyout_side = side
        if side == "over":
            pos, size = self._overlay_clear_of(row, size, pos, area, anchor)
            fly.place(size, pos, pos, None)
            return
        # Tucked: exactly behind the popup, which it slides out from and back behind.
        fly.place(size, pos, QPoint(anchor.left(), pos.y()), anchor)

    def _overlay_clear_of(self, row, size: QSize, pos: QPoint, area: QRect,
                          anchor: QRect) -> tuple[QPoint, QSize]:
        """Over the popup (narrow screens): below the selected row, or above it, so the
        row stays clickable to close the details; shrunk to the larger space if needed."""
        m, gap = placement.MARGIN, placement.GAP
        self._rows_box.activate()
        # The row where it is once the popup is docked (not mid-animation).
        rtop = row.mapToGlobal(QPoint(0, 0)).y() + anchor.top() - self.y()
        rbottom = rtop + row.height() - 1
        below = area.bottom() + 1 - m - (rbottom + 1 + gap)
        above = rtop - gap - (area.top() + m)
        h = size.height()
        if h > below and h > above:
            h = max(80, max(below, above))
        size = QSize(size.width(), h)
        if h <= below:
            return QPoint(pos.x(), rbottom + 1 + gap), size
        return QPoint(pos.x(), max(area.top() + m, rtop - gap - h)), size

    def _list_scrolled(self, _value: int) -> None:
        """The details follow their row; a row scrolled out of view closes them."""
        if self._refreshing or not self.flyout_open():
            return
        row = self.selected_row()
        view = self._scroll.viewport()
        if row is None:
            return
        top_left = row.mapTo(view, QPoint(0, 0))
        if not QRect(top_left, row.size()).intersects(view.rect()):
            self.close_flyout(restore_focus=False)
            return
        self._place_flyout()

    def moveEvent(self, e):
        super().moveEvent(e)
        self._place_flyout()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place_flyout()

    # -- Hide… / Color… menus ---------------------------------------------------
    def menu(self):
        return self._menu

    def _show_menu(self, menu, button) -> None:
        if self._menu is not menu:
            meeting_menus.discard(self._menu)
        self._menu = menu
        menu.aboutToHide.connect(self._settle_after_close)
        meeting_menus.delete_when_closed(menu)
        menu.popup(button.mapToGlobal(button.rect().bottomLeft()))

    def _settle_after_close(self, *_args) -> None:
        """A menu or modal box closed (or activation left the group with nowhere known to
        go). Wait one event-loop pass for Windows to settle activation, then: if the
        click went to another app (no active window, the app is not active, or the
        active window is neither the popup nor the flyout), close the group like any
        focus loss; else the group window that is active takes focus back.
        Re-activating blindly could leave the popup up but inactive, with no further
        WindowDeactivate to ever hide it."""
        if self._settle_pending:
            return
        self._settle_pending = True
        QTimer.singleShot(0, self._settle)

    def _settle(self) -> None:
        self._settle_pending = False
        if not self.isVisible():
            return
        if self.reclaim_check.isActive():
            # Focus is on its way back from the flyout (Esc, ✕, Hide…): for a moment no
            # window is reported active. The reclaim check settles when the grace ends.
            return
        if self.active_modal() is not None or meeting_menus.is_open(self._menu):
            return                      # something else still holds focus on purpose
        active = self.active_window()
        if active is not None and active is self._flyout and not self.flyout_open() \
                and self.app_active():
            # Focus is still on the flyout that was just closed, on its way back to the
            # popup: ask again (the watchdog closes the group if that never happens).
            self.raise_()
            self.activateWindow()
            return
        if active is None or not self.app_active() or not self._is_group_window(active):
            self.hide()                 # hides the flyout too
            return
        if self._foreground_state() is False and self._seen_foreground:
            return                      # another app has the foreground: don't push in;
                                        # the watchdog closes the group if that persists
        active.raise_()
        active.activateWindow()

    def show_hide_menu(self, ev, button) -> None:
        # The menu belongs to the popup (not the row or the flyout's content): a refresh
        # or a content swap never deletes it.
        self._show_menu(meeting_menus.hide_menu(
            self, ev, lambda scope, e=ev: self.hide_meeting(e, scope)), button)

    def show_color_menu(self, ev, button) -> None:
        self._show_menu(meeting_menus.color_menu(
            self, ev, self.settings.event_color(ev),
            lambda color, e=ev: self.color_meeting(e, color)), button)

    def hide_meeting(self, ev, scope: str) -> None:
        try:
            self.settings.hide_event(ev, scope)
        except OSError:                 # rolled back by Settings; say so, change nothing
            self.show_notice(meeting_menus.SAVE_FAILED)
            return
        self.close_flyout(restore_focus=False)   # its meeting is gone
        self.refresh()
        self.show_notice(meeting_menus.HIDDEN_NOTICE)
        self.meetings_changed.emit()

    def color_meeting(self, ev, color) -> None:
        try:
            if color is None:
                self.settings.clear_event_color(ev)
            else:
                self.settings.set_event_color(ev, color)
        except OSError:
            self.show_notice(meeting_menus.SAVE_FAILED)
            return
        self.refresh()          # keeps the flyout open, recolored
        self.meetings_changed.emit()

    def show_notice(self, text: str) -> None:
        self._notice.setText(text)
        self._notice.setVisible(bool(text))
        self._resize()

    def notice_text(self) -> str:
        return self._notice.text() if not self._notice.isHidden() else ""


    # -- placement / focus ---------------------------------------------------
    def compute_position(self, tray: QRect | None, area: QRect) -> QPoint:
        return placement.compute_position(QSize(self.width(), self.height()), tray, area)

    def _place(self) -> None:
        pos = self.compute_position(self._tray_geom, self._area())
        if self.showing_animated():
            self._show_slide.setEndValue(pos)   # the slide owns the position: retarget
            return
        self.move(pos)

    def docked_geometry(self) -> QRect:
        """Where the popup is once it has settled (compute_position next to the tray),
        whatever its opening animation shows right now. The flyout docks to this."""
        if self._anchored:
            return QRect(self.compute_position(self._tray_geom, self._area()), self.size())
        return QRect(self.pos(), self.size())

    def showing_animated(self) -> bool:
        return self._show_anim.state() == QAbstractAnimation.State.Running

    def show_near_tray(self, tray_geometry) -> None:
        self._tray_geom = QRect(tray_geometry)
        self._anchored = True
        was_visible = self.isVisible()
        self._resize()  # sizes against the right screen; places only when visible
        self._place()
        ms = 0 if was_visible else motion.duration(motion.POPUP_MS)
        if ms:
            # Slides in from the tray side (away from the taskbar) while fading in. The
            # first paint is not delayed: the animation runs after the show.
            docked = self.pos()
            edge = motion.tray_edge(self._tray_geom, self._area())
            self.move(docked + motion.slide_in_offset(edge))
            self.setWindowOpacity(0.0)
            self._show_slide.setDuration(ms)
            self._show_slide.setStartValue(self.pos())
            self._show_slide.setEndValue(docked)
            self._show_fade.setDuration(ms)
            self._show_fade.setStartValue(0.0)
            self._show_fade.setEndValue(1.0)
        self.show()
        self.raise_()
        self.activateWindow()
        if ms:
            self._show_anim.start()

    def _stop_show_animation(self) -> None:
        """Hidden (or shown again) mid-animation: no animation left running, fully
        opaque, and docked if still shown."""
        if self.showing_animated():
            self._show_anim.stop()
            if self.isVisible():
                self.move(self._show_slide.endValue())
        if self.windowOpacity() != 1.0:
            self.setWindowOpacity(1.0)

    def event(self, e):
        if e.type() == QEvent.Type.WindowDeactivate and self.isVisible():
            self._group_deactivated(self)
        elif e.type() == QEvent.Type.WindowActivate:
            self.note_foreground()
        return super().event(e)

    def note_foreground(self) -> None:
        """A group window was activated (or a watchdog tick): remember if the OS really
        put the group in front."""
        if self.isVisible() and self._foreground_state() is True:
            self._seen_foreground = True

    def _group_deactivated(self, source) -> None:
        """`source` (the popup or the flyout) lost activation. The group stays open if
        the other group window took it, or a menu/modal box of the group is up;
        otherwise it closes (popup and flyout)."""
        if not self.isVisible():
            return
        if self.reclaim_check.isActive():
            return                      # focus is being reclaimed from the flyout: the
                                        # reclaim check decides when the grace ends
        active = self.active_window()
        if active is not None and active is not source and self._is_group_window(active):
            return                      # e.g. a click in the flyout: still the same group
        modal = self.active_modal()
        menu = self.active_popup()
        if not self._owns(menu):    # e.g. the tray's own context menu: not ours
            menu = self._menu if meeting_menus.is_open(self._menu) else None
        if modal is not None and not self._is_group_window(modal):
            self._reactivate_after(modal)   # e.g. the "Undo anyway" confirm box
        elif menu is not None and not self._is_group_window(menu):
            self._reactivate_after(menu)    # the Hide…/Color… menu
        elif self._flyout_shown() and (active is None or active is source):
            # Not conclusive: "no active window" (Windows may report that for a moment
            # while activation moves between the popup and the flyout), or the window
            # that just lost activation still reported as active (the deactivation
            # overtook the activation change, e.g. a late focus change from a window
            # being destroyed). Re-read the state one event-loop pass later (_settle):
            # the group closes only if neither of its windows is active then.
            self._settle_after_close()
        else:
            self.hide()

    def _is_group_window(self, w) -> bool:
        return w is self or (w is not None and w is self._flyout and self._flyout_shown())

    def _owns(self, w) -> bool:
        """True if `w` (a popup menu) was opened from the popup, the flyout or their
        children."""
        while w is not None:
            if w is self or (w is self._flyout and w is not None):
                return True
            w = w.parentWidget()
        return False

    def _reactivate_after(self, modal) -> None:
        """Stay open while a modal box or a popup menu is up; take focus back when it
        closes (QDialog/QMessageBox: finished; QMenu: aboutToHide)."""
        if modal in self._watched_modals:
            return
        finished = getattr(modal, "finished", None) or getattr(modal, "aboutToHide", None)
        if finished is None:
            return
        self._watched_modals.append(modal)

        def done(*_args, m=modal) -> None:
            if m in self._watched_modals:
                self._watched_modals.remove(m)
            self._settle_after_close()
        finished.connect(done)

    def showEvent(self, e):
        super().showEvent(e)
        self._foreground_strikes = 0
        self._seen_foreground = False
        self.watchdog.start()

    def _watchdog_tick(self) -> None:
        """Every WATCHDOG_MS while shown, unless a menu or modal box of the group is up or
        focus is being reclaimed from the flyout (the reclaim check decides then):
        - app not active, no active window, or a non-group window active (e.g. the
          Week window, Settings): settle, which closes the group if still so;
        - Qt says a group window is active but the OS foreground is elsewhere on two
          ticks in a row (>= WATCHDOG_MS, not a transient flip), and the group was seen
          in front since it was shown: close the group. "Unknown" never counts."""
        if not self.isVisible():
            self.watchdog.stop()
            return
        if self.reclaim_check.isActive():
            return                      # the next tick looks again
        if self._group_menu_or_modal_open():
            self._foreground_strikes = 0
            return
        active = self.active_window()
        if not self.app_active() or active is None or not self._is_group_window(active):
            self._foreground_strikes = 0
            self._settle_after_close()
            return
        state = self._foreground_state()
        if state is True:
            self._seen_foreground = True
        if state is not False or not self._seen_foreground:
            self._foreground_strikes = 0    # in front, unknown, or never seen in front
            return
        self._foreground_strikes += 1
        if self._foreground_strikes >= 2:
            self._foreground_strikes = 0
            if not self._logged_foreground_close:
                self._logged_foreground_close = True
                log.info("foreground check closed the popup")
            self.hide()                 # hides the flyout too

    def _group_menu_or_modal_open(self) -> bool:
        return self.active_modal() is not None or meeting_menus.is_open(self._menu) \
            or self._owns(self.active_popup())

    def _foreground_state(self) -> bool | None:
        """foreground_is_group() as True / False, or None ("unknown") for anything else,
        including any failure: unknown never hides anything."""
        try:
            state = self.foreground_is_group()
        except Exception:  # noqa: BLE001 - a failsafe must never break the popup
            return None
        return state if isinstance(state, bool) else None

    def _os_foreground_is_group(self) -> bool | None:
        """Windows (real display) only: is the foreground window the popup, the flyout,
        or a menu/modal box of the group? None (unknown) offscreen, on other systems,
        when there is no foreground window, or when the call fails."""
        if sys.platform != "win32" or QGuiApplication.platformName() != "windows":
            return None
        try:
            import ctypes
            fg = int(ctypes.windll.user32.GetForegroundWindow() or 0)
        except Exception:  # noqa: BLE001
            return None
        if not fg:
            return None                 # in the middle of a switch: no opinion
        group = [self]
        if self._flyout_shown():
            group.append(self._flyout)
        if meeting_menus.is_open(self._menu):
            group.append(self._menu)
        for w in (self.active_modal(), self.active_popup()):
            if w is not None:
                group.append(w)
        return fg in {int(w.winId()) for w in group}

    def hideEvent(self, e):
        self._seen_foreground = False
        self.watchdog.stop()
        self.reclaim_check.stop()
        self._stop_show_animation()
        self.last_hidden_at = self.clock()
        self.last_hidden_cursor = self.cursor_pos()
        # Never outlives the popup: hidden at once, even mid-slide.
        self.close_flyout(restore_focus=False, animate=False)
        self._notice.hide()
        super().hideEvent(e)

    def recently_dismissed_by_tray_click(self, tray: QRect | None, within: float = 0.6) -> bool:
        """True if the popup just hid because this very click deactivated it."""
        if self.clock() - self.last_hidden_at >= within:
            return False
        if tray is None or tray.isEmpty():
            return True
        return tray.contains(self.last_hidden_cursor)


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _row_key(ev) -> tuple:
    return (ev.account, ev.calendar_id, ev.id)


class AgendaRow(QFrame):
    """One agenda row. Click (outside its compact Join button), Enter or Space selects
    it and shows its details in the flyout (again: closes them); Join never selects."""

    toggle_requested = pyqtSignal(object, bool)   # row, by keyboard

    def __init__(self, ev, time_text: str, is_next: bool, color: str, parent=None):
        super().__init__(parent)
        self.event = ev
        self.join_button: QPushButton | None = None
        self.is_next = is_next
        self._selected = False
        self.setObjectName("row")
        self._restyle()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(f"{time_text} {ev.title}")
        self.setAccessibleDescription("Press Enter to show or hide details")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 4, 0)
        lay.setSpacing(6)
        stripe = QFrame()
        stripe.setObjectName("stripe")
        stripe.setFixedWidth(4)
        stripe.setStyleSheet(f"background:{color};border-radius:2px;")
        lay.addWidget(stripe)
        self._body = QVBoxLayout()
        self._body.setContentsMargins(0, 0, 0, 0)
        self._body.setSpacing(0)
        self.head = QWidget()
        self.head.setObjectName("rowhead")
        self.head_layout = QHBoxLayout(self.head)
        self.head_layout.setContentsMargins(0, 0, 0, 0)
        self.head_layout.setSpacing(6)
        col = QVBoxLayout()
        col.setContentsMargins(0, 3, 0, 3)
        col.setSpacing(0)
        top = QLabel(f"<b>{time_text}</b>  {_esc(ev.title)}")
        top.setObjectName("title")
        top.setTextFormat(Qt.TextFormat.RichText)
        top.setWordWrap(True)
        sub = QLabel(ev.calendar_name)
        sub.setObjectName("calname")
        sub.setTextFormat(Qt.TextFormat.PlainText)
        sub.setStyleSheet(f"color:{theme.MUTED};font-size:10px;")
        col.addWidget(top)
        col.addWidget(sub)
        self.head_layout.addLayout(col, 1)
        self._body.addWidget(self.head)
        lay.addLayout(self._body, 1)

    def _restyle(self) -> None:
        """Selected: gray-blue with a light border; next up (not selected): blue."""
        if self._selected:
            bg, border = BG_SELECTED, theme.MUTED
        else:
            bg, border = (theme.BG_NEXT if self.is_next else "transparent"), "transparent"
        self.setStyleSheet(f"QFrame#row{{background:{bg};border-radius:4px;"
                           f"border:1px solid {border};}}"
                           f"QFrame#row:focus{{border:1px solid {theme.LINK};}}")

    def is_selected(self) -> bool:
        return self._selected

    def set_selected(self, selected: bool) -> None:
        if selected != self._selected:
            self._selected = selected
            self._restyle()

    def mousePressEvent(self, e):
        e.accept()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(
                e.position().toPoint()):
            self.toggle_requested.emit(self, False)
        e.accept()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.toggle_requested.emit(self, True)
            e.accept()
            return
        super().keyPressEvent(e)
