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

from PyQt6.QtCore import (QAbstractAnimation, QEvent, QParallelAnimationGroup,
                          QPoint, QPropertyAnimation, QRect, QRectF, QSize, Qt, QTimer,
                          QUrl, pyqtProperty, pyqtSignal)
from PyQt6.QtGui import QColor, QCursor, QDesktopServices, QGuiApplication, QPainter, QPen
from PyQt6.QtWidgets import (QApplication, QFrame, QGraphicsOpacityEffect, QHBoxLayout,
                             QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from .. import __version__, agenda
from ..textutil import meeting_label, plain_line, safe_http_url
from . import meeting_menus, motion, nativebg, placement, theme
from .widgets import HoverButton

log = logging.getLogger(__name__)

WIDTH = 250
MARGIN = 8
MAX_HEIGHT = 480
WATCHDOG_MS = 1500            # visible but inactive with no menu/modal up: close the group
RECLAIM_CHECK_MS = 250        # after taking focus back from the flyout: did it work?
QWIDGETSIZE_MAX = 16777215
BG_SELECTED = "#3a3f4b"       # the selected meeting (the next-up one stays BG_NEXT blue)
NOTICE_MS = 8000              # the "Hidden." notice (and its Undo) clears itself after this
NO_ACCOUNTS_TEXT = "No calendars connected — right-click the tray icon > Add Google account…"
ALL_HIDDEN_TEXT = "All accounts hidden — right-click the tray icon > Accounts"


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
        # v0.8.4: the slide settles (a slight overshoot, clamped to the work area, see
        # _set_show_pos); the fade eases out plainly (never past 1.0). It starts once
        # the first frame is painted (_show_pending; see show_near_tray).
        self._show_anim = QParallelAnimationGroup(self)
        self._show_slide = QPropertyAnimation(self, b"show_pos")
        self._show_slide.setEasingCurve(motion.curve(motion.SETTLE))
        self._show_fade = QPropertyAnimation(self, b"windowOpacity")
        self._show_fade.setEasingCurve(motion.curve(motion.FADE))
        for a in (self._show_slide, self._show_fade):
            self._show_anim.addAnimation(a)
        self._show_pending = False
        self.show_start_fallback = QTimer(self)
        self.show_start_fallback.setSingleShot(True)
        self.show_start_fallback.setInterval(motion.SHOW_START_FALLBACK_MS)
        self.show_start_fallback.timeout.connect(self._start_show_animation)
        self.last_hidden_at = -1e9
        self.last_hidden_cursor = QPoint()
        self._anchored = False
        self._final_height: int | None = None
        self._tray_geom: QRect | None = None
        self._statuses: dict[str, str] = {}
        self._rows: list[RowInfo] = []
        self._next_line = ""
        self._no_accounts = False
        self._accounts: list[str] | None = None
        # v0.8.0 motion: the popup's height tween, widgets sliding open/closed (notice,
        # chat), rows collapsing after a hide, and rows fading in. All stopped on hide.
        self._tween: dict | None = None     # the running height tween (see _start_tween)
        self._geom_change = False           # inside our own one-call geometry change
        self._fade_anims: list = []
        self._chat_open = False
        self._chat = None
        self._chat_factory = None
        self._ask = None
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint)
        self.setFixedWidth(WIDTH)
        self.setObjectName("popup")
        # One look for the popup and the details panel: background and 1 px border.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setStyleSheet(theme.window_style("popup"))
        outer = QVBoxLayout(self)
        # The window size is set by _resize/_set_height only (measured, then one
        # geometry change); the layout never resizes the window by itself.
        outer.setSizeConstraint(QVBoxLayout.SizeConstraint.SetNoConstraint)
        outer.setContentsMargins(10, 10, 10, 8)
        outer.setSpacing(6)
        self._header = QLabel()
        self._header.setStyleSheet("font-weight:bold;font-size:13px;")
        self._status = QLabel()
        self._status.setTextFormat(Qt.TextFormat.PlainText)
        self._status.setStyleSheet(f"color:{theme.WARN};font-size:11px;")
        self._status.setWordWrap(True)
        self._status.hide()
        # One line under the header after a hide or a failed save: the text and, for a
        # hide, an Undo button. Part of the layout (its height is counted in the list's
        # height budget, see _resize); it clears itself after NOTICE_MS.
        self._notice_box = QWidget()
        self._notice_box.setObjectName("noticebox")
        self._notice_box.setStyleSheet("background:transparent;")
        nl = QHBoxLayout(self._notice_box)
        nl.setContentsMargins(0, 0, 0, 0)
        nl.setSpacing(6)
        self._notice = QLabel()
        self._notice.setObjectName("notice")
        self._notice.setTextFormat(Qt.TextFormat.PlainText)
        self._notice.setStyleSheet(f"color:{theme.MUTED};font-size:11px;")
        self._notice.setWordWrap(True)
        self._notice_undo = QPushButton("Undo")
        self._notice_undo.setObjectName("undo")
        self._notice_undo.setCursor(Qt.CursorShape.PointingHandCursor)
        self._notice_undo.setStyleSheet(
            f"QPushButton{{background:{theme.BORDER};color:{theme.TEXT};font-size:11px;"
            "padding:1px 8px;border:none;border-radius:3px;}"
            "QPushButton:hover{background:#4a4d51;}"
            f"QPushButton:focus{{border:1px solid {theme.LINK};}}")
        self._notice_undo.clicked.connect(lambda _c=False: self._undo_notice())
        self._notice_undo.hide()
        nl.addWidget(self._notice, 1)
        nl.addWidget(self._notice_undo, 0, Qt.AlignmentFlag.AlignVCenter)
        self._notice_box.hide()
        self._notice_shown = False
        self._notice_action = None
        self.notice_timer = QTimer(self)
        self.notice_timer.setSingleShot(True)
        self.notice_timer.setInterval(NOTICE_MS)
        self.notice_timer.timeout.connect(lambda: self.show_notice(""))
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
        for w in (self._header, self._status, self._notice_box, self._next, self._scroll,
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
            self._ask.show()            # now, not on Qt's deferred show (see _refresh)
            self._resize(animate=False)

    def chat(self):
        return self._chat

    def chat_expanded(self) -> bool:
        """The chat is open (or opening); False while it collapses."""
        return self._chat is not None and self._chat_open

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
            self._chat.hide()
            self._outer.insertWidget(self._outer.indexOf(self._footer), self._chat)
            expand = True
        else:
            expand = not self._chat_open
        self._chat_open = expand
        self._ask.setText("Hide chat" if expand else "Ask Claude…")
        # Slides open (or closed) with the popup growing or shrinking along with it.
        self._reveal(self._chat, expand)
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

    def set_accounts(self, accounts) -> None:
        """The connected accounts (run.py: token files). Until set, the accounts that
        have calendars in the cache."""
        self._accounts = list(accounts)
        self._no_accounts = not self._accounts

    def _known_accounts(self) -> list[str]:
        if self._accounts is not None:
            return list(self._accounts)
        return list(self.cache.calendars_by_account())

    def hidden_account_count(self) -> int:
        is_hidden = getattr(self.settings, "is_account_hidden", None)
        if is_hidden is None:
            return 0
        return sum(1 for a in self._known_accounts() if is_hidden(a))

    def all_accounts_hidden(self) -> bool:
        accounts = self._known_accounts()
        return bool(accounts) and self.hidden_account_count() == len(accounts)

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
        items = (self._rows_box.itemAt(i) for i in range(self._rows_box.count()))
        return [it.widget() for it in items if it.widget() is not None]

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

    def refresh(self, now: datetime | None = None, fade_in_new: bool = False) -> None:
        """Rebuild the list from the cache. `fade_in_new` (user actions only: Undo, a
        restore or an account shown again from the tray): rows that were not there
        before fade in. The 30 s tick and syncs never pass it."""
        self._finish_collapse()        # a hide collapsing its rows: done now
        before = {_row_key(r.event) for r in self.row_widgets()
                  if isinstance(r, AgendaRow)} if fade_in_new else None
        self._refreshing = True        # rows are rebuilt: list scrolling means nothing yet
        try:
            self._refresh(now)
        finally:
            self._refreshing = False
        if before is not None:
            self._fade_in([r for r in self.row_widgets() if isinstance(r, AgendaRow)
                           and _row_key(r.event) not in before])
        self._place_flyout()
        self._sync_hover()

    def _sync_hover(self) -> None:
        """After a rebuild the rows may have moved under a still cursor: exactly the row
        under it looks hovered (set at once, no fade)."""
        try:
            pos = self.cursor_pos()
        except Exception:  # noqa: BLE001 - a hover look never breaks a refresh
            return
        for r in self.row_widgets():
            if isinstance(r, AgendaRow):
                inside = self.isVisible() and r.isVisible() \
                    and r.rect().contains(r.mapFromGlobal(pos))
                r.set_hovered(inside, animate=False)

    def _refresh(self, now: datetime | None) -> None:
        now = now or self.now()
        events = agenda.today(self.cache, self.settings, now)
        nxt = agenda.next_event(self.cache, self.settings, now)
        self._header.setText(f"Today · {now:%a, %b} {now.day}")
        self._next_line = f"Next: {agenda.describe_next(nxt, now)}" if nxt else ""
        self._next.setText(self._next_line)
        self._next.setVisible(bool(nxt))

        hovered_key = next((_row_key(r.event) for r in self.row_widgets()
                            if isinstance(r, AgendaRow) and r.is_hovered()), None)
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
            key = _row_key(ev)
            # Built in their final look (selected, under the mouse): a refresh that
            # changed nothing starts no fade at all.
            row = self._make_row(ev, time_text, is_next,
                                 selected=self._selected_key == key,
                                 hovered=key == hovered_key)
            self._rows_box.addWidget(row)
            # Shown now, not on Qt's deferred show: a row added to a visible popup stays
            # hidden until the next event-loop pass, and a layout skips hidden widgets,
            # so _resize below would measure an empty list and size it to one row.
            row.show()
            if self._selected_key is not None and key == self._selected_key:
                selected = row
        # Rows stay at the top; spare list height stays below them (never stretches
        # rows while the list is taller than its rows, e.g. during a shrink).
        self._rows_box.addStretch(1)
        if selected is not None:   # a refresh keeps the selected meeting's details open
            if self.flyout_open():
                # Rebuilt only if what it shows changed (the 30 s tick and syncs mostly
                # change nothing: scroll position, selection and presses survive).
                self._flyout.set_event(selected.event)
        elif self._selected_key is not None:
            self.close_flyout(restore_focus=False)   # gone: hidden, deleted, synced away
        if events:
            self._empty.hide()
        else:
            if self._no_accounts:
                text = NO_ACCOUNTS_TEXT
            elif self.all_accounts_hidden():
                text = ALL_HIDDEN_TEXT
            else:
                text = "Nothing scheduled today"
                n = self.hidden_account_count()
                if n:
                    text += f" ({n} account{'s' if n != 1 else ''} hidden)"
            self._empty.setText(text)
            self._empty.show()
        self._scroll.setVisible(bool(events))
        self._resize()

    # -- measuring, and the popup's height tween ---------------------------------------
    def _measure(self) -> tuple[int | None, int | None, int]:
        """Measure the final layout once: (list height or None when the list is not
        used, window height or None without height-for-width, list content height).
        Leaves the list at its final fixed height; never touches the window geometry
        (the popup's layout has no size constraint: the window size is ours to set)."""
        lay = self.layout()
        final_list, content_h = None, 0
        if not (self._scroll.isHidden() and not self._rows):
            max_total = min(MAX_HEIGHT, self._area().height() - 24)
            self._scroll.setFixedHeight(0)
            lay.activate()
            # Everything but the list (header, status, notice, next line, chat, footer),
            # word-wrapped lines measured at the popup's real width.
            chrome = (lay.totalHeightForWidth(WIDTH) if lay.hasHeightForWidth()
                      else lay.sizeHint().height())
            inner_w = WIDTH - 20
            rows_lay = self._rows_box
            # Measured at the list's real width when the rows wrap (the plain size hint
            # assumes other line counts: a list that fits would get a blank band).
            content_h = (rows_lay.totalHeightForWidth(inner_w) if rows_lay.hasHeightForWidth()
                         else rows_lay.sizeHint().height())
            final_list = max(40, min(content_h, max_total - chrome))
            self._scroll.setFixedHeight(final_list)
        lay.activate()
        final_h = None
        if lay.hasHeightForWidth():
            # Not adjustSize(): it measures wrapped lines at the layout's preferred
            # width (wider than the popup's fixed WIDTH), so a wrapped "Next:" line or
            # notice got fewer lines than it needs and the popup came out too short.
            final_h = max(lay.totalHeightForWidth(WIDTH), lay.minimumSize().height())
        return final_list, final_h, content_h

    def _can_tween(self) -> bool:
        return (self.isVisible() and self._anchored and not self.showing_animated()
                and motion.micro_enabled())

    def _resize(self, animate: bool = True) -> None:
        """Cap the list height (header/footer stay visible), then re-anchor if shown.
        The final size is always measured first (rows already shown, see _refresh);
        with `animate` and the micro-animations on, a shown popup whose list changed
        height tweens there (the list absorbs the change; the edge next to the tray
        stays put). An unchanged height never animates."""
        self._stop_tween()
        h0, l0 = self.height(), self._scroll.height()
        list_before = not self._scroll.isHidden()
        final_list, final_h, content_h = self._measure()
        self._final_height = final_h
        if (animate and final_h is not None and final_list is not None and list_before
                and final_h != h0 and h0 - l0 == final_h - final_list and self._can_tween()):
            self._scroll.setFixedHeight(l0)
            self._start_tween("list", h0, final_h, l0, final_list, content_h <= final_list)
            return
        if final_h is not None:
            self._set_height(final_h)
        else:
            self.adjustSize()
            if self.isVisible() and self._anchored:
                self._place()

    def _set_height(self, h: int) -> None:
        """The window at height `h`, docked (compute_position for that size): ONE
        native geometry change (never a resize then a move, which made the tray-side
        edge bounce); newly exposed pixels are painted in the same frame. The details
        panel follows once."""
        size = QSize(WIDTH, int(h))
        if not (self.isVisible() and self._anchored):
            self.resize(size)
            return
        pos = placement.compute_position(size, self._tray_geom, self._area())
        if self.showing_animated():             # the slide owns the position
            self.resize(size)
            self._show_slide.setEndValue(pos)
            self._place_flyout()
            return
        grow = size.height() > self.height()
        if QRect(pos, size) != self.geometry():
            self._geom_change = True
            try:
                self.setGeometry(QRect(pos, size))
            finally:
                self._geom_change = False
            self._place_flyout()
        if grow:
            self.repaint()

    def final_height(self) -> int:
        """The height the popup has, or is tweening to."""
        return self._final_height if self._final_height is not None else self.height()

    def height_animating(self) -> bool:
        return self._tween is not None and motion.alive(self._tween["anim"])

    def height_animation(self):
        """The running height tween (QVariantAnimation, progress 0..1) or None."""
        return self._tween["anim"] if self._tween is not None else None

    def _start_tween(self, kind: str, h0: int, h1: int, l0: int, l1: int, fits: bool,
                     frame=None, finish=None, w=None) -> None:
        """Tween the window from h0 to h1 and the list from l0 to l1 (both linear in
        the eased progress, so they always add up), calling frame(e, f) for the widget
        being revealed or the rows collapsing. Only geometry changes per frame: the
        final layout was measured before. `finish` runs once, at the end or when the
        tween is interrupted (it snaps its widgets to their final state)."""
        ms = motion.micro_duration(motion.NORMAL)
        if fits:
            # The final list shows every row: no scrollbar flashing in between.
            self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._tween = {"kind": kind, "h0": h0, "h1": h1, "l0": l0, "l1": l1,
                       "frame": frame, "finish": finish, "w": w, "anim": None}
        tw = self._tween
        # Linear progress: each frame applies the settle curve to the geometry and the
        # plain ease-out to the opacity (an overshoot never takes opacity past 0..1).
        tw["anim"] = motion.tween(self, 0.0, 1.0, ms, lambda t, x=tw: self._tween_frame(x, t),
                                  lambda x=tw: self._tween_done(x), kind=motion.LINEAR)

    def _tween_frame(self, tw, t) -> None:
        if self._tween is not tw:
            return
        e = motion.ease(motion.SETTLE, t)
        f = motion.ease(motion.FADE, t)
        h0, h1 = tw["h0"], tw["h1"]
        h = round(h0 + (h1 - h0) * e)
        # The overshoot never takes the window past the screen's room (its tray-side
        # edge stays put, the far edge must stay on screen) nor below 1 px.
        room = max(h0, h1, min(MAX_HEIGHT, self._area().height() - 24))
        h = int(motion.clamp(h, 1, room))
        if h1 != h0:
            e = (h - h0) / (h1 - h0)        # the list follows the clamped height exactly
        if tw["frame"] is not None:
            tw["frame"](e, f)
        self._scroll.setFixedHeight(max(0, round(tw["l0"] + (tw["l1"] - tw["l0"]) * e)))
        self._set_height(h)

    def _tween_done(self, tw) -> None:
        if self._tween is not tw:
            return
        self._end_tween(tw)             # runs its finish (which may start a new tween:
        if self._tween is None:         # a collapse's notice slides in; never snap it)
            self._resize(animate=False)     # lands exactly on the measured final layout
            self._sync_hover()          # the rows may now sit elsewhere under the mouse

    def _end_tween(self, tw) -> None:
        self._tween = None
        motion.stop(tw["anim"])
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        if tw["finish"] is not None:
            tw["finish"]()

    def _stop_tween(self) -> None:
        """Interrupt the running tween (if any): its widgets snap to their final state."""
        tw = self._tween
        if tw is not None:
            self._end_tween(tw)

    # -- widgets sliding open / closed, rows collapsing and fading in ----------------
    def _natural_height(self, w) -> int:
        w.ensurePolished()
        inner = WIDTH - 20
        if w.hasHeightForWidth():
            return max(1, w.heightForWidth(inner))
        return max(1, w.sizeHint().height())

    def revealing(self, w=None) -> bool:
        tw = self._tween
        if tw is None or tw["kind"] != "reveal" or not motion.alive(tw["anim"]):
            return False
        return w is None or tw["w"] is w

    def _reveal(self, w, show: bool, on_done=None) -> None:
        """Show (or hide) `w` inside the popup: its height opens from where it is to its
        natural height (or closes to 0) while it fades, motion.NORMAL; the window's
        height follows. Reversing mid-way continues from the current height and
        opacity. Without the micro-animations (or with the popup hidden) at once."""
        fx = w.graphicsEffect()
        cur_op = fx.opacity() if isinstance(fx, QGraphicsOpacityEffect) else (
            0.0 if w.isHidden() else 1.0)
        cur_h = 0 if w.isHidden() else w.height()
        self._stop_tween()                      # (snaps a running reveal: restored below)
        if not self._can_tween() or (not show and w.isHidden() and cur_h == 0):
            self._clear_reveal(w)
            w.setVisible(show)
            if on_done is not None:
                on_done()
            self._resize()
            return
        h0, l0 = self.height(), self._scroll.height()
        # The final layout, measured once.
        self._clear_reveal(w)
        w.setVisible(show)
        natural = self._natural_height(w) if show else 0
        l1, h1, content_h = self._measure()
        self._final_height = h1
        if l1 is None or h1 is None:
            if on_done is not None:
                on_done()
            self._resize(animate=False)
            return
        # Back to where it is now.
        self._scroll.setFixedHeight(l0)
        w.show()
        w.setMinimumHeight(1)                   # lets the layout give it less
        w.setMaximumHeight(max(1, cur_h))
        fx = QGraphicsOpacityEffect(w)
        fx.setOpacity(cur_op)
        w.setGraphicsEffect(fx)
        self.layout().activate()
        target_op = 1.0 if show else 0.0

        def frame(e, f, w=w, fx=fx) -> None:
            try:
                w.setMaximumHeight(max(1, round(cur_h + (natural - cur_h) * e)))
                fx.setOpacity(motion.clamp(cur_op + (target_op - cur_op) * f, 0.0, 1.0))
            except RuntimeError:
                pass

        def finish(w=w) -> None:
            self._clear_reveal(w)
            if not show:
                w.hide()
            if on_done is not None:
                on_done()

        self._start_tween("reveal", h0, h1, l0, l1, content_h <= l1, frame, finish, w)

    @staticmethod
    def _clear_reveal(w) -> None:
        try:
            w.setMaximumHeight(QWIDGETSIZE_MAX)
            w.setMinimumHeight(0)
            w.setGraphicsEffect(None)
        except RuntimeError:
            pass

    def _end_all_reveals(self) -> None:
        """Hidden: every widget jumps to its final state (shown or hidden)."""
        self._stop_tween()
        if self._chat is not None:
            self._clear_reveal(self._chat)
            self._chat.setVisible(self._chat_open)
        self._clear_reveal(self._notice_box)
        self._notice_box.setVisible(self._notice_shown)
        if not self._notice_shown:
            self._notice.setText("")

    def collapsing(self) -> bool:
        tw = self._tween
        return tw is not None and tw["kind"] == "collapse" and motion.alive(tw["anim"])

    def _collapse_rows(self, rows, on_done) -> None:
        """Rows just hidden by the owner shrink to nothing while fading (motion.NORMAL),
        the list and window shrinking with them; then `on_done` rebuilds the list. At
        once without the micro-animations."""
        self._stop_tween()
        if not rows or not self._can_tween():
            on_done()
            return
        h0, l0 = self.height(), self._scroll.height()
        heights = [max(1, r.height()) for r in rows]
        # The list without them, computed (not by hiding them: the right-clicked row
        # usually has focus, and hiding it would move focus and scroll the list).
        rows_lay = self._rows_box
        inner_w = WIDTH - 20
        content_h = (rows_lay.totalHeightForWidth(inner_w) if rows_lay.hasHeightForWidth()
                     else rows_lay.sizeHint().height())
        all_rows = [r for r in self.row_widgets() if isinstance(r, AgendaRow)]
        left = len(all_rows) - len(rows)
        removed = sum(heights) + rows_lay.spacing() * (len(rows) if left > 0
                                                      else max(0, len(rows) - 1))
        content_h = max(0, content_h - removed)
        chrome = h0 - l0
        max_total = min(MAX_HEIGHT, self._area().height() - 24)
        l1 = max(40, min(content_h, max_total - chrome))
        h1 = chrome + l1
        self._final_height = h1
        fxs = []
        for r, h in zip(rows, heights):
            fx = QGraphicsOpacityEffect(r)
            r.setGraphicsEffect(fx)
            r.setMinimumHeight(1)
            fxs.append((r, fx, h))
        self.layout().activate()

        def frame(e, f) -> None:
            for r, fx, h in fxs:
                try:
                    r.setMaximumHeight(max(1, min(h, round(h * (1.0 - e)))))
                    fx.setOpacity(motion.clamp(1.0 - f, 0.0, 1.0))
                except RuntimeError:
                    pass

        self._start_tween("collapse", h0, h1, l0, l1, content_h <= l1, frame, on_done)

    def _finish_collapse(self) -> None:
        """End a running collapse (finished, or interrupted by a refresh or a hide) and
        run its follow-up once."""
        if self.collapsing():
            self._stop_tween()

    def _fade_in(self, rows) -> None:
        self._fade_anims = [a for a in self._fade_anims if motion.alive(a)]
        ms = motion.micro_duration(motion.NORMAL) if rows and self.isVisible() else 0
        if not ms:
            return
        for r in rows:
            fx = QGraphicsOpacityEffect(r)
            fx.setOpacity(0.0)
            r.setGraphicsEffect(fx)

            def step(v, fx=fx) -> None:
                try:
                    fx.setOpacity(float(v))
                except RuntimeError:
                    pass

            def done(r=r) -> None:
                try:
                    r.setGraphicsEffect(None)
                except RuntimeError:
                    pass
            self._fade_anims.append(motion.tween(r, 0.0, 1.0, ms, step, done,
                                                 kind=motion.FADE))

    def fading_in(self) -> bool:
        return any(motion.alive(a) for a in self._fade_anims)

    def row_fades_running(self) -> bool:
        """Any row (hover/press/selection) or its Join button still fading."""
        for r in self.row_widgets():
            if isinstance(r, AgendaRow) and r.fading():
                return True
        return False

    def animations_running(self) -> bool:
        """Any of the popup's own animations (opening slide, height, reveal, collapse,
        fade-in, row and button fades) still running."""
        return (self.showing_animated() or self.height_animating() or self.revealing()
                or self.collapsing() or self.fading_in() or self.row_fades_running())

    def tile_style(self) -> str:
        style = getattr(self.settings, "tile_style", "stripe")
        return style if style in theme.TILE_STYLES else "stripe"

    def _make_row(self, ev, time_text: str, is_next: bool, selected: bool = False,
                  hovered: bool = False) -> "AgendaRow":
        row = AgendaRow(ev, time_text, is_next, theme.effective_color(ev, self.settings),
                        style=self.tile_style(), selected=selected, hovered=hovered)
        url = getattr(ev, "meeting_url", None)
        if safe_http_url(url) is not None:     # same check as the click handler
            if row.style == "fill":
                # On any fill: the button in the text color, its label in the fill
                # color (the same >= 4.5:1 pair as the row's text); hover and press
                # shade it only as far as that pair stays >= 4.5:1.
                hover = theme.shade_keeping_contrast(row.fg, row.color, row.color)
                pressed = theme.shade_keeping_contrast(row.fg, row.color, row.color,
                                                       (0.25, 0.18, 0.1, 0.05))
                colors = (row.fg, hover, row.color)
            else:
                colors, pressed = (theme.LINK, "#a9c8ff", "#0b0d10"), None
            join = HoverButton("Join", *colors, pressed=pressed, radius=3,
                               extra="font-size:11px;font-weight:bold;padding:2px 8px;",
                               parent=row)
            join.setObjectName("join")
            join.setToolTip(meeting_label(url))
            join.setCursor(Qt.CursorShape.PointingHandCursor)
            join.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            join.clicked.connect(lambda _c=False, u=url: self._join(u))
            row.head_layout.addWidget(join, 0, Qt.AlignmentFlag.AlignVCenter)
            row.join_button = join
        row.toggle_requested.connect(self.toggle_row)
        row.menu_requested.connect(self.show_row_menu)
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
        """The panel finished sliding back and hid. If it had focus then (e.g. clicked
        into while sliding back), the popup takes focus back, as close_flyout does, and
        the reclaim check starts over, even if one was already running: that one may
        have been started before the panel took focus again (RECLAIM_CHECK_MS is longer
        than the slide), and nothing else would ask for the popup's activation."""
        if was_active and self.isVisible():
            self.raise_()
            self.activateWindow()
            self.reclaim_check.start()

    def _ensure_flyout(self):
        if self._flyout is None:
            from .flyout import DetailsFlyout
            self._flyout = DetailsFlyout(self)
        return self._flyout

    def _place_flyout(self, force: bool = False) -> None:
        """The popup's width, top aligned with it, as tall as its content (v0.8.0; at
        most the popup's height, the description then scrolls): left of the popup inside
        the work area of its screen (right of it, or over it, when the left has no room).
        Uses the popup's docked geometry, never a position mid-animation. While sliding,
        only the slide's end moves (the slide owns the panel's position). Its height
        tweens to a new content height, but simply follows the popup's own tween."""
        fly = self._flyout
        if fly is None or (fly.isHidden() and not force):
            return
        anchor = self.docked_geometry()
        area = self._area()
        want = fly.target_height() if fly.closing() else fly.content_height()
        size = QSize(WIDTH, max(1, min(anchor.height(), want)))
        animate = not (self.height_animating() or self.revealing() or self.collapsing())
        pos, side = placement.beside(size, anchor, anchor.top(), area)
        if fly.closing():
            if side != "over":          # sliding back: follow the popup
                fly.place(size, pos, QPoint(anchor.left(), pos.y()), anchor, animate)
            return
        row = self.selected_row()
        if row is None:
            return
        self.flyout_side = side
        if side == "over":
            pos, size = self._overlay_clear_of(row, size, pos, area, anchor)
            fly.place(size, pos, pos, None, animate)
            return
        # Tucked: exactly behind the popup, which it slides out from and back behind.
        fly.place(size, pos, QPoint(anchor.left(), pos.y()), anchor, animate)

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
        if not self._geom_change:       # _set_height places the panel once itself
            self._place_flyout()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if not self._geom_change:
            self._place_flyout()

    # -- the right-click menu ---------------------------------------------------
    def menu(self):
        return self._menu

    def _open_menu(self, menu, global_pos: QPoint) -> None:
        """Every menu of the group (a row's right-click menu) goes through here: one open
        menu at a time, owned by the popup (a refresh or a content swap never deletes
        it), settled when it closes."""
        if self._menu is not menu:
            meeting_menus.discard(self._menu)
        self._menu = menu
        menu.aboutToHide.connect(self._settle_after_close)
        meeting_menus.delete_when_closed(menu)
        menu.popup(global_pos)

    def show_row_menu(self, row: "AgendaRow", global_pos: QPoint) -> None:
        """Right-click on a row: Hide / Hide series / Color ▸ / Color style ▸. Never
        selects the row."""
        if not self.isVisible():
            return
        ev = row.event
        self._open_menu(meeting_menus.context_menu(
            self, ev, self.settings.event_color(ev),
            lambda scope, e=ev: self.hide_meeting(e, scope),
            lambda color, e=ev: self.color_meeting(e, color),
            self.tile_style(), self.set_tile_style), global_pos)

    def set_tile_style(self, style: str) -> None:
        """Side stripe / Whole tile, from the right-click menu: applies at once here and
        (meetings_changed) everywhere else."""
        try:
            self.settings.set_tile_style(style)
        except OSError:
            self.show_notice(meeting_menus.SAVE_FAILED)
            return
        self.refresh()
        self.meetings_changed.emit()

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
            # Focus is on its way back from the flyout (Esc, a hide): for a moment no
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

    def hide_meeting(self, ev, scope: str) -> None:
        """Hide at once (no confirmation); the notice offers Undo."""
        # A hide still collapsing finishes first (its rows go, its notice shows), so
        # this one is measured from the real list and collapses smoothly too.
        self._finish_collapse()
        try:
            key = self.settings.hide_event(ev, scope)
        except OSError:                 # rolled back by Settings; say so, change nothing
            self.show_notice(meeting_menus.SAVE_FAILED)
            return
        row = self.selected_row()
        if row is not None and self.settings.is_event_hidden(row.event):
            self.close_flyout(restore_focus=False)   # its meeting is gone
        self.meetings_changed.emit()
        gone = [r for r in self.row_widgets() if isinstance(r, AgendaRow)
                and self.settings.is_event_hidden(r.event)]

        def finish(k=key) -> None:
            self.refresh()
            self.show_notice(meeting_menus.HIDDEN_NOTICE, undo=lambda: self._unhide(k))
        self._collapse_rows(gone, finish)

    def _unhide(self, key: str) -> None:
        try:
            self.settings.unhide(key)
        except OSError:
            self.show_notice(meeting_menus.SAVE_FAILED)
            return
        self.show_notice("")
        self.refresh(fade_in_new=True)
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

    def show_notice(self, text: str, undo=None) -> None:
        """Show `text` (empty: clear) under the header, with an Undo button when `undo`
        is given. A notice clears itself after NOTICE_MS. It slides open and fades in
        (and out) with the micro-animations on."""
        text = plain_line(text)
        was_shown = self._notice_shown
        self._notice_shown = bool(text)
        self._notice_action = undo if text else None
        self._notice_undo.setVisible(self._notice_action is not None)
        if text:
            self._notice.setText(text)
            self.notice_timer.start()
        else:
            self.notice_timer.stop()
        if bool(text) != was_shown:
            self._reveal(self._notice_box, bool(text),
                         on_done=None if text else self._clear_notice_text)
        else:
            self._resize()

    def _clear_notice_text(self) -> None:
        if not self._notice_shown:      # (a new notice may have come in meanwhile)
            self._notice.setText("")

    def notice_text(self) -> str:
        return self._notice.text() if self._notice_shown else ""

    def notice_undo_button(self) -> QPushButton | None:
        """The notice's Undo button while it is offered, else None."""
        if self._notice_shown and not self._notice_undo.isHidden():
            return self._notice_undo
        return None

    def _undo_notice(self) -> None:
        action, self._notice_action = self._notice_action, None
        if action is not None:
            action()


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
        """The opening animation is running, or set up and waiting for the first
        painted frame (the slide owns the position either way)."""
        return self._show_pending or self._show_anim.state() == QAbstractAnimation.State.Running

    def _get_show_pos(self) -> QPoint:
        return self.pos()

    def _set_show_pos(self, pos: QPoint) -> None:
        # The settle overshoot never takes the popup past the work area (over the
        # taskbar or onto another screen); the last frame is exactly the docked spot.
        if self._anchored:
            pos = motion.clamp_to_area(pos, self.size(), self._area())
        self.move(pos)

    show_pos = pyqtProperty(QPoint, fget=_get_show_pos, fset=_set_show_pos)

    def show_near_tray(self, tray_geometry) -> None:
        self._tray_geom = QRect(tray_geometry)
        self._anchored = True
        was_visible = self.isVisible()
        # Styled before measuring: the very first open measures (and places) exactly
        # like every later one.
        self.ensurePolished()
        self._resize(animate=False)  # right screen; places only when visible
        self._place()
        ms = 0 if was_visible else motion.duration(motion.POPUP_MS)
        if ms:
            # Slides in from the tray side (away from the taskbar) while fading in. The
            # first paint is not delayed; the animation starts once that frame is
            # painted (v0.8.4: the very first open paints slowly, and an animation
            # started before it was already over when the popup first appeared).
            docked = self.pos()
            area = self._area()
            edge = motion.tray_edge(self._tray_geom, area)
            # Starts inside the work area: never over the taskbar or another monitor.
            start = docked + motion.slide_in_offset(edge)
            self.move(motion.clamp_to_area(start, self.size(), area))
            self.setWindowOpacity(0.0)
            self._show_slide.setDuration(ms)
            self._show_slide.setStartValue(self.pos())
            self._show_slide.setEndValue(docked)
            self._show_fade.setDuration(ms)
            self._show_fade.setStartValue(0.0)
            self._show_fade.setEndValue(1.0)
            self._show_pending = True
        self.show()
        self.raise_()
        self.activateWindow()
        if ms and self.isVisible():     # it may have hidden again during show/activate
            if self._show_pending:
                self.show_start_fallback.start()    # (no paint seen: start anyway)
        elif ms:
            self._stop_show_animation()

    def _first_frame_painted(self) -> None:
        """A paint of the shown popup while the opening waits: start it right after
        this frame is on screen (the next event-loop pass, once the paint is flushed)."""
        if self._show_pending:
            QTimer.singleShot(0, self._start_show_animation)

    def _start_show_animation(self) -> None:
        if not self._show_pending:
            return
        self._show_pending = False
        self.show_start_fallback.stop()
        if not self.isVisible():
            self._stop_show_animation()
            return
        self._show_slide.setStartValue(self.pos())   # where the first frame showed it
        self._show_anim.start()

    def _stop_show_animation(self) -> None:
        """Hidden (or shown again) mid-animation: no animation left running, fully
        opaque, and docked if still shown."""
        if self.showing_animated():
            self._show_pending = False
            self.show_start_fallback.stop()
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
        elif e.type() == QEvent.Type.Paint and self._show_pending:
            self._first_frame_painted()
        return super().event(e)

    def nativeEvent(self, event_type, message):
        # Never the base class's nativeEvent: its (False, None) crashes Qt (see nativebg).
        return nativebg.native_event(event_type, message, theme.BG)

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
            self._reactivate_after(menu)    # a row's right-click menu
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
        # Every animation stops; everything jumps to its final state (no handler runs
        # after the hide, nothing keeps a timer going).
        self._stop_tween()              # a collapse's follow-up (the hide) still runs
        for a in self._fade_anims:
            motion.stop(a)
        self._fade_anims = []
        for r in self.row_widgets():
            if isinstance(r, AgendaRow) and r.graphicsEffect() is not None:
                r.setGraphicsEffect(None)
        self._notice_shown = False
        self._notice_action = None
        self.notice_timer.stop()
        self._end_all_reveals()
        self._notice_box.hide()
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
    it and shows its details in the flyout (again: closes them); Join never selects.

    It paints its own background so hover, press and selection can fade (motion.FAST):
    - "stripe" style: a colored bar on the left; next up blue, selected gray-blue with a
      light border, hover a faint lift;
    - "fill" style: the whole tile in the meeting's color, text black or white (WCAG
      >= 4.5:1); selected = a 2 px ring in the text color, next up = a thin ring."""

    toggle_requested = pyqtSignal(object, bool)   # row, by keyboard
    menu_requested = pyqtSignal(object, QPoint)   # row, global position (right-click)

    def __init__(self, ev, time_text: str, is_next: bool, color: str, parent=None,
                 style: str = "stripe", selected: bool = False, hovered: bool = False):
        super().__init__(parent)
        self.event = ev
        self.join_button: QPushButton | None = None
        self.is_next = is_next
        self.color = color
        self.style = style if style in theme.TILE_STYLES else "stripe"
        self.fg = theme.text_on(color) if self.style == "fill" else theme.TEXT
        # Built in its final state: no fade when a refresh re-creates a selected row or
        # the row under the mouse.
        self._selected, self._hovered, self._pressed = bool(selected), bool(hovered), False
        self._bg_anim = None
        self.setObjectName("row")
        self._bg = QColor(self.target_background())
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(f"{time_text} {ev.title}")
        self.setAccessibleDescription("Press Enter to show or hide details")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0 if self.style == "stripe" else 8, 0, 4, 0)
        lay.setSpacing(6)
        # Every child is created with this row as its parent: a parentless widget that
        # is shown would open as a window of its own (v0.8.0 had that with the stripe).
        stripe = QFrame(self)
        stripe.setObjectName("stripe")
        stripe.setFixedWidth(4)
        stripe.setStyleSheet(f"background:{color};border-radius:2px;")
        lay.addWidget(stripe)
        if self.style != "stripe":
            stripe.hide()
        self._body = QVBoxLayout()
        self._body.setContentsMargins(0, 0, 0, 0)
        self._body.setSpacing(0)
        self.head = QWidget(self)
        self.head.setObjectName("rowhead")
        self.head_layout = QHBoxLayout(self.head)
        self.head_layout.setContentsMargins(0, 0, 0, 0)
        self.head_layout.setSpacing(6)
        col = QVBoxLayout()
        col.setContentsMargins(0, 3, 0, 3)
        col.setSpacing(0)
        # Time and title only: the calendar (often just the owner's email) is in the
        # details panel when it says something; the stripe (or fill) shows the color.
        top = QLabel(f"<b>{time_text}</b>  {_esc(ev.title)}", self.head)
        top.setObjectName("title")
        top.setTextFormat(Qt.TextFormat.RichText)
        top.setWordWrap(True)
        if self.style == "fill":
            top.setStyleSheet(f"color:{self.fg};")
        col.addWidget(top)
        self.head_layout.addLayout(col, 1)
        self._body.addWidget(self.head)
        lay.addLayout(self._body, 1)

    # -- look ------------------------------------------------------------------
    def target_background(self) -> str:
        """The color the background is (or is fading) toward for the current state,
        as #rrggbb, or "transparent"."""
        if self.style == "fill":
            # Hover/press shade AWAY from the text color: the text only gains contrast.
            away = theme.ON_LIGHT if self.fg == theme.ON_DARK else theme.ON_DARK
            if self._pressed:
                return theme.mix(self.color, away, 0.22)
            if self._hovered:
                return theme.mix(self.color, away, 0.10)
            return QColor(self.color).name()
        if self._selected:
            base = BG_SELECTED
        elif self.is_next:
            base = theme.BG_NEXT
        elif self._pressed or self._hovered:
            return theme.mix(theme.BG, "#ffffff", 0.10 if self._pressed else 0.05)
        else:
            return "transparent"
        if self._pressed:
            return theme.mix(base, "#ffffff", 0.10)
        if self._hovered:
            return theme.mix(base, "#ffffff", 0.05)
        return base

    def background(self) -> QColor:
        """The background painted right now (mid-fade: in between)."""
        return QColor(self._bg)

    def _target_qcolor(self) -> QColor:
        target = self.target_background()
        if target == "transparent":
            c = QColor(theme.BG)
            c.setAlpha(0)
            return c
        return QColor(target)

    def _restyle(self) -> None:
        """Fade the background to the current state's color (at once when the
        micro-animations are off or the row is not on screen)."""
        motion.stop(self._bg_anim)
        self._bg_anim = None
        target = self._target_qcolor()
        ms = motion.micro_duration(motion.FAST) if self.isVisible() else 0
        if not ms or self._bg == target:
            self._set_bg(target)
            return
        self._bg_anim = motion.tween(self, QColor(self._bg), target, ms, self._set_bg,
                                     kind=motion.FADE)

    def _set_bg(self, color) -> None:
        self._bg = QColor(color)
        self.update()

    def border(self) -> tuple[str, int] | None:
        """(color, width) of the border drawn now, or None."""
        if self.style == "fill":
            if self._selected:
                return self.fg, 2
            if self.hasFocus():
                return self.fg, 1
            if self.is_next:
                c = QColor(self.fg)
                c.setAlpha(150)
                return c.name(QColor.NameFormat.HexArgb), 1
            return None
        if self.hasFocus():
            return theme.LINK, 1
        if self._selected:
            return theme.MUTED, 1
        return None

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(Qt.PenStyle.NoPen)
        if self._bg.alpha():
            p.setBrush(self._bg)
            p.drawRoundedRect(rect, 4, 4)
        border = self.border()
        if border is not None:
            color, width = border
            pen = QPen(QColor(color), width)
            if self.style == "fill" and width == 1 and self.hasFocus() \
                    and not self._selected:
                pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            inset = width / 2
            p.drawRoundedRect(QRectF(self.rect()).adjusted(inset, inset, -inset, -inset),
                              4, 4)
        p.end()

    # -- state -----------------------------------------------------------------
    def is_selected(self) -> bool:
        return self._selected

    def is_hovered(self) -> bool:
        return self._hovered

    def set_hovered(self, hovered: bool, animate: bool = True) -> None:
        if hovered == self._hovered:
            return
        self._hovered = hovered
        if animate:
            self._restyle()
        else:
            motion.stop(self._bg_anim)
            self._bg_anim = None
            self._set_bg(self._target_qcolor())

    def fading(self) -> bool:
        """Its background (or its Join button's) is still fading."""
        return motion.alive(self._bg_anim) or (
            self.join_button is not None and self.join_button.fading())

    def showEvent(self, e):
        # Defensive: a row is only ever shown inside the popup's list.
        if self.parentWidget() is None:
            log.warning("agenda row shown without a parent; not opening it as a window")
            self.hide()
            return
        super().showEvent(e)

    def set_selected(self, selected: bool) -> None:
        if selected != self._selected:
            self._selected = selected
            self._restyle()

    def enterEvent(self, e):
        if not self._hovered:           # (a row built "hovered" gets Qt's synthetic
            self._hovered = True        # enter on appearing: nothing to fade)
            self._restyle()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hovered = self._pressed = False
        self._restyle()
        super().leaveEvent(e)

    def focusInEvent(self, e):
        super().focusInEvent(e)
        self.update()

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self.update()

    def hideEvent(self, e):
        motion.stop(self._bg_anim)
        self._bg_anim = None
        self._hovered = self._pressed = False
        self._bg = self._target_qcolor()
        super().hideEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._pressed = True        # subtle press feedback
            self._restyle()
        e.accept()

    def mouseReleaseEvent(self, e):
        if self._pressed:
            self._pressed = False
            self._restyle()
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(
                e.position().toPoint()):
            self.toggle_requested.emit(self, False)
        e.accept()

    def contextMenuEvent(self, e):
        """Right-click (or the Menu key): Hide / Color menu. Never toggles selection."""
        pos = e.globalPos()
        if e.reason() == e.Reason.Keyboard:
            pos = self.mapToGlobal(self.rect().center())
        self.menu_requested.emit(self, pos)
        e.accept()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.toggle_requested.emit(self, True)
            e.accept()
            return
        super().keyPressEvent(e)
