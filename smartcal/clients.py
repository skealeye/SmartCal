"""Per-thread Google client cache.

A GoogleClient holds one httplib2 connection, which is not thread-safe. The sync
thread and the chat threads must never share one, so clients are cached per
(thread, account). Entries of threads that have finished are dropped.
"""
from __future__ import annotations

import threading
from typing import Callable


class ClientPool:
    def __init__(self, factory: Callable[[str], object]):
        self._factory = factory
        self._lock = threading.Lock()
        # (thread ident, account) -> (thread, client)
        self._clients: dict[tuple[int, str], tuple[threading.Thread, object]] = {}

    def get(self, account: str):
        """The calling thread's client for `account` (built on first use)."""
        me = threading.current_thread()
        key = (threading.get_ident(), account)
        with self._lock:
            self._prune()
            hit = self._clients.get(key)
            if hit is not None and hit[0] is me:
                return hit[1]
        client = self._factory(account)   # built outside the lock (may read token files)
        with self._lock:
            self._clients[key] = (me, client)
        return client

    __call__ = get

    def clear(self) -> None:
        """Forget every client (e.g. after accounts changed: new credentials)."""
        with self._lock:
            self._clients.clear()

    def __len__(self) -> int:
        with self._lock:
            self._prune()
            return len(self._clients)

    def _prune(self) -> None:
        dead = [k for k, (t, _) in self._clients.items() if not _alive(t)]
        for k in dead:
            del self._clients[k]


def _alive(thread) -> bool:
    """is_alive(), treating a thread whose state cannot be read as dead."""
    try:
        return thread.is_alive()
    except RuntimeError:
        return False
