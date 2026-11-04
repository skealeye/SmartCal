"""Windows toast sender (winotify), with a tray-balloon fallback.

winotify builds a PowerShell script by string formatting, so every piece of text
is sanitized here: no markup, no PowerShell expansion ($, backtick, quotes) and
no line breaks can survive into the script.
"""
from __future__ import annotations

import hashlib
import logging
import re
from urllib.parse import quote, urlsplit

log = logging.getLogger(__name__)

APP_ID = "SmartCal"
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f  ]+")
_TEXT_MAP = str.maketrans({"$": "＄", "`": "'", '"': "”", "<": "‹",
                           ">": "›", "&": "+"})


def clean_text(text: str, limit: int = 200) -> str:
    """Single-line plain text safe to embed in the toast script."""
    text = _CONTROL.sub(" ", str(text or "")).translate(_TEXT_MAP)
    text = text.replace("]]", "] ]").strip()
    return text[:limit]


def join_url(url: str | None) -> str | None:
    """Return `url` made safe for the toast's Join button, or None unless it is http(s)."""
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        return None
    if _CONTROL.search(url) or " " in url:
        return None
    # Percent-encode everything that could break out of the XML attribute or the script.
    safe = quote(url, safe="/:?=&%#~-._+,;@!*()[]")
    return safe.replace("&", "&amp;")


class ToastSender:
    """Callable `(title, body, url) -> None`; never raises for display problems."""

    def __init__(self, tray=None, notification_cls=None):
        self.tray = tray
        self._cls = notification_cls

    def _notification_cls(self):
        if self._cls is None:
            from winotify import Notification  # may raise ImportError
            self._cls = Notification
        return self._cls

    def __call__(self, title: str, body: str, url: str | None = None) -> None:
        try:
            self._show_native(title, body, url)
        except Exception as exc:  # noqa: BLE001 - fall back to the tray balloon
            log.warning("native toast failed (%s); using tray balloon", exc)
            self._show_tray(title, body)

    def _show_native(self, title: str, body: str, url: str | None) -> None:
        title, body = clean_text(title, 120), clean_text(body)
        note = self._notification_cls()(app_id=APP_ID, title=title, msg=body)
        # winotify defaults tag to the title and puts it in a PowerShell string.
        note.tag = hashlib.sha1((title + body).encode("utf-8")).hexdigest()[:16]
        link = join_url(url)
        if link:
            note.add_actions(label="Join", launch=link)
        note.show()

    def _show_tray(self, title: str, body: str) -> None:
        if self.tray is None:
            return
        from PyQt6.QtWidgets import QSystemTrayIcon
        self.tray.showMessage(title, body, QSystemTrayIcon.MessageIcon.Information, 10_000)
