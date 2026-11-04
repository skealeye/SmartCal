import sys
import threading

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from smartcal import __version__
from smartcal.config import data_dir
from smartcal.db import Cache
from smartcal.google_auth import list_accounts
from smartcal.google_client import GoogleClient
from smartcal.live_sync import LiveSyncWorker
from smartcal.logsetup import guarded, install_excepthooks, setup_logging
from smartcal.settings import Settings
from smartcal.ui.popup import AgendaPopup
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
    tray = TrayController(popup, worker, cache, settings, on_accounts_changed=drop_clients)
    tray.show()

    @guarded
    def on_synced(_account: str) -> None:
        accounts = list_accounts()
        popup.set_no_accounts(not accounts)
        popup.prune_accounts(accounts)
        popup.refresh()
        window.refresh()
        tray.update_tooltip()

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

    worker.start()
    code = app.exec()
    worker.stop()
    worker.wait(3000)
    return code


if __name__ == "__main__":
    sys.exit(main())
