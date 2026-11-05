"""The meeting details panel that slides out beside the tray popup.

One instance per popup, built on the first selection and reused: selecting another
meeting swaps its content in place. It is a separate frameless tool window with the
popup's size (same width and height, top aligned with it), but the popup treats the two
as one group for focus and lifetime (see AgendaPopup); the popup owns its menus, its
placement and every decision about closing it.

It slides out from behind the popup's edge and back. On Windows an owned tool window is
always above its owner, so "behind" is drawn with a window mask: while the panel
overlaps the popup, the overlapping part is masked away."""
from __future__ import annotations

import html

from PyQt6.QtCore import (QEasingCurve, QEvent, QPoint, QPropertyAnimation, QRect, QSize, Qt,
                          QTimer, pyqtProperty)
from PyQt6.QtGui import QColor, QPalette, QRegion
from PyQt6.QtWidgets import (QApplication, QBoxLayout, QFrame, QLabel, QPushButton,
                             QScrollArea, QSizePolicy, QStyle, QTextBrowser, QVBoxLayout,
                             QWidget)

from ..textutil import html_to_text, meeting_label, safe_http_url
from . import layout, meeting_menus, motion, theme
from .linktext import LinkText, plain_tip
from .popup import WIDTH   # one source of truth: the panel is as wide as the popup

MARGINS = 10
DESCRIPTION_MIN_LINES = 3       # the description fills the rest of the panel; at least this
HEADER_TEXT = "Meeting details"
# Tab order inside the panel (whichever exist for the meeting).
TAB_CHAIN = ("bigjoin", "hide", "color")


def _set_scroll(box, value: int) -> None:
    try:
        box.verticalScrollBar().setValue(value)
    except RuntimeError:                # the box was replaced meanwhile
        pass


def _scrollbar_w() -> int:
    style = QApplication.style()
    return style.pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent) if style else 12



