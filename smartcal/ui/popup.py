"""The tray agenda popup ("A. Agenda list" layout). Reads only from the cache."""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime

from PyQt6.QtCore import QEvent, QPoint, Qt, pyqtSignal
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .. import __version__, agenda
from . import theme


@dataclass
class RowInfo:
    title: str
    is_next: bool
    time_text: str


class AgendaPopup(QWidget):
    """Frameless tool window. Hides when it loses activation (WindowDeactivate)."""

    view_requested = pyqtSignal(str)

    def __init__(self, cache, settings, parent=None):
        super().__init__(parent)
        self.cache, self.settings = cache, settings
        self.last_hidden_at = 0.0
        self._statuses: dict[str, str] = {}
        self._rows: list[RowInfo] = []
        self._next_line = ""
        self._no_accounts = False
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint)
        self.setFixedWidth(250)
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
        self._status.setStyleSheet(f"color:{theme.WARN};font-size:11px;")
        self._status.setWordWrap(True)
        self._status.hide()
        self._next = QLabel()
        self._next.setStyleSheet(f"color:{theme.LINK};")
        self._next.setWordWrap(True)
        self._rows_box = QVBoxLayout()
        self._rows_box.setSpacing(4)
        self._empty = QLabel()
        self._empty.setStyleSheet(f"color:{theme.MUTED};")
        self._empty.setWordWrap(True)
        footer = QHBoxLayout()
        for name in ("Day", "Week", "Month"):
            link = QLabel(f'<a href="{name.lower()}" style="color:{theme.LINK};'
                          f'text-decoration:none;">{name}</a>')
            link.linkActivated.connect(self.request_view)
            footer.addWidget(link)
        footer.addStretch()
        ver = QLabel(f"v{__version__}")
        ver.setStyleSheet(f"color:{theme.MUTED};font-size:10px;")
        footer.addWidget(ver)
        for w in (self._header, self._status, self._next):
            outer.addWidget(w)
        outer.addLayout(self._rows_box)
        outer.addWidget(self._empty)
        outer.addLayout(footer)

    # -- signals / state ------------------------------------------------
    def request_view(self, name: str) -> None:
        self.view_requested.emit(name)

    def set_account_status(self, account: str, status: str) -> None:
        self._statuses[account] = status
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

    # -- accessors (used by tests and tooltip) ---------------------------
    def rows(self) -> list[RowInfo]:
        return list(self._rows)

    def header_text(self) -> str:
        return self._header.text()

    def next_text(self) -> str:
        return self._next_line

    def empty_text(self) -> str:
        return self._empty.text()

    def status_text(self) -> str:
        return self._status.text()

    # -- rendering ---------------------------------------------------------
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
        self.adjustSize()

    def _make_row(self, ev, time_text: str, is_next: bool) -> QWidget:
        row = QFrame()
        row.setObjectName("row")
        bg = theme.BG_NEXT if is_next else "transparent"
        row.setStyleSheet(f"QFrame#row{{background:{bg};border-radius:4px;}}")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 4, 0)
        lay.setSpacing(6)
        stripe = QFrame()
        stripe.setFixedWidth(4)
        stripe.setStyleSheet(f"background:{theme.stripe_color(ev.color)};border-radius:2px;")
        lay.addWidget(stripe)
        col = QVBoxLayout()
        col.setContentsMargins(0, 3, 0, 3)
        col.setSpacing(0)
        top = QLabel(f"<b>{time_text}</b>  {_esc(ev.title)}")
        top.setTextFormat(Qt.TextFormat.RichText)
        top.setWordWrap(True)
        sub = QLabel(ev.calendar_name)
        sub.setStyleSheet(f"color:{theme.MUTED};font-size:10px;")
        col.addWidget(top)
        col.addWidget(sub)
        lay.addLayout(col, 1)
        return row

    # -- placement / focus ---------------------------------------------------
    def show_near_tray(self, tray_geometry) -> None:
        from PyQt6.QtGui import QGuiApplication
        self.adjustSize()
        screen = QGuiApplication.screenAt(tray_geometry.center()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        x = tray_geometry.center().x() - self.width() // 2
        y = tray_geometry.top() - self.height() - 6
        if tray_geometry.top() < area.center().y():  # taskbar at top
            y = tray_geometry.bottom() + 6
        x = max(area.left() + 4, min(x, area.right() - self.width() - 4))
        y = max(area.top() + 4, min(y, area.bottom() - self.height() - 4))
        self.move(QPoint(x, y))
        self.show()
        self.raise_()
        self.activateWindow()

    def event(self, e):
        if e.type() == QEvent.Type.WindowDeactivate and self.isVisible():
            self.hide()
        return super().event(e)

    def hideEvent(self, e):
        self.last_hidden_at = time.monotonic()
        super().hideEvent(e)

    def recently_hidden(self, within: float = 0.3) -> bool:
        return time.monotonic() - self.last_hidden_at < within


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
