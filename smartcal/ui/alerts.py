"""SmartCal's own reminder cards (v0.15.0).

A small dark card at the bottom-right of the screen for each meeting or reminder alert,
drawn by SmartCal itself: Windows hides its own toast banners under Do Not Disturb /
Focus, these never hide. Cards stack upward, stay until dismissed (or AUTO_CLOSE_MS),
and never take keyboard focus.
"""
from __future__ import annotations

import logging

from PyQt6.QtCore import QObject, QRectF, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices, QPainter, QPen
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..textutil import safe_http_url
from . import placement, theme

log = logging.getLogger(__name__)

CARD_WIDTH = 340
GAP = 8
MAX_CARDS = 4
AUTO_CLOSE_MS = 120_000
TITLE_LIMIT, BODY_LIMIT = 120, 300


class AlertCard(QWidget):
    closed = pyqtSignal(object)

    def __init__(self, title: str, body: str, url: str | None = None):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(CARD_WIDTH)
        self.url = safe_http_url(url) if url else None
        self._done = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 12, 12)
        lay.setSpacing(4)
        self.title_label = QLabel(str(title)[:TITLE_LIMIT])
        self.title_label.setTextFormat(Qt.TextFormat.PlainText)   # event titles are untrusted
        self.title_label.setWordWrap(True)
        self.title_label.setStyleSheet(f"color:{theme.TEXT}; font-size:14px; font-weight:600;")
        lay.addWidget(self.title_label)
        self.body_label = QLabel(str(body)[:BODY_LIMIT])
        self.body_label.setTextFormat(Qt.TextFormat.PlainText)
        self.body_label.setWordWrap(True)
        self.body_label.setStyleSheet(f"color:{theme.MUTED}; font-size:12px;")
        lay.addWidget(self.body_label)
        row = QHBoxLayout()
        row.setContentsMargins(0, 6, 0, 0)
        row.addStretch(1)
        self.join_button = None
        if self.url:
            self.join_button = self._button("Join", accent=True)
            self.join_button.clicked.connect(self._join)
            row.addWidget(self.join_button)
        self.dismiss_button = self._button("Dismiss")
        self.dismiss_button.clicked.connect(self.dismiss)
        row.addWidget(self.dismiss_button)
        lay.addLayout(row)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)
        self._timer.start(AUTO_CLOSE_MS)

    @staticmethod
    def _button(text: str, accent: bool = False) -> QPushButton:
        b = QPushButton(text)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        bg, hover = (theme.ACCENT, theme.LINK) if accent else (theme.BUTTON, theme.BUTTON_HOVER)
        fg = theme.ON_DARK if accent else theme.TEXT
        b.setStyleSheet(
            f"QPushButton {{ background:{bg}; color:{fg}; border:none; padding:5px 14px;"
            f" border-radius:{theme.RADIUS_SMALL}px; font-size:12px; }}"
            f" QPushButton:hover {{ background:{hover}; }}"
            f" QPushButton:pressed {{ background:{theme.BUTTON_PRESSED}; }}")
        return b

    def _join(self) -> None:
        if self.url:
            QDesktopServices.openUrl(QUrl(self.url))
        self.dismiss()

    def dismiss(self) -> None:
        if self._done:
            return
        self._done = True
        self._timer.stop()
        self.hide()
        self.closed.emit(self)
        self.deleteLater()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setBrush(QColor(theme.BG))
        p.setPen(QPen(QColor(theme.ACCENT), 1))
        p.drawRoundedRect(rect, theme.CORNER_RADIUS, theme.CORNER_RADIUS)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.ACCENT))
        p.drawRoundedRect(QRectF(0, 10, 4, self.height() - 20), 2, 2)   # left stripe


class AlertManager(QObject):
    """Owns the visible cards. `show()` may be called from any thread."""

    _request = pyqtSignal(str, str, object)

    def __init__(self, tray_geometry_fn=None, parent=None):
        super().__init__(parent)
        self._tray_geometry_fn = tray_geometry_fn
        self.cards: list[AlertCard] = []
        self._request.connect(self._show_on_gui_thread)

    def show(self, title: str, body: str, url: str | None = None) -> None:
        self._request.emit(str(title), str(body), url)   # queued onto the GUI thread

    def _show_on_gui_thread(self, title: str, body: str, url) -> None:
        card = AlertCard(title, body, url)
        card.closed.connect(self._card_closed)
        self.cards.append(card)
        while len(self.cards) > MAX_CARDS:
            self.cards[0].dismiss()
        self._layout()
        card.show()

    def _card_closed(self, card) -> None:
        if card in self.cards:
            self.cards.remove(card)
        self._layout()

    def _layout(self) -> None:
        tray = self._tray_geometry_fn() if self._tray_geometry_fn else None
        area = placement.area_for(tray)
        y = area.bottom() + 1 - 12
        for card in reversed(self.cards):          # newest at the bottom
            card.adjustSize()
            y -= card.height()
            card.move(area.right() + 1 - card.width() - 12, y)
            y -= GAP
