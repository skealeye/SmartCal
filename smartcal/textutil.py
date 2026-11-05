"""Text helpers: Google event-description HTML to safe plain text, meeting link labels."""
from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit

DESCRIPTION_LIMIT = 20_000
_INPUT_CAP = 150_000          # never parse more than this many characters
_TAG_START = re.compile(r"<[A-Za-z/!?]")
_NEWLINE_AFTER = {"br", "p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr"}
_SKIP = {"script", "style"}


class _Converter(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._skip = 0
        self._href: str | None = None
        self._link: list[str] | None = None

    def _emit(self, s: str) -> None:
        if self._link is not None:
            self._link.append(s)
        else:
            self.out.append(s)

    def _at_line_start(self) -> bool:
        for chunk in reversed(self.out):
            if chunk:
                return chunk.endswith("\n")
        return True

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self._skip += 1
        elif self._skip:
            return
        elif tag == "br":
            self._emit("\n")
        elif tag == "li":
            if self._link is None and not self._at_line_start():
                self.out.append("\n")
            self._emit("• ")
        elif tag == "a" and self._link is None:
            self._href = dict(attrs).get("href") or ""
            self._link = []

    def handle_startendtag(self, tag, attrs):
        if tag == "br" and not self._skip:
            self._emit("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP:
            self._skip = max(0, self._skip - 1)
        elif self._skip:
            return
        elif tag == "a" and self._link is not None:
            self._finish_link()
        elif tag in _NEWLINE_AFTER and tag != "br":
            self._emit("\n")

    def _finish_link(self) -> None:
        text = "".join(self._link or [])
        href = (self._href or "").strip()
        self._link, self._href = None, None
        if href.lower().startswith(("http://", "https://")) and text.strip() \
                and text.strip() != href:
            self.out.append(f"{text} ({href})")
        else:
            self.out.append(text)

    def handle_data(self, data):
        if not self._skip:
            self._emit(data)

    def finish(self) -> str:
        self.close()
        if self._link is not None:
            self._finish_link()
        return "".join(self.out)


def html_to_text(s: str, limit: int = DESCRIPTION_LIMIT) -> str:
    """Convert event-description HTML to plain text. Never produces markup of its own."""
    if not s:
        return ""
    s = s[:_INPUT_CAP]
    if _TAG_START.search(s):
        p = _Converter()
        try:
            p.feed(s)
            text = p.finish()
        except Exception:                      # malformed input: fall back to tag-free text
            text = html.unescape(re.sub(r"<[^>]*>", "", s))
    else:
        text = html.unescape(s)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip("\n")
    if len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text


def meeting_label(url: str | None) -> str:
    """Button label for a meeting link, decided by the parsed hostname only."""
    try:
        host = (urlsplit(url or "").hostname or "").lower().rstrip(".")
    except ValueError:
        return "Join meeting"
    if host == "zoom.us" or host.endswith(".zoom.us"):
        return "Join Zoom meeting"
    if host == "meet.google.com":
        return "Join Google Meet"
    if host in ("teams.microsoft.com", "teams.live.com"):
        return "Join Teams meeting"
    return "Join meeting"
