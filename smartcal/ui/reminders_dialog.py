"""Upcoming reminders (the ones Claude set from the chat), with a Cancel button per row.

v0.12.0: a TrayDialog (no white frame on open, placed once, inside the work area); rows
are rebuilt only when the list changed, and the dialog keeps its tray-side edge and
stays inside the work area when rows come or go while it is open (it used to grow down
under the taskbar). Friendlier times ("Today 3:45 PM", "Tomorrow 9:00 AM",
"Mon, Nov 9 3:45 PM") and a hint on how reminders are made."""
from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QDialogButtonBox, QHBoxLayout, QLabel, QPushButton,
                             QVBoxLayout, QWidget)

from ..agenda import clock_text
from . import theme
from .dialogs import TrayDialog, add_press_scale

HINT = ("Reminders are set by Claude: open the popup, click Ask Claude… and say, for "
        "example, “remind me to call Sam in 20 minutes”. Each one pops up as a "
        "Windows notification.")
EMPTY = "No upcoming reminders."


def when_text(when: datetime, now: datetime) -> str:
    """'Today 3:45 PM', 'Tomorrow 9:00 AM', 'Mon, Nov 9 3:45 PM' (with the year when it
    is not this year). Both in local time."""
    days = (when.date() - now.date()).days
    if days == 0:
        day = "Today"
    elif days == 1:
        day = "Tomorrow"
    elif days == -1:
        day = "Yesterday"
    else:
        day = f"{when:%a}, {when:%b} {when.day}"
        if when.year != now.year:
            day += f", {when.year}"
    return f"{day} {clock_text(when)}"


class RemindersDialog(TrayDialog):
    OBJECT_NAME = "remindersDialog"

    def __init__(self, reminders, parent=None):
        super().__init__(parent)
        self.reminders = reminders
        self.now = lambda: datetime.now().astimezone()      # injectable for tests
        self.setWindowTitle("Reminders")
        self.setMinimumWidth(380)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(16, 14, 16, 12)
        self._layout.setSpacing(10)
        self.hint = QLabel(HINT)
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet(f"color:{theme.MUTED};")
        self._rows = QWidget()
        self._rows_layout = QVBoxLayout(self._rows)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(6)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.buttons.rejected.connect(self.reject)
        for b in self.buttons.buttons():
            add_press_scale(b)
        self._layout.addWidget(self._rows)
        self._layout.addWidget(self.hint)
        self._layout.addWidget(self.buttons)
        self._shown_items = None
        self.refresh()

    def build_lines(self) -> list:
        return [*self.rows(), self.hint, self.buttons]

    def refresh(self) -> None:
        """Re-list the reminders; nothing is rebuilt when the list did not change."""
        items = self.reminders.list()
        key = [(r.id, r.text, r.fire_at) for r in items]
        today = self.now().date()
        if key == self._shown_items and today == getattr(self, "_shown_day", None):
            return
        self._shown_items, self._shown_day = key, today
        self.finish_build()             # (rows on their way in are being replaced)
        while self._rows_layout.count():
            w = self._rows_layout.takeAt(0).widget()
            if w:
                w.hide()
                w.deleteLater()
        if not items:
            empty = QLabel(EMPTY, self._rows)
            empty.setTextFormat(Qt.TextFormat.PlainText)
            self._rows_layout.addWidget(empty)
        now = self.now()
        for r in items:
            row = QWidget(self._rows)
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(10)
            when = r.fire_at.astimezone()
            time_label = QLabel(when_text(when, now), row)
            time_label.setTextFormat(Qt.TextFormat.PlainText)
            time_label.setStyleSheet(f"color:{theme.MUTED};")
            label = QLabel(r.text, row)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            btn = QPushButton("Cancel", row)
            btn.setToolTip("Cancel this reminder")
            btn.setAutoDefault(False)
            btn.clicked.connect(lambda _c=False, rid=r.id: self._cancel(rid))
            add_press_scale(btn)
            h.addWidget(time_label)
            h.addWidget(label, 1)
            h.addWidget(btn)
            row.label, row.time_label, row.button = label, time_label, btn
            self._rows_layout.addWidget(row)
        self.settle_geometry()

    def _cancel(self, rid: int) -> None:
        self.reminders.cancel(rid)
        self.refresh()

    def rows(self) -> list:
        return [self._rows_layout.itemAt(i).widget() for i in range(self._rows_layout.count())]