class DetailsFlyout(QWidget):
    """Frameless tool window with one meeting's details. `popup` is the AgendaPopup:
    it opens the menus, joins, hides the group, places the panel and decides what Esc
    does."""

    def __init__(self, popup):
        super().__init__(popup)
        self._popup = popup
        self._hiding = False
        self._closing = False           # sliding back; hidden when that finishes
        self._docked = QPoint()
        self._tucked = QPoint()
        self._behind: QRect | None = None   # the popup's rect while sliding behind it
        self._masked = False
        self.event_shown = None
        self._shown_key = None
        self.builds = 0                 # content builds so far (tests: no needless rebuilds)
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint)
        # A mouse selection keeps the popup active; the keyboard path activates explicitly.
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        # Opaque, painted edge to edge in the popup's colors (never an unpainted area).
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor(theme.BG))
        self.setPalette(pal)
        self.setObjectName("flyout")
        self.setStyleSheet(
            f"#flyout{{background:{theme.BG};border:1px solid {theme.BORDER};}}"
            f"QLabel{{color:{theme.TEXT};font-size:12px;}}")
        w = WIDTH
        self.setFixedSize(w, max(1, popup.height()))
        outer = QVBoxLayout(self)
        outer.setContentsMargins(MARGINS, 8, MARGINS, MARGINS)
        outer.setSpacing(6)
        self._header = QLabel(HEADER_TEXT)
        self._header.setObjectName("header")
        self._header.setTextFormat(Qt.TextFormat.PlainText)
        self._header.setStyleSheet(f"color:{theme.MUTED};font-size:11px;")
        outer.addWidget(self._header)
        self._scroll = QScrollArea()
        self._scroll.setObjectName("flyoutscroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet("QScrollArea{background:transparent;}")
        self._scroll.viewport().setAutoFillBackground(False)
        outer.addWidget(self._scroll, 1)
        # The slide: the window position, animated (see _set_slide_pos).
        self._anim = QPropertyAnimation(self, b"slide_pos", self)
        self._anim.finished.connect(self._slide_finished)

    # -- content -------------------------------------------------------------
    def body(self) -> QWidget | None:
        return self._scroll.widget()

    def content_key(self, ev) -> tuple:
        """Everything the details show. Same key: the shown content is still right."""
        return ((ev.account, ev.calendar_id, ev.id), ev.title, ev.start, ev.end,
                ev.all_day, ev.calendar_name, ev.location, ev.description,
                getattr(ev, "meeting_url", None),
                theme.effective_color(ev, self._popup.settings),
                meeting_menus.color_button_text(ev), bool(getattr(ev, "declined", False)))

    def set_event(self, ev) -> bool:
        """Show `ev`. If its content is what is shown already, nothing is rebuilt (a
        refresh keeps scroll position, selected text and a press in progress). Else the
        previous content is swapped out and freed; keyboard focus inside it moves to the
        same control in the new content, and the description keeps its scroll position
        when it is the same meeting. Returns True if the content was rebuilt."""
        key = self.content_key(ev)
        self.event_shown = ev
        if key == self._shown_key and self.body() is not None:
            return False
        same_meeting = self._shown_key is not None and self._shown_key[0] == key[0]
        self._shown_key = key
        focus = QApplication.focusWidget()
        old = self.body()
        refocus = focus.objectName() if focus is not None and old is not None \
            and old.isAncestorOf(focus) else ""
        scroll = None
        if old is not None:
            box = old.findChild(QTextBrowser, "description")
            if same_meeting and box is not None:
                scroll = box.verticalScrollBar().value()
            self._scroll.takeWidget()
            old.hide()
            old.deleteLater()
        self.builds += 1
        self._scroll.setWidget(self._build(ev))
        self._fix_tab_order()
        if refocus:
            w = self.body().findChild(QWidget, refocus)
            (w or self.first_control()).setFocus()
        if scroll:
            self._restore_description_scroll(scroll)
        return True

    def description_box(self):
        body = self.body()
        return body.findChild(QTextBrowser, "description") if body is not None else None

    def _restore_description_scroll(self, value: int) -> None:
        box = self.description_box()
        if box is None:
            return
        bar = box.verticalScrollBar()
        bar.setValue(value)
        if bar.value() != value:        # the range is set once the box is laid out
            QTimer.singleShot(0, lambda b=box, v=value: _set_scroll(b, v))

    def _label(self, text: str, name: str, fmt=Qt.TextFormat.PlainText) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName(name)
        lab.setTextFormat(fmt)
        lab.setWordWrap(True)
        # Never wider than the panel, whatever the text (wraps; a giant word is cut).
        lab.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        return lab

    def _build(self, ev) -> QWidget:
        popup = self._popup
        width = self.inner_width() - _scrollbar_w()
        box = QWidget()
        box.setObjectName("details")
        box.setStyleSheet("QWidget#details{background:transparent;}")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(5)
        title = self._label(ev.title or "", "title")
        title.setStyleSheet("font-weight:bold;font-size:13px;")
        lay.addWidget(title)
        lay.addWidget(self._label(layout.format_range(ev, popup.now()), "when"))
        lay.addWidget(self._label(
            f'<span style="color:{theme.effective_color(ev, popup.settings)};">'
            f'&#9632;</span> {html.escape(ev.calendar_name or "")}', "calendar",
            Qt.TextFormat.RichText))
        if ev.location:
            loc = self._label(ev.location, "location")
            loc.setStyleSheet(f"color:{theme.MUTED};")
            lay.addWidget(loc)
        description = html_to_text(ev.description or "")
        has_desc = bool(description)
        if has_desc:
            desc = LinkText(description)
            desc.setObjectName("description")
            desc.setStyleSheet(f"QTextBrowser{{background:{theme.BG};color:{theme.TEXT};"
                               f"border:1px solid {theme.BORDER};font-size:11px;}}")
            # Fills the rest of the panel (up to its whole text), scrolls beyond that.
            full = desc.fit_height(width)
            desc.setMinimumHeight(min(full, desc.fit_height(width, DESCRIPTION_MIN_LINES)))
            desc.setMaximumHeight(full)
            desc.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
            desc.link_opened.connect(lambda _u: popup.hide())
            # Out of the Tab chain (Join -> Hide… -> Color…); links still click.
            desc.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
            lay.addWidget(desc, 1)
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
            # Full width, never wider than the panel, whatever the label's font metrics.
            join.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            lay.addWidget(join)
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
        spacing = 6
        side_by_side = (hide_btn.sizeHint().width() + spacing
                        + color_btn.sizeHint().width()) <= width
        actions = QBoxLayout(QBoxLayout.Direction.LeftToRight if side_by_side
                             else QBoxLayout.Direction.TopToBottom)
        actions.setObjectName("actions")
        actions.setSpacing(spacing)
        actions.addWidget(hide_btn)
        actions.addWidget(color_btn)
        if side_by_side:
            actions.addStretch(1)
        else:                           # stacked: each one as wide as the panel
            for b in (hide_btn, color_btn):
                b.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        lay.addLayout(actions)
        lay.addStretch(1)               # spare height stays below the buttons
        return box

    # -- focus ---------------------------------------------------------------
    def tab_chain(self) -> list[QWidget]:
        body = self.body()
        found = [body.findChild(QPushButton, n) if body is not None else None
                 for n in TAB_CHAIN]
        return [w for w in found if w is not None]

    def first_control(self) -> QWidget:
        chain = self.tab_chain()
        return chain[0] if chain else self

    def _fix_tab_order(self) -> None:
        chain = self.tab_chain()
        for a, b in zip(chain, chain[1:]):
            QWidget.setTabOrder(a, b)
        if len(chain) > 1:
            QWidget.setTabOrder(chain[-1], chain[0])   # wraps inside the panel

    def focus_first(self) -> None:
        self.first_control().setFocus(Qt.FocusReason.TabFocusReason)

    def focusNextPrevChild(self, forward: bool) -> bool:
        """Tab / Shift+Tab cycle through Join, Hide… and Color… only."""
        chain = self.tab_chain()
        focus = QApplication.focusWidget()
        if chain and focus in chain:
            i = chain.index(focus) + (1 if forward else -1)
            chain[i % len(chain)].setFocus(Qt.FocusReason.TabFocusReason if forward
                                           else Qt.FocusReason.BacktabFocusReason)
            return True
        self.focus_first()
        return True

    # -- size and place (the popup decides both) ---------------------------------
    def inner_width(self) -> int:
        return WIDTH - 2 * MARGINS

    def docked_pos(self) -> QPoint:
        return QPoint(self._docked)

    def tucked_pos(self) -> QPoint:
        return QPoint(self._tucked)

    def place(self, size: QSize, docked: QPoint, tucked: QPoint,
              behind: QRect | None) -> None:
        """Size the panel and set where it docks (`docked`) and where it hides behind
        the popup (`tucked`); `behind` is the popup's rect it slides behind (None: no
        slide, e.g. over the popup on a narrow screen). While a slide runs, the slide
        owns the position: only its end moves."""
        if self.size() != size:
            self.setFixedSize(size)
        self._docked, self._tucked = QPoint(docked), QPoint(tucked)
        self._behind = QRect(behind) if behind is not None else None
        if self.sliding():
            self._anim.setEndValue(self._tucked if self._closing else self._docked)
        elif not self.isHidden() and not self._closing and self.pos() != self._docked:
            self._move_to(self._docked)
        self._update_mask()

    # -- the slide ---------------------------------------------------------------
    def sliding(self) -> bool:
        return self._anim.state() == QPropertyAnimation.State.Running

    def closing(self) -> bool:
        """Sliding back: still visible, already closed for the popup."""
        return self._closing

    def slide_open(self, ms: int) -> None:
        """Show (tucked behind the popup) and slide out to the docked position; while
        sliding back, turn around from where it is. `ms` 0: dock at once."""
        self._closing = False
        if self.isHidden():
            self._anim.stop()
            # Instant (animations off): straight to the dock, never masked.
            self._move_to(self._tucked if ms > 0 else self._docked)
            self.show()                 # WA_ShowWithoutActivating: the popup keeps focus
            start = QPoint(self._tucked)
        else:
            start = self.pos()
        if not self._run(start, self._docked, ms, QEasingCurve.Type.OutCubic):
            self._move_to(self._docked)
            self._update_mask()

    def slide_close(self, ms: int) -> None:
        """Slide back behind the popup, then hide. `ms` 0: hide at once."""
        self._closing = True
        if not self._run(self.pos(), self._tucked, ms, QEasingCurve.Type.InCubic):
            self._finish_close(notify=False)

    def hide_now(self) -> None:
        """Hidden at once, no slide (the popup went away)."""
        self._anim.stop()
        self._closing = False
        self._clear_mask()
        self.hide()

    def _run(self, start: QPoint, end: QPoint, ms: int, curve) -> bool:
        """Animate start -> end; the time is `ms` scaled by the share of the full slide
        left to go (a turnaround midway takes part of it). False: nothing to animate."""
        self._anim.stop()
        span = abs(self._docked.x() - self._tucked.x()) + abs(self._docked.y() - self._tucked.y())
        left = abs(end.x() - start.x()) + abs(end.y() - start.y())
        if span > 0:
            ms = int(round(ms * min(1.0, left / span)))
        if ms <= 0 or left == 0:
            return False
        self._anim.setDuration(ms)
        self._anim.setStartValue(QPoint(start))
        self._anim.setEndValue(QPoint(end))
        self._anim.setEasingCurve(QEasingCurve(curve))
        self._anim.start()
        return True

    def _slide_finished(self) -> None:
        if self._closing:
            self._finish_close(notify=True)
        else:
            self._update_mask()         # docked: nothing to mask

    def _finish_close(self, notify: bool) -> None:
        was_active = self._popup.active_window() is self
        self._anim.stop()
        self._closing = False
        self._clear_mask()
        self.hide()
        if notify:
            self._popup._flyout_slid_back(was_active)

    def _get_slide_pos(self) -> QPoint:
        return self.pos()

    def _set_slide_pos(self, pos: QPoint) -> None:
        self._move_to(pos)

    slide_pos = pyqtProperty(QPoint, fget=_get_slide_pos, fset=_set_slide_pos)

    def _visible_region(self, pos: QPoint) -> QRegion | None:
        """The part of the panel at `pos` that is not behind the popup (local coords);
        None when nothing is behind it."""
        if self._behind is None:
            return None
        here = QRect(pos, self.size())
        overlap = here.intersected(self._behind)
        if overlap.isEmpty():
            return None
        return QRegion(self.rect()).subtracted(QRegion(overlap.translated(-pos)))

    def _hidden_area(self, pos: QPoint) -> int:
        """How much of the panel at `pos` is behind the popup (pixels)."""
        if self._behind is None:
            return 0
        overlap = QRect(pos, self.size()).intersected(self._behind)
        return 0 if overlap.isEmpty() else overlap.width() * overlap.height()

    def _move_to(self, pos: QPoint) -> None:
        """Move, keeping the part behind the popup masked away in every frame: a growing
        visible part is unmasked after the move, a shrinking one masked before it."""
        if pos == self.pos():
            self._update_mask()
            return
        new = self._visible_region(pos)
        if self._hidden_area(pos) > self._hidden_area(self.pos()):
            self._apply_mask(new)
            self.move(pos)
        else:
            self.move(pos)
            self._apply_mask(new)

    def _update_mask(self) -> None:
        self._apply_mask(self._visible_region(self.pos()))

    def _apply_mask(self, region: QRegion | None) -> None:
        if region is None:
            self._clear_mask()
            return
        if region.isEmpty():
            # Fully behind the popup. (An empty mask would mean "no mask".) One pixel on
            # the popup's own border, in the same border color: nothing shows.
            region = QRegion(0, 0, 1, 1)
        self.setMask(region)
        self._masked = True

    def _clear_mask(self) -> None:
        if self._masked:
            self.clearMask()
            self._masked = False

    # -- events: the popup decides ---------------------------------------------
    def setVisible(self, visible: bool) -> None:
        # Hiding the active panel deactivates it synchronously, while it still counts
        # as visible: that is the panel closing, not focus leaving the group.
        self._hiding = not visible
        try:
            super().setVisible(visible)
        finally:
            self._hiding = False

    def hideEvent(self, e):
        self._anim.stop()               # never keeps moving a hidden window
        self._closing = False
        super().hideEvent(e)

    def event(self, e):
        if (e.type() == QEvent.Type.WindowDeactivate and self.isVisible()
                and not self._hiding):
            self._popup._group_deactivated(self)
        elif e.type() == QEvent.Type.WindowActivate:
            self._popup.note_foreground()
        return super().event(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self._popup.handle_escape()
            e.accept()
            return
        super().keyPressEvent(e)

    def closeEvent(self, e):
        # Alt+F4 or the like: same as clicking the meeting again (the popup clears the
        # selection and the panel slides back).
        e.ignore()
        self._popup.close_flyout()
