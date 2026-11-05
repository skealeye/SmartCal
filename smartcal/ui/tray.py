"""System tray icon: left-click toggles the agenda popup, right-click opens a menu."""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .. import agenda, google_auth

log = logging.getLogger(__name__)

STOP_WAIT_MS = 3000
ADD_TEXT = "Add Google account…"
CANCEL_TEXT = "Cancel Google sign-in"


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
    _add_done = pyqtSignal(int, str, str)   # generation, status, message
    signin_changed = pyqtSignal(bool)       # a Google sign-in started (True) / ended

    def __init__(self, popup, worker, cache, settings, parent=None,
                 on_accounts_changed=None, add_account_fn=None, on_open_settings=None, reminders=None,
                 on_before_exit=None, exit_fn=os._exit, cancel_signin_fn=None,
                 clock=time.monotonic):
        super().__init__(parent)
        self.popup, self.worker = popup, worker
        self._on_before_exit = on_before_exit
        self._exit = exit_fn
        self._quit_app = QApplication.quit
        self.cache, self.settings = cache, settings
        self._on_accounts_changed = on_accounts_changed
        self._add_account_fn = add_account_fn or google_auth.add_account
        self._cancel_signin_fn = cancel_signin_fn or google_auth.SIGN_IN.cancel
        self._clock = clock
        self._add_started = 0.0
        self._add_gen = 0
        self._on_open_settings = on_open_settings
        self.reminders = reminders
        self._reminders_dialog = None
        self._adding = False
        self._attention = False
        self._icon_day = 0
        self.setIcon(make_icon(datetime.now().day))
        menu = QMenu()
        self._add_action = QAction(ADD_TEXT, menu)
        self._add_action.triggered.connect(lambda _checked=False: self._on_add_triggered())
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
        menu.aboutToShow.connect(self._update_add_action)
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
            self._attention = False
            self.update_tooltip()
            self.popup.refresh()
            self.popup.show_near_tray(rect)

    def show_popup(self) -> None:
        """Show the popup (never hides it); used when a second launch was refused."""
        if self.popup.isVisible():
            self.popup.raise_()
            self.popup.activateWindow()
            return
        self._attention = False
        self.update_tooltip()
        self.popup.refresh()
        self.popup.show_near_tray(self._tray_rect())

    def update_tooltip(self) -> None:
        now = datetime.now().astimezone()
        if now.day != self._icon_day:
            self._icon_day = now.day
            self.setIcon(make_icon(now.day))
        nxt = agenda.next_event(self.cache, self.settings, now)
        tip = f"Next: {agenda.describe_next(nxt, now)}" if nxt else "SmartCal"
        if self._attention:
            tip = f"Claude is waiting for your OK\n{tip}"
        self.setToolTip(tip)

    def notify_attention(self, text: str) -> None:
        """Plain-text cue that the chat needs the owner (popup is hidden)."""
        self._attention = True
        self.update_tooltip()
        self.showMessage("SmartCal", text, QSystemTrayIcon.MessageIcon.Information, 10000)

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

    # -- Google sign-in ------------------------------------------------------
    def signin_pending(self) -> bool:
        """A sign-in is running and not yet older than the OAuth timeout (+5 s)."""
        return self._adding and \
            self._clock() - self._add_started < google_auth.SIGNIN_DEAD_AFTER

    def _update_add_action(self) -> None:
        self._add_action.setText(CANCEL_TEXT if self.signin_pending() else ADD_TEXT)
        self._add_action.setEnabled(True)

    def _on_add_triggered(self) -> None:
        if self.signin_pending():
            self.cancel_signin()
        else:
            self.add_account()

    def add_account(self) -> None:
        if self.signin_pending():
            return
        self._add_gen += 1
        gen = self._add_gen
        self._adding = True
        self._add_started = self._clock()
        self._update_add_action()
        self.signin_changed.emit(True)

        def work() -> None:
            status, message = "ok", ""
            try:
                self._add_account_fn("new")
            except google_auth.SignInCancelled:
                status = "cancelled"
            except BaseException as exc:  # noqa: BLE001 - reported to the user
                log.exception("add account failed")
                status, message = "error", str(exc) or type(exc).__name__
            finally:
                self._add_done.emit(gen, status, message)

        threading.Thread(target=work, daemon=True, name="add-account").start()

    def cancel_signin(self) -> None:
        if not self.signin_pending():
            return
        try:
            self._cancel_signin_fn()
        except Exception:  # noqa: BLE001
            log.exception("cancel sign-in failed")

    def _finish_add(self, gen: int, status: str, message: str) -> None:
        current = gen == self._add_gen   # an older (dead) sign-in finishing late
        try:
            if status == "ok":
                if self._on_accounts_changed:
                    self._on_accounts_changed()  # drop cached clients: new credentials
                self.worker.sync_now()
            elif status == "cancelled":
                if current:
                    self.showMessage("SmartCal", "Sign-in cancelled",
                                     QSystemTrayIcon.MessageIcon.Information)
            elif current:
                self.showMessage("SmartCal", f"Could not add account: {message}",
                                 QSystemTrayIcon.MessageIcon.Warning)
        except Exception:  # noqa: BLE001
            log.exception("after adding account")
        finally:
            if current:
                self._adding = False
                self._update_add_action()
                self.signin_changed.emit(False)

    def quit(self) -> None:
        """Stop the sync thread and quit. A thread stuck in a request (HTTP timeout is
        20 s) is never terminate()d mid-write: state is flushed and the process exits.
        Every state file is written atomically, so nothing is left half-written."""
        self.worker.stop()
        if not self.worker.wait(STOP_WAIT_MS):
            log.warning("sync thread did not stop; exiting without waiting for it")
            try:
                if self._on_before_exit:
                    self._on_before_exit()
                for handler in logging.getLogger().handlers:
                    handler.flush()
            except Exception:  # noqa: BLE001
                log.exception("flush before exit failed")
            self._exit(0)
            return
        self.hide()
        self._quit_app()
