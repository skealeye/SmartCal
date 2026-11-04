"""System tray icon: left-click toggles the agenda popup, right-click opens a menu."""
from __future__ import annotations

import logging
import threading
from datetime import datetime

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .. import agenda, google_auth

log = logging.getLogger(__name__)


def dialog_visible(dlg) -> bool:
    """True if `dlg` is a live, visible widget (False for None or a deleted wrapper)."""
    if dlg is None:
        return False
    try:
        return bool(dlg.isVisible())
    except RuntimeError:  # underlying C++ object already deleted
        return False


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
    _add_done = pyqtSignal(str)

    def __init__(self, popup, worker, cache, settings, parent=None,
                 on_accounts_changed=None, add_account_fn=None, on_open_settings=None, reminders=None):
        super().__init__(parent)
        self.popup, self.worker = popup, worker
        self.cache, self.settings = cache, settings
        self._on_accounts_changed = on_accounts_changed
        self._add_account_fn = add_account_fn or google_auth.add_account
        self._on_open_settings = on_open_settings
        self.reminders = reminders
        self._reminders_dialog = None
        self._adding = False
        self._icon_day = 0
        self.setIcon(make_icon(datetime.now().day))
        menu = QMenu()
        self._add_action = QAction("Add Google account…", menu)
        self._add_action.triggered.connect(lambda _checked=False: self.add_account())
        self._sync_action = QAction("Sync now", menu)
        self._sync_action.triggered.connect(lambda _checked=False: self.worker.sync_now())
        self._settings_action = QAction("Settings…", menu)
        self._settings_action.triggered.connect(lambda _checked=False: self.open_settings())
        self._reminders_action = QAction("Reminders…", menu)
        self._reminders_action.triggered.connect(lambda _checked=False: self.open_reminders())
        quit_action = QAction("Quit", menu)
        quit_action.triggered.connect(lambda _checked=False: self.quit())
        menu.addAction(self._add_action)
        menu.addAction(self._sync_action)
        menu.addAction(self._reminders_action)
        menu.addAction(self._settings_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        self._menu = menu  # keep a reference
        self.setContextMenu(menu)
        self.activated.connect(self._on_activated)
        self._add_done.connect(self._finish_add)
        self.update_tooltip()

    def _tray_rect(self):
        return self.geometry()

    def _on_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle_popup()

    def toggle_popup(self) -> None:
        rect = self._tray_rect()
        if self.popup.isVisible():
            self.popup.hide()
        elif self.popup.recently_dismissed_by_tray_click(rect):
            # The click that deactivated (hid) the popup also reached the tray icon.
            return
        else:
            self.popup.refresh()
            self.popup.show_near_tray(rect)

    def update_tooltip(self) -> None:
        now = datetime.now().astimezone()
        if now.day != self._icon_day:
            self._icon_day = now.day
            self.setIcon(make_icon(now.day))
        nxt = agenda.next_event(self.cache, self.settings, now)
        self.setToolTip(f"Next: {agenda.describe_next(nxt, now)}" if nxt else "SmartCal")

    def open_settings(self) -> None:
        if self._on_open_settings:
            self._on_open_settings()

    def open_reminders(self) -> None:
        if self.reminders is None:
            return
        from .reminders_dialog import RemindersDialog
        dlg = self._reminders_dialog
        if dialog_visible(dlg):
            dlg.refresh()
            dlg.raise_()
            dlg.activateWindow()
            return
        dlg = RemindersDialog(self.reminders)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._reminders_dialog = dlg
        dlg.destroyed.connect(lambda *_: setattr(self, "_reminders_dialog", None))
        dlg.show()

    def refresh_reminders(self) -> None:
        if dialog_visible(self._reminders_dialog):
            self._reminders_dialog.refresh()

    def add_account(self) -> None:
        if self._adding:
            return
        self._adding = True
        self._add_action.setEnabled(False)

        def work() -> None:
            message = ""
            try:
                self._add_account_fn("new")
            except BaseException as exc:  # noqa: BLE001 - reported to the user
                log.exception("add account failed")
                message = str(exc) or type(exc).__name__
            finally:
                self._add_done.emit(message)

        threading.Thread(target=work, daemon=True, name="add-account").start()

    def _finish_add(self, message: str) -> None:
        try:
            if not message:
                if self._on_accounts_changed:
                    self._on_accounts_changed()  # drop cached clients: new credentials
                self.worker.sync_now()
            else:
                self.showMessage("SmartCal", f"Could not add account: {message}",
                                 QSystemTrayIcon.MessageIcon.Warning)
        except Exception:  # noqa: BLE001
            log.exception("after adding account")
        finally:
            self._adding = False
            self._add_action.setEnabled(True)

    def quit(self) -> None:
        self.worker.stop()
        if not self.worker.wait(3000):
            self.worker.terminate()
            self.worker.wait(1000)
        self.hide()
        QApplication.quit()
