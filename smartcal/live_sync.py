"""SyncWorker that re-reads the stored accounts on every pass."""
from __future__ import annotations

import logging

from .google_auth import ReauthRequired, list_accounts
from .sync import SyncWorker

log = logging.getLogger(__name__)


class LiveSyncWorker(SyncWorker):
    """Picks up accounts added while running; one bad account never stops the thread."""

    def __init__(self, cache, client_for, accounts_fn=list_accounts, **kw):
        super().__init__(cache, client_for, **kw)
        self._accounts_fn = accounts_fn

    def _accounts(self) -> list[str]:
        try:
            return list(self._accounts_fn())
        except Exception:  # noqa: BLE001
            log.exception("could not list accounts")
            return []

    def sync_account(self, account: str) -> str:
        # Base class already maps ReauthRequired/others raised by the client factory;
        # this guard also covers anything else outside its try block.
        try:
            return super().sync_account(account)
        except ReauthRequired:
            return "reauth"
        except Exception:  # noqa: BLE001
            log.exception("sync of %s crashed", account)
            return "error"
