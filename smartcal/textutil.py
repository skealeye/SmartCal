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
_BLOCK_START = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "ul", "ol", "li",
                "table", "blockquote"}
_CELLS = {"td", "th"}
_BAD_CHARS = re.compile("[\x00-\x08\x0e-\x1f\x7f-\x9f​-‏  "
                        "‪-‮⁠-⁩﻿]")
_UNSAFE_URL = re.compile(r"[\\\s\x00-\x1f\x7f-\x9f]")


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
        elif tag in _BLOCK_START:
            if self._link is None and not self._at_line_start():
                self.out.append("\n")
            if tag == "li":
                self._emit("• ")
        elif tag in _CELLS:
            if (self._link is None and not self._at_line_start()
                    and not (self.out and self.out[-1].endswith("\t"))):
                self.out.append("\t")
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
    text = text.replace("\x0b", "\n").replace("\x0c", "\n")
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    text = _BAD_CHARS.sub("", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip("\n")
    if len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text


def safe_http_url(url) -> str | None:
    """The URL if it is a plain http(s) URL with no backslash, whitespace, control chars or
    userinfo (`@` in the host part); else None."""
    if not isinstance(url, str) or not url or _UNSAFE_URL.search(url):
        return None
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not host or "@" in parts.netloc:
        return None
    return url


# A URL candidate stops at whitespace and at characters that could close an HTML
# attribute or tag; the linkified HTML is built from escaped text either way.
_URL_CANDIDATE = re.compile(r"\bhttps?://[^\s<>\"'`]+", re.IGNORECASE)
_TRAILING_PUNCT = ".,;:!?"


def _trim_url(url: str) -> str:
    """Drop trailing sentence punctuation and unbalanced closing brackets.

    Bracket counts are taken once and updated as characters are dropped (linear time,
    even for a 20,000-character run of ")")."""
    opens = {")": url.count("("), "]": url.count("[")}
    closes = {")": url.count(")"), "]": url.count("]")}
    end = len(url)
    while end:
        last = url[end - 1]
        if last in _TRAILING_PUNCT:
            end -= 1
        elif last in closes and closes[last] > opens[last]:
            closes[last] -= 1
            end -= 1
        else:
            break
    return url[:end]


def plain_tip(text: str) -> str:
    """A tooltip string Qt will not interpret as rich text markup."""
    return f"<p style='white-space:pre'>{html.escape(text)}</p>"


def linkify_to_html(plain: str) -> str:
    """HTML for already-plain text: everything escaped, http(s) URLs wrapped in <a>.

    Only URLs that pass `safe_http_url` become links; anything else stays escaped text.
    Line breaks are kept with white-space:pre-wrap. Never passes markup through."""
    plain = plain or ""
    out: list[str] = []
    pos = 0
    for m in _URL_CANDIDATE.finditer(plain):
        url = _trim_url(m.group())
        if not url or safe_http_url(url) is None or urlsplit(url).hostname is None:
            continue
        start, end = m.start(), m.start() + len(url)
        out.append(html.escape(plain[pos:start], quote=True))
        esc = html.escape(url, quote=True)
        out.append(f'<a href="{esc}">{esc}</a>')
        pos = end
    out.append(html.escape(plain[pos:], quote=True))
    return '<div style="white-space:pre-wrap;">' + "".join(out) + "</div>"


def meeting_label(url: str | None) -> str:
    """Button label for a meeting link, decided by the parsed hostname only."""
    if safe_http_url(url) is None:
        return "Join meeting"
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    if host == "zoom.us" or host.endswith(".zoom.us"):
        return "Join Zoom meeting"
    if host == "meet.google.com":
        return "Join Google Meet"
    if host in ("teams.microsoft.com", "teams.live.com"):
        return "Join Teams meeting"
    return "Join meeting"


_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def looks_like_email(text) -> bool:
    """True for `x@y.z` (a calendar named after its account, e.g. the primary one)."""
    return isinstance(text, str) and _EMAIL.fullmatch(text.strip()) is not None


def calendar_label(name) -> str:
    """The calendar name to show in the UI, or "" when it is only an email address
    (an account's primary calendar is named after the account: no information)."""
    if not isinstance(name, str) or looks_like_email(name):
        return ""
    return name.strip()
