"""The tray agenda popup ("A. Agenda list" layout). Reads only from the cache."""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime

from PyQt6.QtCore import QEvent, QPoint, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
                             QVBoxLayout, QWidget)

from .. import __version__, agenda
from . import placement, theme

WIDTH = 250
MARGIN = 8
MAX_HEIGHT = 480


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

    def __init__(self, cache, settings, parent=None):
        super().__init__(parent)
        self.cache, self.settings = cache, settings
        # Injectable for tests.
        self.clock = time.monotonic
        self.cursor_pos = lambda: QCursor.pos()
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
        for w in (self._header, self._status, self._next, self._scroll, self._empty, self._footer):
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
            self.hide()
            return
        super().keyPressEvent(e)

    def toggle_chat(self) -> None:
        if self._chat_factory is None:
            return
        if self._chat is None:
            from .chat import ChatWidget
            self._chat = ChatWidget(self._chat_factory)
            self._chat.changed.connect(self.chat_changed)
            self._chat.attention.connect(self.chat_attention)
            self._chat.collapse_requested.connect(self._collapse_chat)
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
        now = now or datetime.now().astimezone()
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
        for ev in events:
            is_next = nxt is not None and ev.id == nxt.id and ev.account == nxt.account
            time_text = "All day" if ev.all_day else f"{ev.start:%I:%M %p}".lstrip("0")
            self._rows.append(RowInfo(ev.title, is_next, time_text))
            self._rows_box.addWidget(self._make_row(ev, time_text, is_next))
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

    def _make_row(self, ev, time_text: str, is_next: bool) -> QWidget:
        row = QFrame()
        row.setObjectName("row")
        bg = theme.BG_NEXT if is_next else "transparent"
        row.setStyleSheet(f"QFrame#row{{background:{bg};border-radius:4px;}}")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 4, 0)
        lay.setSpacing(6)
        stripe = QFrame()
        stripe.setObjectName("stripe")
        stripe.setFixedWidth(4)
        stripe.setStyleSheet(f"background:{theme.stripe_color(ev.color)};border-radius:2px;")
        lay.addWidget(stripe)
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
        lay.addLayout(col, 1)
        return row

    # -- placement / focus ---------------------------------------------------
    def compute_position(self, tray: QRect | None, area: QRect) -> QPoint:
        return placement.compute_position(QSize(self.width(), self.height()), tray, area)

    def _place(self) -> None:
        self.move(self.compute_position(self._tray_geom, self._area()))

    def show_near_tray(self, tray_geometry) -> None:
        self._tray_geom = QRect(tray_geometry)
        self._anchored = True
        self._resize()  # sizes against the right screen; places only when visible
        self._place()
        self.show()
        self.raise_()
        self.activateWindow()

    def event(self, e):
        if e.type() == QEvent.Type.WindowDeactivate and self.isVisible():
            self.hide()
        return super().event(e)

    def hideEvent(self, e):
        self.last_hidden_at = self.clock()
        self.last_hidden_cursor = self.cursor_pos()
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
