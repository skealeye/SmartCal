"""Read-only description text with clickable http(s) links.

The HTML is built by `textutil.linkify_to_html` from text that is already plain
(`textutil.html_to_text`): event data is escaped and can never add tags or attributes.
Qt never follows a link by itself; every click is re-validated and only http(s) opens."""
from __future__ import annotations

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QTextOption
from PyQt6.QtWidgets import QTextBrowser

from ..textutil import linkify_to_html, plain_tip, safe_http_url
from . import theme

__all__ = ["LinkText", "plain_tip"]


class LinkText(QTextBrowser):
    """Description box: plain text, URLs clickable, nothing else interpreted."""

    link_opened = pyqtSignal(str)

    def __init__(self, plain: str, parent=None):
        super().__init__(parent)
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setReadOnly(True)
        self.setUndoRedoEnabled(False)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.document().setDefaultStyleSheet(f"a{{color:{theme.LINK};}}")
        self._plain = plain
        self.setHtml(linkify_to_html(plain))
        self.anchorClicked.connect(self.open_link)
        self.highlighted.connect(self._hover)

    def plain(self) -> str:
        return self._plain

    def _hover(self, url: QUrl) -> None:
        text = url.toString() if isinstance(url, QUrl) else str(url or "")
        self.setToolTip(plain_tip(text) if text else "")

    def open_link(self, url) -> bool:
        """Open only plain http(s) URLs (re-checked here, whatever the HTML said)."""
        text = url.toString() if isinstance(url, QUrl) else str(url or "")
        safe = safe_http_url(text)
        if safe is None:
            return False
        QDesktopServices.openUrl(QUrl(safe))
        self.link_opened.emit(safe)
        return True

    def anchors(self) -> list[str]:
        """Every link target in the rendered document (for tests and checks)."""
        out = []
        block = self.document().begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                if frag.isValid() and frag.charFormat().isAnchor():
                    href = frag.charFormat().anchorHref()
                    if not out or out[-1] != href:
                        out.append(href)
                it += 1
            block = block.next()
        return out

    def fit_height(self, width: int, max_lines: int | None = None) -> int:
        """Height that shows the whole text at `width`, capped at `max_lines` lines."""
        doc = self.document()
        frame = 2 * self.frameWidth()
        doc.setTextWidth(max(40, width - frame))
        h = int(doc.size().height()) + frame + 2
        if max_lines:
            cap = max_lines * self.fontMetrics().lineSpacing() + int(2 * doc.documentMargin()) \
                + frame + 2
            h = min(h, cap)
        return h
