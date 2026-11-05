"""Read-only list of upcoming reminders with a Cancel button per row."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from ..agenda import clock_text
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


class RemindersDialog(QDialog):
    def __init__(self, reminders, parent=None):
        super().__init__(parent)
        self.reminders = reminders
        self.setWindowTitle("Reminders")
        self.setMinimumWidth(360)
        self._layout = QVBoxLayout(self)
        self._rows = QWidget()
        self._rows_layout = QVBoxLayout(self._rows)
        self._layout.addWidget(self._rows)
        self.refresh()

    def showEvent(self, event) -> None:
        self.refresh()
        super().showEvent(event)

    def refresh(self) -> None:
        while self._rows_layout.count():
            w = self._rows_layout.takeAt(0).widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        items = self.reminders.list()
        if not items:
            empty = QLabel("No upcoming reminders.")
            empty.setTextFormat(Qt.TextFormat.PlainText)
            self._rows_layout.addWidget(empty)
        for r in items:
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            when = r.fire_at.astimezone()
            label = QLabel(f"{when:%Y-%m-%d} {clock_text(when)}  {r.text}")
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            btn = QPushButton("Cancel")
            btn.clicked.connect(lambda _c=False, rid=r.id: self._cancel(rid))
            h.addWidget(label, 1)
            h.addWidget(btn)
            row.label, row.button = label, btn
            self._rows_layout.addWidget(row)

    def _cancel(self, rid: int) -> None:
        self.reminders.cancel(rid)
        self.refresh()

    def rows(self) -> list:
        return [self._rows_layout.itemAt(i).widget() for i in range(self._rows_layout.count())]
