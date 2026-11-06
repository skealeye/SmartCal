import logging
import os
import sys

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QApplication

from smartcal import __version__, autostart
from smartcal.actions import Actions
from smartcal.assistant import Assistant, make_client
from smartcal.clients import ClientPool
from smartcal.config import data_dir
from smartcal.db import Cache
from smartcal.google_auth import list_accounts, remove_account
from smartcal.google_client import GoogleClient
from smartcal.live_sync import LiveSyncWorker
from smartcal.logsetup import (enable_crash_log, guarded, install_excepthooks,
                               install_qt_message_handler, setup_logging)
from smartcal.notifier import Notifier
from smartcal.reminders import ReminderFirer, Reminders
from smartcal.settings import Settings
from smartcal.single_instance import SingleInstance
from smartcal.undo import UndoStore
from smartcal.toast import ToastSender, register_app_id
from smartcal.ui import motion, wintrace
from smartcal.ui.placement import show_near_tray
from smartcal.ui.popup import AgendaPopup
from smartcal.ui.settings_dialog import SettingsDialog
from smartcal.ui.tray import TrayController, dialog_visible, track_dialog
from smartcal.ui.window import CalendarWindow


TOOK_OVER_TEXT = "SmartCal was already running but not responding; starting a new copy"


def main() -> int:
    install_excepthooks()   # first: even a failure while setting up logging is caught
    setup_logging(data_dir() / "smartcal.log")
    # A native crash leaves the Python stacks in crash.log; Qt's warnings go to the log.
    if not enable_crash_log(data_dir() / "crash.log", __version__):
        logging.getLogger(__name__).warning("could not open crash.log")
    install_qt_message_handler()
    logging.getLogger(__name__).info("SmartCal %s starting", __version__)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("SmartCal")
    wintrace.install(app)   # SMARTCAL_DEBUG_UI=1 only: every window of ours, in the log

    instance = SingleInstance(data_dir())
    if not instance.acquire():
        logging.getLogger(__name__).info("SmartCal is already running; showed its popup")
        return 0

    cache = Cache(data_dir() / "cache.db")
    cache.purge_orphans(list_accounts())
    settings = Settings()
    motion.set_user_preference(settings.animations)   # before any window exists
    logging.getLogger(__name__).info(motion.describe_mode())
    settings.prune_hidden_accounts(list_accounts())   # no token left: not hidden either
    autostart.apply_at_startup(settings)  # on by default, first run only
    try:
        register_app_id()
    except OSError:
        logging.getLogger(__name__).exception("could not register toast app id")

    # One GoogleClient per (thread, account): the sync thread and chat threads never
    # share an httplib2 connection.
    clients = ClientPool(GoogleClient)
    client_for = clients.get

    def drop_clients() -> None:
        clients.clear()

    worker = LiveSyncWorker(cache, client_for)

    popup = AgendaPopup(cache, settings)
    popup.set_accounts(list_accounts())
    popup.refresh()
    window = CalendarWindow(cache, settings)

    @guarded
    def open_window(view: str) -> None:
        popup.dismiss()
        window.show_view(view)

    popup.view_requested.connect(open_window)
    toast = ToastSender()
    notifier = Notifier(cache, settings, toast)
    reminders = Reminders()  # shared with the tray; Task 9's assistant gets the same instance
    app.reminders = reminders
    reminder_firer = ReminderFirer(reminders, toast,
                                   on_fired=lambda: tray.refresh_reminders())
    dialog_ref: list[SettingsDialog] = []

    def sync_settings_dialog() -> None:
        """An open Settings dialog shows changes made elsewhere (e.g. Color style)."""
        if dialog_ref and dialog_visible(dialog_ref[0]):
            dialog_ref[0].sync_from_settings()

    @guarded
    def refresh_views() -> None:
        popup.refresh()
        window.refresh()
        tray.update_tooltip()
        sync_settings_dialog()

    @guarded
    def refresh_others() -> None:
        notifier.mark_in_progress()   # e.g. Undo restored a meeting: no late toast
        window.refresh()
        sync_settings_dialog()
        tray.update_tooltip()

    # Hiding or coloring a meeting (popup details or window details) updates every view.
    # The popup has already refreshed itself: once is enough.
    popup.meetings_changed.connect(refresh_others)
    window.meetings_changed.connect(refresh_views)

    autostart_watcher = autostart.SettingsWatcher(settings)   # after apply_at_startup

    @guarded
    def on_settings_applied() -> None:
        autostart_watcher.settings_applied()   # Run key only when the box was toggled
        notifier.mark_in_progress()   # unhidden in Settings: no late "Started" toast
        refresh_views()
        notifier.check()

    @guarded
    def remove_account_and_refresh(account: str) -> None:
        remove_account(account, cache, settings)
        drop_clients()
        popup.set_accounts(list_accounts())
        popup.prune_accounts(list_accounts())
        refresh_views()

    @guarded
    def open_settings() -> None:
        if dialog_ref and dialog_visible(dialog_ref[0]):
            dialog_ref[0].raise_()
            dialog_ref[0].activateWindow()
            return
        dlg = SettingsDialog(settings, cache, list_accounts, tray.add_account,
                             remove_account_and_refresh, signin_pending=tray.signin_pending,
                             on_cancel_signin=tray.cancel_signin)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dlg.applied.connect(on_settings_applied)
        tray.signin_changed.connect(dlg.refresh_signin)
        track_dialog(dialog_ref, dlg)
        show_near_tray(dlg, tray.tray_geometry())   # opened from the tray menu

    assistant_ref: list[Assistant] = []

    def assistant_factory() -> Assistant:
        # Built once on the first chat message; the same UndoStore/Actions serve every message.
        if not assistant_ref:
            actions = Actions(client_for, cache, UndoStore(data_dir() / "undo.json"), settings)
            assistant_ref.append(Assistant(make_client(), actions, app.reminders, settings, cache))
        return assistant_ref[0]

    @guarded
    def on_chat_changed() -> None:
        refresh_views()
        worker.sync_now()

    popup.attach_chat(assistant_factory)
    popup.chat_changed.connect(on_chat_changed)

    @guarded
    def on_visibility_changed() -> None:
        # The tray menu showed/hid an account or restored a hidden meeting; the tray
        # has already refreshed the popup and its tooltip.
        window.refresh()
        notifier.mark_in_progress()   # shown again: no late "Started" toast
        notifier.check()
        if dialog_ref and dialog_visible(dialog_ref[0]):
            dialog_ref[0].sync_from_settings()

    tray = TrayController(popup, worker, cache, settings, on_accounts_changed=drop_clients,
                          on_open_settings=open_settings, reminders=reminders,
                          on_before_exit=window.save_state, instance=instance,
                          accounts_fn=list_accounts,
                          on_visibility_changed=on_visibility_changed)
    toast.tray = tray
    window.tray_geometry = tray.tray_geometry   # first open: next to the tray icon
    popup.chat_attention.connect(guarded(tray.notify_attention))
    instance.activated.connect(guarded(tray.show_popup))   # a second launch was refused
    tray.show()
    if instance.took_over:
        tray.showMessage("SmartCal", TOOK_OVER_TEXT)

    @guarded
    def on_synced(_account: str) -> None:
        accounts = list_accounts()
        cache.purge_orphans(accounts)   # a sync in flight during removal re-adds rows
        popup.set_accounts(accounts)
        popup.prune_accounts(accounts)
        popup.refresh()
        window.refresh()
        tray.update_tooltip()
        if dialog_ref and dialog_visible(dialog_ref[0]):
            dialog_ref[0].refresh_accounts()

    worker.synced.connect(on_synced)
    worker.status.connect(guarded(popup.set_account_status))

    timer = QTimer()
    timer.setInterval(30_000)

    @guarded
    def tick() -> None:
        tray.update_tooltip()
        popup.prune_accounts(list_accounts())
        if popup.is_open():
            popup.refresh()

    timer.timeout.connect(tick)
    timer.start()

    notify_timer = QTimer()
    notify_timer.setInterval(30_000)
    notify_timer.timeout.connect(guarded(notifier.check))
    notify_timer.start()
    notify_timer.timeout.connect(guarded(reminder_firer.check))
    QTimer.singleShot(2000, guarded(notifier.check))
    QTimer.singleShot(2000, guarded(reminder_firer.check))

    # v0.9.1: quitting (any way, e.g. Windows ending the session) never plays the outro.
    app.aboutToQuit.connect(popup.shutdown)
    worker.start()
    code = app.exec()
    instance.release()
    worker.stop()
    if not worker.wait(3000):
        # Never terminate() a thread mid-request; state files are written atomically.
        window.save_state()
        for handler in logging.getLogger().handlers:
            handler.flush()
        os._exit(code)
    return code


if __name__ == "__main__":
    sys.exit(main())
