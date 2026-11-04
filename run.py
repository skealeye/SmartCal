import sys

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from smartcal import __version__
from smartcal.config import data_dir
from smartcal.db import Cache
from smartcal.google_auth import list_accounts
from smartcal.google_client import GoogleClient
from smartcal.settings import Settings
from smartcal.sync import SyncWorker
from smartcal.ui.popup import AgendaPopup
from smartcal.ui.tray import TrayController


def main() -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName(f"SmartCal {__version__}")

    cache = Cache(data_dir() / "cache.db")
    settings = Settings()

    clients: dict[str, GoogleClient] = {}

    def client_for(account: str) -> GoogleClient:
        if account not in clients:
            clients[account] = GoogleClient(account)
        return clients[account]

    class LiveSyncWorker(SyncWorker):
        """Re-reads stored tokens every pass so newly added accounts sync."""

        def _accounts(self) -> list[str]:
            return list_accounts()

    worker = LiveSyncWorker(cache, client_for, settings)

    popup = AgendaPopup(cache, settings)
    popup.set_no_accounts(not list_accounts())
    popup.refresh()
    tray = TrayController(popup, worker, cache, settings)
    tray.show()

    def on_synced(_account: str) -> None:
        popup.set_no_accounts(not list_accounts())
        popup.refresh()
        tray.update_tooltip()

    worker.synced.connect(on_synced)
    worker.status.connect(popup.set_account_status)

    timer = QTimer()
    timer.setInterval(30_000)

    def tick() -> None:
        tray.update_tooltip()
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
