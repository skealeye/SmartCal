"""System tray icon: left-click toggles the agenda popup, right-click opens a menu."""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from datetime import date, datetime

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .. import agenda, google_auth
from . import meeting_menus, placement

log = logging.getLogger(__name__)

STOP_WAIT_MS = 3000
ADD_TEXT = "Add Google account…"
CANCEL_TEXT = "Cancel Google sign-in"
ACCOUNTS_TEXT = "Accounts"
NO_ACCOUNTS_TEXT = "No accounts"
RESTORE_TEXT = "Restore hidden"
NOTHING_HIDDEN_TEXT = "Nothing hidden"
RESTORE_ALL_TEXT = "Restore all hidden"
RESTORE_TITLE_MAX = 40


def menu_text(text: str) -> str:
    """Plain text for a QAction label: whitespace (tabs, newlines) collapsed, and `&`
    doubled so Qt never reads it as a mnemonic."""
    return re.sub(r"\s+", " ", text).strip().replace("&", "&&")


def restore_label(info: dict) -> str:
    """'Title (series)' or 'Title (Nov 4)', the title shortened to RESTORE_TITLE_MAX."""
    title = re.sub(r"\s+", " ", str(info.get("title") or "")).strip() or "(no title)"
    if len(title) > RESTORE_TITLE_MAX:
        title = title[:RESTORE_TITLE_MAX - 1].rstrip() + "…"
    if info.get("scope") == "series":
        suffix = "series"
    else:
        try:
            d = date.fromisoformat(str(info.get("when")))
            suffix = f"{d:%b} {d.day}"
        except ValueError:
            suffix = "this meeting"
    return menu_text(f"{title} ({suffix})")


def dialog_visible(dlg) -> bool:
    """True if `dlg` is a live, visible widget (False for None or a deleted wrapper)."""
    if dlg is None:
        return False
    try:
        return bool(dlg.isVisible())
    except RuntimeError:  # underlying C++ object already deleted
        return False


