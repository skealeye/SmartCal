"""Google OAuth: consent flow, token storage, credential loading."""
from __future__ import annotations

import re
from pathlib import Path

from smartcal.config import data_dir

SCOPES = ["https://www.googleapis.com/auth/calendar"]


class ReauthRequired(Exception):
    """The account has no valid token and the user must sign in again."""


def _tokens_dir() -> Path:
    path = data_dir() / "tokens"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe(account: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "_", account)


def token_path(account: str) -> Path:
    return _tokens_dir() / f"{_safe(account)}.json"


def list_accounts() -> list[str]:
    """Emails of accounts that have a stored token."""
    return sorted(p.stem for p in _tokens_dir().glob("*.json"))


SIGNIN_TIMEOUT = 120      # seconds the local OAuth server waits for the browser
SIGNIN_DEAD_AFTER = 125   # a sign-in pending longer than this is dead: allow a new one


class SignInCancelled(Exception):
    """The owner cancelled a pending Google sign-in (not an error)."""


def free_port() -> int:
    """A free local TCP port for the OAuth redirect server."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("localhost", 0))
        return s.getsockname()[1]


def _make_flow():
    from google_auth_oauthlib.flow import InstalledAppFlow
    secret = data_dir() / "client_secret.json"
    if not secret.exists():
        raise FileNotFoundError(f"Missing {secret}. See README.md for setup steps.")
    return InstalledAppFlow.from_client_secrets_file(str(secret), SCOPES)


def _http_get(url: str) -> None:
    import urllib.request
    with urllib.request.urlopen(url, timeout=3) as resp:   # noqa: S310 - localhost only
        resp.read()


def _store_credentials(creds) -> str:
    """Save a new token under the account's email (its primary calendar id)."""
    from googleapiclient.discovery import build
    service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    email = service.calendars().get(calendarId="primary").execute()["id"]
    _write_atomic(token_path(email), creds.to_json())
    return email


class SignIn:
    """One browser consent flow at a time, which the owner can cancel.

    The local redirect server listens on a port chosen here, so `cancel()` can
    end it at once: any request makes the server return, and the
    `error=access_denied` redirect makes the token exchange fail, which `run()`
    reports as SignInCancelled.
    """

    def __init__(self, flow_factory=None, store=None, http_get=None, port_fn=free_port):
        import threading
        self._flow_factory = flow_factory or _make_flow
        self._store = store or _store_credentials
        self._http_get = http_get or _http_get
        self._port_fn = port_fn
        self._lock = threading.Lock()
        self.port: int | None = None
        self._cancelled = False

    def run(self, label: str = "new") -> str:
        """Blocking (run it on a worker thread); returns the new account's email."""
        flow = self._flow_factory()
        port = self._port_fn()
        with self._lock:
            self.port, self._cancelled = port, False
        try:
            try:
                creds = flow.run_local_server(port=port, open_browser=True,
                                              timeout_seconds=SIGNIN_TIMEOUT)
            except Exception:
                if self._cancelled:
                    raise SignInCancelled() from None
                raise
            if creds is None or self._cancelled:
                raise SignInCancelled()
            return self._store(creds)
        finally:
            with self._lock:
                if self.port == port:
                    self.port = None

    def cancel(self) -> bool:
        """End a pending sign-in; False if none is running."""
        import threading
        with self._lock:
            port = self.port
            if port is None:
                return False
            self._cancelled = True
        url = f"http://localhost:{port}/?error=access_denied"

        def poke() -> None:
            try:
                self._http_get(url)
            except Exception:  # noqa: BLE001 - the server may already be gone
                pass
        threading.Thread(target=poke, daemon=True, name="cancel-signin").start()
        return True


SIGN_IN = SignIn()


def add_account(label: str) -> str:
    """Run the browser consent flow; store the token; return the account email.

    `label` is only a hint; identity is the primary calendar id. Raises
    SignInCancelled if SIGN_IN.cancel() was called meanwhile.
    """
    return SIGN_IN.run(label)


def remove_account(account: str, cache=None) -> None:
    """Forget an account: delete its token file and purge its cached data."""
    token_path(account).unlink(missing_ok=True)
    if cache is not None:
        cache.delete_account(account)


def load_credentials(account: str):
    """Return valid credentials, refreshing if needed; raise ReauthRequired otherwise."""
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    path = token_path(account)
    if not path.exists():
        raise ReauthRequired(account)
    try:
        creds = Credentials.from_authorized_user_file(str(path), SCOPES)
    except (ValueError, KeyError) as exc:
        raise ReauthRequired(account) from exc
    if creds.valid:
        return creds
    if creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError as exc:
            raise ReauthRequired(account) from exc
        _write_atomic(path, creds.to_json())
        return creds
    raise ReauthRequired(account)


def _write_atomic(path: Path, text: str) -> None:
    """Several threads may refresh the same token at once: never leave a torn file."""
    import os
    import threading
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
