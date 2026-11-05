"""The meeting details pop-out shown beside the tray popup.

One instance per popup, built on the first selection and reused: selecting another
meeting swaps its content in place. It is a separate frameless tool window, but the
popup treats the two as one group for focus and lifetime (see AgendaPopup); the popup
owns its menus, its placement and every decision about closing it."""
from __future__ import annotations

import html

from PyQt6.QtCore import QEvent, QRect, Qt
from PyQt6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QPushButton,
                             QScrollArea, QSizePolicy, QStyle, QVBoxLayout, QWidget)

from ..textutil import html_to_text, meeting_label, safe_http_url
from . import layout, meeting_menus, placement, theme
from .linktext import LinkText, plain_tip

WIDTH = 340
MARGINS = 10
DESCRIPTION_LINES = 10          # longer descriptions scroll
HEADER_TEXT = "Meeting details"
# Tab order inside the flyout (whichever exist for the meeting).
TAB_CHAIN = ("bigjoin", "hide", "color", "close")


def _scrollbar_w() -> int:
    style = QApplication.style()
    return style.pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent) if style else 12


class DetailsFlyout(QWidget):
    """Frameless tool window with one meeting's details. `popup` is the AgendaPopup:
    it opens the menus, joins, hides the group and decides what Esc does."""

    def __init__(self, popup):
        super().__init__(popup)
        self._popup = popup
        self._hiding = False
        self.event_shown = None
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint)
        # A mouse selection keeps the popup active; the keyboard path activates explicitly.
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedWidth(WIDTH)
        self.setObjectName("flyout")
        self.setStyleSheet(
            f"#flyout{{background:{theme.BG};border:1px solid {theme.BORDER};}}"
            f"QLabel{{color:{theme.TEXT};font-size:12px;}}")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(MARGINS, 8, MARGINS, MARGINS)
        outer.setSpacing(6)
        head = QHBoxLayout()
        head.setSpacing(6)
        self._header = QLabel(HEADER_TEXT)
        self._header.setObjectName("header")
        self._header.setTextFormat(Qt.TextFormat.PlainText)
        self._header.setStyleSheet(f"color:{theme.MUTED};font-size:11px;")
        self.close_button = QPushButton("✕")
        self.close_button.setObjectName("close")
        self.close_button.setFlat(True)
        self.close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_button.setToolTip("Close details (Esc)")
        self.close_button.setAccessibleName("Close details")
        self.close_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.close_button.setStyleSheet(
            f"QPushButton{{color:{theme.MUTED};border:none;padding:0 4px;font-size:12px;}}"
            f"QPushButton:hover{{color:{theme.TEXT};}}"
            f"QPushButton:focus{{color:{theme.TEXT};border:1px solid {theme.LINK};}}")
        self.close_button.clicked.connect(lambda _c=False: self._popup.close_flyout())
        head.addWidget(self._header, 1)
        head.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignTop)
        outer.addLayout(head)
        self._scroll = QScrollArea()
        self._scroll.setObjectName("flyoutscroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet("QScrollArea{background:transparent;}")
        outer.addWidget(self._scroll)

    # -- content -------------------------------------------------------------
    def body(self) -> QWidget | None:
        return self._scroll.widget()

    def set_event(self, ev) -> None:
        """Show `ev` (swapping out the previous meeting's content, which is freed).
        Keyboard focus inside the old content moves to the same control in the new."""
        focus = QApplication.focusWidget()
        old = self.body()
        refocus = focus.objectName() if focus is not None and old is not None \
            and old.isAncestorOf(focus) else ""
        if old is not None:
            self._scroll.takeWidget()
            old.hide()
            old.deleteLater()
        self.event_shown = ev
        self._scroll.setWidget(self._build(ev))
        self._fix_tab_order()
        if refocus:
            w = self.body().findChild(QWidget, refocus)
            (w or self.first_control()).setFocus()

    def _build(self, ev) -> QWidget:
        popup = self._popup
        box = QWidget()
        box.setObjectName("details")
        box.setStyleSheet("QWidget#details{background:transparent;}")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(5)
        title = QLabel(ev.title or "")
        title.setObjectName("title")
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setWordWrap(True)
        title.setStyleSheet("font-weight:bold;font-size:13px;")
        lay.addWidget(title)
        when = QLabel(layout.format_range(ev, popup.now()))
        when.setObjectName("when")
        when.setTextFormat(Qt.TextFormat.PlainText)
        when.setWordWrap(True)
        lay.addWidget(when)
        cal = QLabel(f'<span style="color:{theme.effective_color(ev, popup.settings)};">'
                     f'&#9632;</span> {html.escape(ev.calendar_name or "")}')
        cal.setObjectName("calendar")
        cal.setTextFormat(Qt.TextFormat.RichText)
        cal.setWordWrap(True)
        lay.addWidget(cal)
        if ev.location:
            loc = QLabel(ev.location)
            loc.setObjectName("location")
            loc.setTextFormat(Qt.TextFormat.PlainText)
            loc.setWordWrap(True)
            loc.setStyleSheet(f"color:{theme.MUTED};")
            lay.addWidget(loc)
        description = html_to_text(ev.description or "")
        if description:
            desc = LinkText(description)
            desc.setObjectName("description")
            desc.setStyleSheet(f"QTextBrowser{{background:{theme.BG};color:{theme.TEXT};"
                               f"border:1px solid {theme.BORDER};font-size:11px;}}")
            desc.setFixedHeight(desc.fit_height(self.inner_width() - _scrollbar_w(),
                                                DESCRIPTION_LINES))
            desc.link_opened.connect(lambda _u: popup.hide())
            # Out of the Tab chain (Join -> Hide… -> Color… -> ✕); links still click.
            desc.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
            lay.addWidget(desc)
        url = getattr(ev, "meeting_url", None)
        if safe_http_url(url) is not None:
            join = QPushButton(meeting_label(url))
            join.setObjectName("bigjoin")
            join.setCursor(Qt.CursorShape.PointingHandCursor)
            join.setToolTip(plain_tip(url))
            join.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            join.setStyleSheet(
                f"QPushButton{{background:{theme.LINK};color:#0b0d10;font-weight:bold;"
                "padding:5px 10px;border:none;border-radius:4px;}"
                "QPushButton:hover{background:#a9c8ff;}"
                "QPushButton:focus{border:2px solid #f1f3f4;}")
            join.clicked.connect(lambda _c=False, u=url: popup._join(u))
            # Never wider than the flyout, whatever the label's font metrics.
            join.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            lay.addWidget(join)
        actions = QHBoxLayout()
        actions.setSpacing(6)
        style = (f"QPushButton{{background:{theme.BORDER};color:{theme.TEXT};font-size:11px;"
                 "padding:3px 8px;border:none;border-radius:3px;}"
                 "QPushButton:hover{background:#4a4d51;}"
                 f"QPushButton:focus{{border:1px solid {theme.LINK};}}")
        hide_btn = QPushButton("Hide…")
        hide_btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        hide_btn.setObjectName("hide")
        hide_btn.setToolTip("Hide in SmartCal only (Google is not changed)")
        hide_btn.setStyleSheet(style)
        hide_btn.clicked.connect(lambda _c=False, e=ev, b=hide_btn: popup.show_hide_menu(e, b))
        color_btn = QPushButton(meeting_menus.color_button_text(ev))
        color_btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        color_btn.setObjectName("color")
        color_btn.setToolTip("Color in SmartCal only (Google is not changed)")
        color_btn.setStyleSheet(style)
        color_btn.clicked.connect(
            lambda _c=False, e=ev, b=color_btn: popup.show_color_menu(e, b))
        actions.addWidget(hide_btn)
        actions.addWidget(color_btn)
        actions.addStretch(1)
        lay.addLayout(actions)
        lay.addStretch(1)
        return box

    # -- focus ---------------------------------------------------------------
    def tab_chain(self) -> list[QWidget]:
        body = self.body()
        found = [body.findChild(QPushButton, n) if body is not None else None
                 for n in TAB_CHAIN[:-1]]
        return [w for w in found if w is not None] + [self.close_button]

    def first_control(self) -> QWidget:
        return self.tab_chain()[0]

    def _fix_tab_order(self) -> None:
        chain = self.tab_chain()
        for a, b in zip(chain, chain[1:]):
            QWidget.setTabOrder(a, b)
        if len(chain) > 1:
            QWidget.setTabOrder(chain[-1], chain[0])   # wraps inside the flyout

    def focus_first(self) -> None:
        self.first_control().setFocus(Qt.FocusReason.TabFocusReason)

    def focusNextPrevChild(self, forward: bool) -> bool:
        """Tab / Shift+Tab cycle through Join, Hide…, Color… and ✕ only."""
        chain = self.tab_chain()
        focus = QApplication.focusWidget()
        if focus in chain:
            i = chain.index(focus) + (1 if forward else -1)
            chain[i % len(chain)].setFocus(Qt.FocusReason.TabFocusReason if forward
                                           else Qt.FocusReason.BacktabFocusReason)
            return True
        self.focus_first()
        return True

    # -- size ------------------------------------------------------------------
    def inner_width(self) -> int:
        return WIDTH - 2 * MARGINS

    def fit_to(self, area: QRect) -> None:
        """Height: the whole content, at most the work area's height (then it scrolls)."""
        lay = self.layout()
        body = self.body()
        self._scroll.setFixedHeight(0)
        lay.activate()
        chrome = lay.sizeHint().height()
        content_h = 0
        if body is not None:
            bl = body.layout()
            bl.activate()
            w = self.inner_width() - _scrollbar_w()
            content_h = bl.totalHeightForWidth(w) if bl.hasHeightForWidth() \
                else bl.sizeHint().height()
        max_total = max(80, area.height() - 2 * placement.MARGIN)
        self._scroll.setFixedHeight(max(20, min(content_h, max_total - chrome)))
        lay.activate()
        self.resize(WIDTH, min(lay.sizeHint().height(), max_total))

    # -- events: the popup decides ---------------------------------------------
    def setVisible(self, visible: bool) -> None:
        # Hiding the active flyout deactivates it synchronously, while it still counts
        # as visible: that is the flyout closing, not focus leaving the group.
        self._hiding = not visible
        try:
            super().setVisible(visible)
        finally:
            self._hiding = False

    def event(self, e):
        if e.type() == QEvent.Type.WindowDeactivate and self.isVisible()                 and not getattr(self, "_hiding", False):
            self._popup._group_deactivated(self)
        return super().event(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self._popup.handle_escape()
            e.accept()
            return
        super().keyPressEvent(e)

    def closeEvent(self, e):
        # Alt+F4 or the like: same as the ✕ (the popup clears the selection).
        e.ignore()
        self._popup.close_flyout()
