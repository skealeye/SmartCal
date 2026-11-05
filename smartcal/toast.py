"""Windows toast sender (winotify), with a tray-balloon fallback.

winotify builds a PowerShell script by string formatting, so every piece of text
is sanitized here with an allowlist: no markup, no PowerShell expansion or quote
characters and no line breaks can survive into the script.

Note: winotify's show() only spawns powershell.exe, so a failure inside
PowerShell (bad script, notifications disabled) cannot be detected; only spawn
failures fall back to the tray balloon.
"""
from __future__ import annotations

import hashlib
import logging
import re
from urllib.parse import quote, urlsplit

log = logging.getLogger(__name__)

APP_ID = "SmartCal"
APP_ID_KEY = r"Software\Classes\AppUserModelId\SmartCal"
TITLE_LIMIT, BODY_LIMIT = 120, 300
_CONTROL = re.compile("[\x00-\x1f\x7f-\x9f  ]")
_NEUTRAL_QUOTE = "ʼ"  # modifier apostrophe: not a PowerShell quote
_QUOTES = set("\"'`«»‘’‚‛“”„‟"
              "‹›＂＇")
_FULLWIDTH = {"$": "＄", "<": "＜", ">": "＞", "&": "＆"}


def _xml_ok(cp: int) -> bool:
    return (cp in (0x9, 0xA, 0xD) or 0x20 <= cp <= 0xD7FF or 0xE000 <= cp <= 0xFFFD
            or 0x10000 <= cp <= 0x10FFFF) and cp not in (0xFFFE, 0xFFFF)


def clean_text(text: str, limit: int = BODY_LIMIT) -> str:
    """Single-line plain text safe to embed in the toast script (allowlist)."""
    out = []
    for ch in str(text or ""):
        cp = ord(ch)
        if ch in _QUOTES:
            out.append(_NEUTRAL_QUOTE)
        elif ch in _FULLWIDTH:
            out.append(_FULLWIDTH[ch])
        elif _CONTROL.match(ch):
            out.append(" ")  # control characters and line separators
        elif _xml_ok(cp):
            out.append(ch)  # lone surrogates, U+FFFE/U+FFFF, XML-invalid: dropped
    t = re.sub(" {2,}", " ", "".join(out)).replace("]]", "] ]").strip()
    return t[:limit]


def register_app_id(key_path: str = APP_ID_KEY) -> None:
    """Register the toast app id (display name) under HKCU; idempotent."""
    import winreg
    from smartcal.config import no_system_writes
    if key_path == APP_ID_KEY and no_system_writes():
        return
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, APP_ID)


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
            try:
                self._show_tray(clean_text(title, TITLE_LIMIT), clean_text(body, BODY_LIMIT))
            except Exception:  # noqa: BLE001
                log.exception("tray balloon failed too")

    def _show_native(self, title: str, body: str, url: str | None) -> None:
        title, body = clean_text(title, TITLE_LIMIT), clean_text(body, BODY_LIMIT)
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