def track_dialog(holder: list, dlg) -> None:
    """Remember `dlg` in `holder` (a 1-item list) until it is destroyed. A late
    `destroyed` from an older dialog never clears a newer one."""
    holder[:] = [dlg]

    def forget(*_args, d=dlg) -> None:
        if holder and holder[0] is d:
            holder.clear()
    dlg.destroyed.connect(forget)


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
                 clock=time.monotonic, instance=None, accounts_fn=None,
                 on_visibility_changed=None):
        super().__init__(parent)
        self.popup, self.worker = popup, worker
        self._instance = instance   # SingleInstance: released first thing on Quit
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
        self._accounts_fn = accounts_fn or google_auth.list_accounts
        # Called after the tray menu changed what is shown (an account shown/hidden, a
        # meeting restored): run.py refreshes the window, toasts and Settings.
        self._on_visibility_changed = on_visibility_changed
        self.reminders = reminders
        self._reminders_ref: list = []
        self._adding = False
        self._attention = False
        self._icon_day = 0
        self.now = lambda: datetime.now().astimezone()   # injectable for tests
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
        # Built when the tray menu is about to show, so they are always current.
        self.accounts_menu = QMenu(ACCOUNTS_TEXT, menu)
        self.restore_menu = QMenu(RESTORE_TEXT, menu)
        self.restore_menu.aboutToShow.connect(self._build_restore_menu)
        menu.addAction(self._add_action)
        menu.addAction(self._sync_action)
        menu.addSeparator()
        menu.addMenu(self.accounts_menu)
        menu.addMenu(self.restore_menu)
        menu.addSeparator()
        menu.addAction(self._reminders_action)
        menu.addAction(self._settings_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        self._menu = menu  # keep a reference
        menu.aboutToShow.connect(self._update_add_action)
        menu.aboutToShow.connect(self.update_menus)
        self.setContextMenu(menu)
        self.activated.connect(self._on_activated)
        self._add_done.connect(self._finish_add)
        self.update_tooltip()

    def _tray_rect(self):
        return self.geometry()

    def tray_geometry(self):
        """The icon's screen rect (empty when it sits in the overflow flyout)."""
        return self._tray_rect()

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
        now = self.now()
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

    # -- Accounts ▸ / Restore hidden ▸ ------------------------------------------
    def update_menus(self) -> None:
        """Rebuild the Accounts and Restore hidden submenus from the current state."""
        self._build_accounts_menu()
        self._build_restore_menu()

    def _visibility_changed(self) -> None:
        """Something the tray menu changed is shown/hidden now: every view follows."""
        self.popup.refresh()
        self.update_tooltip()
        if self._on_visibility_changed:
            try:
                self._on_visibility_changed()
            except Exception:  # noqa: BLE001
                log.exception("refresh after a visibility change failed")

    def _save_failed(self) -> None:
        self.showMessage("SmartCal", meeting_menus.SAVE_FAILED,
                         QSystemTrayIcon.MessageIcon.Warning)

    def _build_accounts_menu(self) -> None:
        menu = self.accounts_menu
        menu.clear()
        try:
            accounts = list(self._accounts_fn())
        except Exception:  # noqa: BLE001
            log.exception("listing accounts failed")
            accounts = []
        if not accounts:
            none = QAction(NO_ACCOUNTS_TEXT, menu)
            none.setEnabled(False)
            menu.addAction(none)
            return
        for account in accounts:
            act = QAction(menu_text(account), menu)
            act.setCheckable(True)
            act.setChecked(not self.settings.is_account_hidden(account))
            act.setData(account)
            act.setToolTip("Show this account's meetings in SmartCal")
            act.toggled.connect(lambda shown, a=account: self.set_account_shown(a, shown))
            menu.addAction(act)

    def set_account_shown(self, account: str, shown: bool) -> None:
        try:
            self.settings.set_account_hidden(account, not shown)
        except OSError:
            self._save_failed()
            return
        self._visibility_changed()

    def _build_restore_menu(self) -> None:
        menu = self.restore_menu
        menu.clear()
        hidden = list(self.settings.hidden_events.items())
        menu.menuAction().setEnabled(bool(hidden))
        menu.setTitle(RESTORE_TEXT if hidden else NOTHING_HIDDEN_TEXT)
        if not hidden:
            return
        for key, info in reversed(hidden):          # newest first
            act = QAction(restore_label(info), menu)
            act.setData(key)
            act.triggered.connect(lambda _c=False, k=key: self.restore_hidden(k))
            menu.addAction(act)
        menu.addSeparator()
        restore_all = QAction(RESTORE_ALL_TEXT, menu)
        restore_all.triggered.connect(lambda _c=False: self.restore_all_hidden())
        menu.addAction(restore_all)

    def restore_hidden(self, key: str) -> None:
        try:
            self.settings.unhide(key)
        except OSError:
            self._save_failed()
            return
        self._visibility_changed()

    def restore_all_hidden(self) -> None:
        try:
            self.settings.unhide_all()
        except OSError:
            self._save_failed()
            return
        self._visibility_changed()

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
        track_dialog(self._reminders_ref, dlg)
        placement.show_near_tray(dlg, self.tray_geometry())

    @property
    def _reminders_dialog(self):
        return self._reminders_ref[0] if self._reminders_ref else None

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
            except google_auth.SignInTimedOut:
                status = "timeout"
                log.info("sign-in timed out")
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
            elif status == "timeout":
                if current:
                    self.showMessage("SmartCal", google_auth.TIMED_OUT_TEXT,
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
        Every state file is written atomically, so nothing is left half-written.

        The single-instance lock and server are released first: a relaunch during the
        wait below must start a new copy, not hand over to this exiting one."""
        if self._instance is not None:
            try:
                self._instance.release()
            except Exception:  # noqa: BLE001
                log.exception("releasing the single-instance lock failed")
        self.worker.stop()
        if not self.worker.wait(STOP_WAIT_MS):
            log.warning("sync thread did not stop; exiting without waiting for it")
            try:
                self.hide()   # os._exit skips cleanup: remove the icon, no ghost in the tray
            except Exception:  # noqa: BLE001
                log.exception("hiding the tray icon failed")
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
