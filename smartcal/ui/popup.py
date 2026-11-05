"""The tray agenda popup ("A. Agenda list" layout). Reads only from the cache."""
from __future__ import annotations

import html
import time
from dataclasses import dataclass
from datetime import datetime

from PyQt6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QCursor, QDesktopServices
from PyQt6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QPushButton,
                             QScrollArea, QSizePolicy, QStyle, QVBoxLayout, QWidget)

from .. import __version__, agenda
from ..textutil import html_to_text, meeting_label, safe_http_url
from . import layout, meeting_menus, placement, theme
from .linktext import LinkText, plain_tip

WIDTH = 250
MARGIN = 8
MAX_HEIGHT = 480
DETAILS_WIDTH = WIDTH - 20 - 4 - 6 - 4 - 2   # popup margins, stripe, spacing, row margins
DESCRIPTION_LINES = 8                         # longer descriptions scroll


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
        self._menu = None
        self._expanded_key: tuple | None = None
        self._watched_modals: list = []
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
            row = self.expanded_row()
            if row is not None:          # Esc first collapses an expanded meeting
                self.toggle_row(row)
                row.setFocus()
                return
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
        expanded = None
        for ev in events:
            is_next = nxt is not None and ev.id == nxt.id and ev.account == nxt.account
            time_text = "All day" if ev.all_day else f"{ev.start:%I:%M %p}".lstrip("0")
            self._rows.append(RowInfo(ev.title, is_next, time_text))
            row = self._make_row(ev, time_text, is_next)
            self._rows_box.addWidget(row)
            if self._expanded_key is not None and _row_key(ev) == self._expanded_key:
                expanded = row
        if expanded is not None:   # a refresh keeps the open meeting open
            expanded.set_expanded(True, self._build_details)
        else:
            self._expanded_key = None
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
        if url and url.lower().startswith(("http://", "https://")):
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
        row.toggle_requested.connect(self.toggle_row)
        return row

    def _join(self, url: str) -> None:
        if safe_http_url(url) is not None:
            QDesktopServices.openUrl(QUrl(url))
            self.hide()

    # -- expanded row ---------------------------------------------------------
    def expanded_row(self) -> "AgendaRow | None":
        return next((r for r in self.row_widgets()
                     if isinstance(r, AgendaRow) and r.is_expanded()), None)

    def toggle_row(self, row: "AgendaRow") -> None:
        """Accordion: expanding one row collapses the one that was open."""
        if row.is_expanded():
            row.set_expanded(False)
            self._expanded_key = None
        else:
            for other in self.row_widgets():
                if isinstance(other, AgendaRow) and other is not row and other.is_expanded():
                    other.set_expanded(False)
            row.set_expanded(True, self._build_details)
            self._expanded_key = _row_key(row.event)
        for r in self.row_widgets():
            if isinstance(r, AgendaRow):
                r.layout().invalidate()  # size hints now, not on the next event loop pass
                r.layout().activate()
        self._rows_box.invalidate()
        self._resize()
        if row.is_expanded():
            self._scroll.ensureWidgetVisible(row, 0, 0)

    def _build_details(self, ev) -> QWidget:
        """The expanded part of a row; built on first expand only (keeps refresh fast)."""
        box = QWidget()
        box.setObjectName("details")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 2, 0, 6)
        lay.setSpacing(4)
        when = QLabel(layout.format_range(ev, self.now()))
        when.setObjectName("when")
        when.setTextFormat(Qt.TextFormat.PlainText)
        when.setWordWrap(True)
        lay.addWidget(when)
        cal = QLabel(f'<span style="color:{theme.effective_color(ev, self.settings)};">'
                     f'&#9632;</span> {html.escape(ev.calendar_name or "")}')
        cal.setObjectName("calendar")
        cal.setTextFormat(Qt.TextFormat.RichText)
        cal.setWordWrap(True)
        lay.addWidget(cal)
        if ev.location:
            loc = QLabel(ev.location)
            loc.setObjectName("location")
            loc.setTextFormat(Qt.TextFormat.PlainText)
            loc.setWordWrap(True)
            loc.setStyleSheet(f"color:{theme.MUTED};")
            lay.addWidget(loc)
        description = html_to_text(ev.description or "")
        if description:
            desc = LinkText(description)
            desc.setObjectName("description")
            desc.setStyleSheet(f"QTextBrowser{{background:{theme.BG};color:{theme.TEXT};"
                               f"border:1px solid {theme.BORDER};font-size:11px;}}")
            desc.setFixedHeight(desc.fit_height(DETAILS_WIDTH - _scrollbar_w(), DESCRIPTION_LINES))
            desc.link_opened.connect(lambda _u: self.hide())
            lay.addWidget(desc)
        url = getattr(ev, "meeting_url", None)
        if safe_http_url(url) is not None:
            join = QPushButton(meeting_label(url))
            join.setObjectName("bigjoin")
            join.setCursor(Qt.CursorShape.PointingHandCursor)
            join.setToolTip(plain_tip(url))
            join.setStyleSheet(
                f"QPushButton{{background:{theme.LINK};color:#0b0d10;font-weight:bold;"
                "padding:5px 10px;border:none;border-radius:4px;}"
                "QPushButton:hover{background:#a9c8ff;}")
            join.clicked.connect(lambda _c=False, u=url: self._join(u))
            # Never wider than the popup, whatever the label's font metrics.
            join.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            lay.addWidget(join)
        actions = QHBoxLayout()
        actions.setSpacing(6)
        style = (f"QPushButton{{background:{theme.BORDER};color:{theme.TEXT};font-size:11px;"
                 "padding:3px 8px;border:none;border-radius:3px;}"
                 "QPushButton:hover{background:#4a4d51;}")
        hide_btn = QPushButton("Hide…")
        hide_btn.setObjectName("hide")
        hide_btn.setToolTip("Hide in SmartCal only (Google is not changed)")
        hide_btn.setStyleSheet(style)
        hide_btn.clicked.connect(lambda _c=False, e=ev, b=hide_btn: self.show_hide_menu(e, b))
        color_btn = QPushButton(meeting_menus.color_button_text(ev))
        color_btn.setObjectName("color")
        color_btn.setToolTip("Color in SmartCal only (Google is not changed)")
        color_btn.setStyleSheet(style)
        color_btn.clicked.connect(lambda _c=False, e=ev, b=color_btn: self.show_color_menu(e, b))
        actions.addWidget(hide_btn)
        actions.addWidget(color_btn)
        actions.addStretch(1)
        lay.addLayout(actions)
        return box

    # -- Hide… / Color… menus ---------------------------------------------------
    def menu(self):
        return self._menu

    def _show_menu(self, menu, button) -> None:
        if self._menu is not None and self._menu is not menu:
            self._menu.deleteLater()
        self._menu = menu
        menu.aboutToHide.connect(self._after_menu)
        menu.popup(button.mapToGlobal(button.rect().bottomLeft()))

    def _after_menu(self) -> None:
        if self.isVisible():
            self.raise_()
            self.activateWindow()

    def show_hide_menu(self, ev, button) -> None:
        # The menu belongs to the popup (not the row): a refresh never deletes it.
        self._show_menu(meeting_menus.hide_menu(
            self, ev, lambda scope, e=ev: self.hide_meeting(e, scope)), button)

    def show_color_menu(self, ev, button) -> None:
        self._show_menu(meeting_menus.color_menu(
            self, ev, self.settings.event_color(ev),
            lambda color, e=ev: self.color_meeting(e, color)), button)

    def hide_meeting(self, ev, scope: str) -> None:
        self.settings.hide_event(ev, scope)
        self._expanded_key = None
        self.refresh()
        self.show_notice(meeting_menus.HIDDEN_NOTICE)
        self.meetings_changed.emit()

    def color_meeting(self, ev, color) -> None:
        if color is None:
            self.settings.clear_event_color(ev)
        else:
            self.settings.set_event_color(ev, color)
        self.refresh()          # keeps this row expanded
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
            modal = self.active_modal()
            menu = self.active_popup()
            if menu is None and self._menu is not None and self._menu.isVisible():
                menu = self._menu
            if modal is not None and modal is not self:
                self._reactivate_after(modal)   # e.g. the "Undo anyway" confirm box
            elif menu is not None and menu is not self:
                self._reactivate_after(menu)    # the Hide…/Color… menu
            else:
                self.hide()
        return super().event(e)

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
            if self.isVisible():
                self.raise_()
                self.activateWindow()
        finished.connect(done)

    def hideEvent(self, e):
        self.last_hidden_at = self.clock()
        self.last_hidden_cursor = self.cursor_pos()
        self._expanded_key = None        # opens collapsed next time
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


