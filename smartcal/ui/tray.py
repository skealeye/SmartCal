"""System tray icon: left-click toggles the agenda popup, right-click opens a menu."""
from __future__ import annotations

import threading
from datetime import datetime

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .. import agenda, google_auth


def make_icon(day: int) -> QIcon:
    """A small calendar-style icon showing the day number (drawn, no image files)."""
    size = 64
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#f1f3f4"))
    p.drawRoundedRect(4, 6, 56, 54, 8, 8)
    p.setBrush(QColor("#ea4335"))
    p.drawRoundedRect(4, 6, 56, 18, 8, 8)
    p.drawRect(4, 16, 56, 8)
    p.setPen(QColor("#202124"))
    font = QFont("Segoe UI")
    font.setBold(True)
    font.setPixelSize(30)
    p.setFont(font)
    p.drawText(4, 26, 56, 34, Qt.AlignmentFlag.AlignCenter, str(day))
    p.end()
    return QIcon(pm)


class TrayController(QSystemTrayIcon):
    _add_failed = pyqtSignal(str)

    def __init__(self, popup, worker, cache, settings, parent=None):
        super().__init__(parent)
        self.popup, self.worker = popup, worker
        self.cache, self.settings = cache, settings
        self._icon_day = 0
        self.setIcon(make_icon(datetime.now().day))
        menu = QMenu()
        self._add_action = QAction("Add Google account…", menu)
        self._add_action.triggered.connect(self.add_account)
        self._sync_action = QAction("Sync now", menu)
        self._sync_action.triggered.connect(self.worker.sync_now)
        quit_action = QAction("Quit", menu)
        quit_action.triggered.connect(self.quit)
        menu.addAction(self._add_action)
        menu.addAction(self._sync_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        self._menu = menu  # keep a reference
        self.setContextMenu(menu)
        self.activated.connect(self._on_activated)
        self._add_failed.connect(self._show_add_error)
        self.update_tooltip()

    def _on_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle_popup()

    def toggle_popup(self) -> None:
        if self.popup.isVisible():
            self.popup.hide()
        elif self.popup.recently_hidden():
            # The click that deactivated the popup also reached the tray icon; stay hidden.
            return
        else:
            self.popup.refresh()
            self.popup.show_near_tray(self.geometry())

    def update_tooltip(self) -> None:
        now = datetime.now().astimezone()
        if now.day != self._icon_day:
            self._icon_day = now.day
            self.setIcon(make_icon(now.day))
        nxt = agenda.next_event(self.cache, self.settings, now)
        self.setToolTip(f"Next: {agenda.describe_next(nxt, now)}" if nxt else "SmartCal")

    def add_account(self) -> None:
        self._add_action.setEnabled(False)

        def work() -> None:
            try:
                google_auth.add_account("new")
            except Exception as exc:  # noqa: BLE001 - reported to the user
                self._add_failed.emit(str(exc))
            else:
                self.worker.sync_now()
                self._add_failed.emit("")

        threading.Thread(target=work, daemon=True, name="add-account").start()

    def _show_add_error(self, message: str) -> None:
        self._add_action.setEnabled(True)
        if message:
            self.showMessage("SmartCal", f"Could not add account: {message}",
                             QSystemTrayIcon.MessageIcon.Warning)

    def quit(self) -> None:
        self.worker.stop()
        if not self.worker.wait(3000):
            self.worker.terminate()
            self.worker.wait(1000)
        self.hide()
        QApplication.quit()
