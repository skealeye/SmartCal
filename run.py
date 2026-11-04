import logging
import sys
import threading

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QApplication

from smartcal import __version__, autostart
from smartcal.config import data_dir
from smartcal.db import Cache
from smartcal.google_auth import list_accounts, remove_account
from smartcal.google_client import GoogleClient
from smartcal.live_sync import LiveSyncWorker
from smartcal.logsetup import guarded, install_excepthooks, setup_logging
from smartcal.notifier import Notifier
from smartcal.reminders import ReminderFirer, Reminders
from smartcal.settings import Settings
from smartcal.toast import ToastSender, register_app_id
from smartcal.ui.popup import AgendaPopup
from smartcal.ui.settings_dialog import SettingsDialog
from smartcal.ui.tray import TrayController
from smartcal.ui.window import CalendarWindow


def main() -> int:
    setup_logging(data_dir() / "smartcal.log")
    install_excepthooks()
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("SmartCal")

    cache = Cache(data_dir() / "cache.db")
    settings = Settings()
    autostart.apply_at_startup(settings)  # on by default, first run only
    try:
        register_app_id()
    except OSError:
        logging.getLogger(__name__).exception("could not register toast app id")

    clients: dict[str, GoogleClient] = {}
    clients_lock = threading.Lock()

    def client_for(account: str) -> GoogleClient:
        with clients_lock:
            if account not in clients:
                clients[account] = GoogleClient(account)
            return clients[account]

    def drop_clients() -> None:
        with clients_lock:
            clients.clear()

    worker = LiveSyncWorker(cache, client_for, settings)

    popup = AgendaPopup(cache, settings)
    popup.set_no_accounts(not list_accounts())
    popup.refresh()
    window = CalendarWindow(cache, settings)

    @guarded
    def open_window(view: str) -> None:
        popup.hide()
        window.show_view(view)

    popup.view_requested.connect(open_window)
    toast = ToastSender()
    notifier = Notifier(cache, settings, toast)
    reminders = Reminders()  # shared with the tray; Task 9's assistant gets the same instance
    app.reminders = reminders
    reminder_firer = ReminderFirer(reminders, toast)
    dialog_ref: list[SettingsDialog] = []

    @guarded
    def refresh_views() -> None:
        popup.refresh()
        window.refresh()
        tray.update_tooltip()

    @guarded
    def on_settings_applied() -> None:
        autostart.apply(settings.autostart)
        refresh_views()
        notifier.check()

    @guarded
    def remove_account_and_refresh(account: str) -> None:
        remove_account(account, cache)
        drop_clients()
        popup.set_no_accounts(not list_accounts())
        popup.prune_accounts(list_accounts())
        refresh_views()

    @guarded
    def open_settings() -> None:
        if dialog_ref and dialog_ref[0].isVisible():
            dialog_ref[0].raise_()
            dialog_ref[0].activateWindow()
            return
        dlg = SettingsDialog(settings, cache, list_accounts, tray.add_account,
                             remove_account_and_refresh)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dlg.applied.connect(on_settings_applied)
        dialog_ref[:] = [dlg]
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    tray = TrayController(popup, worker, cache, settings, on_accounts_changed=drop_clients,
                          on_open_settings=open_settings, reminders=reminders)
    toast.tray = tray
    tray.show()

    @guarded
    def on_synced(_account: str) -> None:
        accounts = list_accounts()
        popup.set_no_accounts(not accounts)
        popup.prune_accounts(accounts)
        popup.refresh()
        window.refresh()
        tray.update_tooltip()
        if dialog_ref and dialog_ref[0].isVisible():
            dialog_ref[0].refresh_accounts()

    worker.synced.connect(on_synced)
    worker.status.connect(guarded(popup.set_account_status))

    timer = QTimer()
    timer.setInterval(30_000)

    @guarded
    def tick() -> None:
        tray.update_tooltip()
        popup.prune_accounts(list_accounts())
        if popup.isVisible():
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

    worker.start()
    code = app.exec()
    worker.stop()
    worker.wait(3000)
    return code


if __name__ == "__main__":
    sys.exit(main())
