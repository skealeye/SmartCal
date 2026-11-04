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


def add_account(label: str) -> str:
    """Run the browser consent flow; store the token; return the account email.

    `label` is only a hint for logs/prompts; identity is the primary calendar id.
    """
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    secret = data_dir() / "client_secret.json"
    if not secret.exists():
        raise FileNotFoundError(f"Missing {secret}. See README.md for setup steps.")
    print(f"Signing in Google account '{label}'...")
    flow = InstalledAppFlow.from_client_secrets_file(str(secret), SCOPES)
    creds = flow.run_local_server(port=0, timeout_seconds=180)
    service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    email = service.calendars().get(calendarId="primary").execute()["id"]
    token_path(email).write_text(creds.to_json(), encoding="utf-8")
    return email


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
        path.write_text(creds.to_json(), encoding="utf-8")
        return creds
    raise ReauthRequired(account)