def _scrollbar_w() -> int:
    style = QApplication.style()
    return style.pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent) if style else 12


class AgendaRow(QFrame):
    """One agenda row. Click (outside its buttons and details), Enter or Space toggles
    the inline details; the compact Join button never toggles."""

    toggle_requested = pyqtSignal(object)

    def __init__(self, ev, time_text: str, is_next: bool, color: str, parent=None):
        super().__init__(parent)
        self.event = ev
        self.details: QWidget | None = None
        self.setObjectName("row")
        bg = theme.BG_NEXT if is_next else "transparent"
        self.setStyleSheet(f"QFrame#row{{background:{bg};border-radius:4px;"
                           f"border:1px solid transparent;}}"
                           f"QFrame#row:focus{{border:1px solid {theme.LINK};}}")
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

    def is_expanded(self) -> bool:
        return self.details is not None and not self.details.isHidden()

    def set_expanded(self, expanded: bool, builder=None) -> None:
        if expanded and self.details is None:
            if builder is None:
                return
            self.details = builder(self.event)
            self._body.addWidget(self.details)
        if self.details is not None:
            self.details.setVisible(expanded)

    def mousePressEvent(self, e):
        e.accept()

    def mouseReleaseEvent(self, e):
        pos = e.position().toPoint()
        in_details = self.is_expanded() and self.details.geometry().contains(pos)
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(pos) \
                and not in_details:
            self.toggle_requested.emit(self)
        e.accept()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.toggle_requested.emit(self)
            e.accept()
            return
        super().keyPressEvent(e)
