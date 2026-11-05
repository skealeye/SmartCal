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

import math
import os

from PyQt6.QtCore import (QEvent, QPoint, QPropertyAnimation, QRect, QSize, Qt,
                          QTimer, pyqtProperty)
from PyQt6.QtGui import QColor, QPalette, QRegion
from PyQt6.QtWidgets import (QApplication, QBoxLayout, QFrame, QLabel, QPushButton,
                             QScrollArea, QSizePolicy, QStyle, QTextBrowser, QVBoxLayout,
                             QWidget)

from ..textutil import calendar_label, html_to_text, meeting_label, safe_http_url
from . import layout, motion, nativebg, theme
from .widgets import HoverButton
from .linktext import LinkText, plain_tip
from .popup import WIDTH   # one source of truth: the panel is as wide as the popup

# True: the panel slides out from behind the popup, the overlapping part hidden by a
# window mask. Set it False to fall back to a fade-only open/close (no overlap and no
# mask: the panel sits docked next to the popup and only fades, same durations) if the
# mask flickers on some display. SMARTCAL_SLIDE_MODE=fade picks that without a rebuild.
SLIDE_USES_MASK = True
SLIDE_MODE_ENV = "SMARTCAL_SLIDE_MODE"

MARGINS = 10
DESCRIPTION_MIN_LINES = 3       # the description fills the rest of the panel; at least this
HEADER_TEXT = "Meeting details"
# Tab order inside the panel (whichever exist for the meeting).
TAB_CHAIN = ("bigjoin",)
MIN_HEIGHT = 96                 # the panel is never shorter than this


def _set_scroll(box, value: int) -> None:
    try:
        box.verticalScrollBar().setValue(value)
    except RuntimeError:                # the box was replaced meanwhile
        pass


def _scrollbar_w() -> int:
    style = QApplication.style()
    return style.pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent) if style else 12


def slide_mode() -> str:
    """"slide": out from behind the popup, the overlap hidden by a window mask.
    "fade": docked beside the popup from the start, only fading in and out (same
    durations); no mask. SLIDE_USES_MASK picks the default; SMARTCAL_SLIDE_MODE=fade
    (or =slide) overrides it without a rebuild."""
    env = os.environ.get(SLIDE_MODE_ENV, "").strip().lower()
    if env in ("fade", "slide"):
        return env
    return "slide" if SLIDE_USES_MASK else "fade"


def px_before_edge(x: int, edge: int, dpr: float) -> int:
    """Logical pixels of a window at `x` that lie left of `edge`, rounded down in device
    pixels (never reaching the edge at fractional scaling)."""
    return max(0, math.floor((round(edge * dpr) - round(x * dpr)) / dpr))


def px_until_edge(x: int, edge: int, dpr: float) -> int:
    """Logical pixels from a window's left at `x` up to `edge`, rounded up in device
    pixels (whatever is kept right of it never reaches back over the edge)."""
    return max(0, math.ceil((round(edge * dpr) - round(x * dpr)) / dpr))


class _NotesBox(LinkText):
    """The description box: prefers its minimum height (it grows into whatever room the
    panel has left, through its Expanding policy), so a short panel scrolls the body
    instead of stretching the box to QTextBrowser's default height."""

    def sizeHint(self) -> QSize:
        return QSize(super().sizeHint().width(), self.minimumHeight())


def _set_opacity_fx(fx, value) -> None:
    try:
        fx.setOpacity(float(value))
    except RuntimeError:                # its widget went away mid-fade
        pass


def _area(region: QRegion | None, full: QRect) -> int:
    if region is not None and _shows_nothing(region):
        return 0
    r = region.boundingRect() if region is not None else full
    return r.width() * r.height()


# v0.8.4: the window mask of a panel fully behind the popup. An empty QRegion would mean
# "no mask" (all of it shows), so this is one pixel just outside the window: nothing of
# the window lies inside it, so 0 px show. The window stays fully opaque all along (until
# v0.8.3 the panel went transparent here instead, and turning opaque again as it came
# out made Windows drop and repaint the layered window: a white frame).
NOTHING_SHOWN = QRegion(-1, -1, 1, 1)


def _shows_nothing(region: QRegion) -> bool:
    return region.isEmpty() or region == NOTHING_SHOWN



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
        self.setStyleSheet(theme.window_style("flyout"))   # the popup's own look
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
        # A thin bar in the meeting's effective color under the header.
        self._color_bar = QFrame()
        self._color_bar.setObjectName("colorbar")
        self._color_bar.setFixedHeight(3)
        outer.addWidget(self._color_bar)
        self._ghost = None              # the old content fading out during a swap
        self._ghost_anim = self._fade_in_anim = self._height_anim = None
        self._height_target: int | None = None
        self._content_h = None          # (content key, measured height)
        self._scroll = QScrollArea()
        self._scroll.setObjectName("flyoutscroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet("QScrollArea{background:transparent;}")
        self._scroll.viewport().setAutoFillBackground(False)
        outer.addWidget(self._scroll, 1)
        self._mode = slide_mode()
        self._side = "left"
        # The slide: the window position, animated (see _set_slide_pos). Fade-only
        # mode animates the window opacity instead.
        self._anim = QPropertyAnimation(self, b"slide_pos", self)
        self._anim.finished.connect(self._slide_finished)
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.finished.connect(self._slide_finished)

    # -- content -------------------------------------------------------------
    def body(self) -> QWidget | None:
        return self._scroll.widget()

    def content_key(self, ev) -> tuple:
        """Everything the details show. Same key: the shown content is still right."""
        return ((ev.account, ev.calendar_id, ev.id), ev.title, ev.start, ev.end,
                ev.all_day, ev.calendar_name, ev.location, ev.description,
                getattr(ev, "meeting_url", None),
                theme.effective_color(ev, self._popup.settings),
                bool(getattr(ev, "declined", False)))

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
        # Visible and open: the old content cross-fades into the new (a snapshot of it
        # fades out on top), with the micro-animations on.
        crossfade = (old is not None and self.isVisible() and not self.closing()
                     and motion.micro_enabled())
        # A swap during a swap: the previous fade ends first, so the snapshot is of
        # the fully shown content, never a half-transparent frame.
        self._end_crossfade()
        ghost = self._grab(old) if crossfade else None
        ghost_rect = QRect(old.pos(), old.size()) if crossfade else None
        if old is not None:
            box = old.findChild(QTextBrowser, "description")
            if same_meeting and box is not None:
                scroll = box.verticalScrollBar().value()
            self._scroll.takeWidget()
            old.hide()
            old.deleteLater()
        self.builds += 1
        self._color_bar.setStyleSheet(
            f"background:{theme.effective_color(ev, self._popup.settings)};"
            "border-radius:1px;")
        self._scroll.setWidget(self._build(ev))
        self._fix_tab_order()
        if ghost is not None:
            self._crossfade(ghost, ghost_rect)
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
        cal_name = calendar_label(ev.calendar_name)
        if cal_name:                    # an email as the name: no calendar line at all
            cal = self._label(cal_name, "calendar")
            cal.setStyleSheet(f"color:{theme.MUTED};")
            lay.addWidget(cal)
        if ev.location:
            loc = self._label(ev.location, "location")
            loc.setStyleSheet(f"color:{theme.MUTED};")
            lay.addWidget(loc)
        description = html_to_text(ev.description or "")
        has_desc = bool(description)
        if has_desc:
            desc = _NotesBox(description)
            desc.setObjectName("description")
            desc.setStyleSheet(f"QTextBrowser{{background:{theme.BG};color:{theme.TEXT};"
                               f"border:1px solid {theme.BORDER};font-size:11px;}}")
            # Fills the rest of the panel down to the buttons and scrolls beyond that; no
            # height cap. Measured after the style sheet (font, border) is applied.
            desc.ensurePolished()
            desc.setMinimumHeight(min(desc.fit_height(width),
                                      desc.fit_height(width, DESCRIPTION_MIN_LINES)))
            desc.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
            desc.link_opened.connect(lambda _u: popup.hide())
            # Out of the Tab chain (Join is the only stop); links still click.
            desc.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
            lay.addWidget(desc, 1)
        url = getattr(ev, "meeting_url", None)
        if safe_http_url(url) is not None:
            join = HoverButton(meeting_label(url), theme.LINK, "#a9c8ff", "#0b0d10",
                               extra="font-weight:bold;padding:5px 10px;border-radius:4px;",
                               focus="border:2px solid #f1f3f4;")
            join.setObjectName("bigjoin")
            join.setCursor(Qt.CursorShape.PointingHandCursor)
            join.setToolTip(plain_tip(url))
            join.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            join.clicked.connect(lambda _c=False, u=url: popup._join(u))
            # Full width, never wider than the panel, whatever the label's font metrics.
            join.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            lay.addWidget(join)
        # v0.8.0: no Hide…/Color… here (right-click the meeting instead). The panel's
        # height fits its content (content_height); without notes, any spare height
        # (the MIN_HEIGHT floor, a shrink in progress) stays below, never between the
        # lines (a QLabel given extra height centers its text).
        if not has_desc:
            lay.addStretch(1)
        return box

    # -- fitted height ---------------------------------------------------------
    def content_height(self) -> int:
        """The panel height that shows everything with no blank band: margins, header,
        color bar and the body at its natural height (the description fully wrapped).
        The popup caps it at its own height (the description then scrolls). Measured
        once per content (the popup asks on every move/resize frame)."""
        screen = self.screen()
        key = (self._shown_key, self.builds, self.devicePixelRatioF(),
               screen.name() if screen is not None else "")   # a new screen: re-measure
        if self._content_h is not None and self._content_h[0] == key:
            return self._content_h[1]
        h = self._measure_content_height()
        self._content_h = (key, h)
        return h

    def _measure_content_height(self) -> int:
        outer = self.layout()
        m = outer.contentsMargins()
        h = (m.top() + m.bottom() + self._header.sizeHint().height()
             + self._color_bar.height() + 2 * outer.spacing())
        body = self.body()
        if body is None or body.layout() is None:
            return max(MIN_HEIGHT, h)
        width = self.inner_width()
        lay = body.layout()
        lay.activate()
        body_h = (lay.totalHeightForWidth(width) if lay.hasHeightForWidth()
                  else lay.sizeHint().height())
        box = self.description_box()
        if box is not None:
            body_h += max(0, box.fit_height(width) - box.minimumHeight())
        return max(MIN_HEIGHT, h + body_h)

    def _apply_size(self, size: QSize, animate: bool = True) -> QSize | None:
        """The size the panel takes now (returned; None: unchanged, or its own height
        tween owns the height). A height change of the open, visible panel tweens
        (motion.SETTLE, motion.NORMAL) with the micro-animations on; else it is returned
        for place() to apply together with the position, in one geometry change."""
        target_h = size.height()
        width = size.width()
        if self._height_target == target_h and (self.height() == target_h
                                                or self.height_animating()):
            return QSize(width, self.height()) if self.width() != width else None
        self._stop_height_anim()
        self._height_target = target_h
        ms = motion.micro_duration(motion.NORMAL) \
            if animate and self.isVisible() and not self._closing else 0
        if not ms or self.height() == target_h:
            now = QSize(width, target_h)
            return now if now != self.size() else None
        if self.width() != width:
            self.setFixedSize(width, self.height())
        if self.body() is not None and self.content_height() <= target_h:
            # The content fits at the end: no scrollbar flashing in between.
            self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._height_anim = motion.tween(self, self.height(), target_h, ms,
                                         self._resize_height, self._height_anim_done,
                                         kind=motion.SETTLE)
        return None

    def _set_geometry(self, rect: QRect) -> None:
        """Size and position in ONE native change (a resize then a move made the panel
        poke below the popup for a frame while it followed the popup's own tween). The
        fixed-size limits are widened/narrowed in the order that never resizes the
        window by themselves; newly exposed pixels are painted in the same frame."""
        if rect == self.geometry():
            self._update_mask()
            return
        size = rect.size()
        grow = size.height() > self.height() or size.width() > self.width()
        big = QSize(max(size.width(), self.width()), max(size.height(), self.height()))
        small = QSize(min(size.width(), self.width()), min(size.height(), self.height()))
        self.setMaximumSize(big)                # widen first: no resize from the limits
        self.setMinimumSize(small)
        old = self._visible_region(self.pos())
        new = self._visible_region(rect.topLeft())
        shrinking = new is not None and old is not None \
            and _area(new, self.rect()) < _area(old, self.rect())
        if shrinking:
            self._apply_mask(new)       # shrinking visible part: mask before moving
        self.setGeometry(rect)
        self.setMinimumSize(size)               # ...then fixed at the new size
        self.setMaximumSize(size)
        self._update_mask()
        if grow and self.isVisible():
            self.repaint()

    def _height_anim_done(self) -> None:
        self._height_anim = None
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

    def _stop_height_anim(self) -> None:
        motion.stop(self._height_anim)
        self._height_anim = None
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

    def target_height(self) -> int:
        return self._height_target if self._height_target is not None else self.height()

    def height_animating(self) -> bool:
        return motion.alive(self._height_anim)

    def _resize_height(self, h: int) -> None:
        # The settle overshoot never makes it taller than the popup (it would poke out
        # below it); the last frame is exactly the target.
        cap = max(self._height_target or 1, self._popup.height())
        h = int(motion.clamp(int(h), 1, cap))
        if self.height() != h:
            self.setFixedSize(self.width(), h)
            self._update_mask()

    def _grab(self, widget):
        """The snapshot for a cross-fade (a seam for tests: e.g. a 1.5x pixmap)."""
        return widget.grab()

    def _crossfade(self, ghost, rect: QRect) -> None:
        """The old content (a snapshot) fades out over the new one, which fades in. The
        snapshot sits exactly where the old content was (`rect`, logical pixels; the
        pixmap may be 1.25x/1.5x in device pixels), top-left aligned."""
        from PyQt6.QtWidgets import QGraphicsOpacityEffect
        self._end_crossfade()
        view = self._scroll.viewport()
        label = QLabel(view)
        label.setObjectName("ghost")
        label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        label.setPixmap(ghost)
        label.setGeometry(rect)
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        out_fx = QGraphicsOpacityEffect(label)
        out_fx.setOpacity(1.0)
        label.setGraphicsEffect(out_fx)
        label.show()
        label.raise_()
        self._ghost = label
        body = self.body()
        in_fx = QGraphicsOpacityEffect(body)
        in_fx.setOpacity(0.0)
        body.setGraphicsEffect(in_fx)
        ms = motion.micro_duration(motion.CROSSFADE_MS)
        self._ghost_anim = motion.tween(self, 1.0, 0.0, ms,
                                        lambda v, fx=out_fx: _set_opacity_fx(fx, v),
                                        kind=motion.FADE)
        self._fade_in_anim = motion.tween(self, 0.0, 1.0, ms,
                                          lambda v, fx=in_fx: _set_opacity_fx(fx, v),
                                          self._end_crossfade, kind=motion.FADE)

    def crossfading(self) -> bool:
        return self._ghost is not None

    def _end_crossfade(self) -> None:
        motion.stop(self._ghost_anim)
        motion.stop(self._fade_in_anim)
        self._ghost_anim = self._fade_in_anim = None
        if self._ghost is not None:
            try:
                self._ghost.hide()
                self._ghost.deleteLater()
            except RuntimeError:
                pass
            self._ghost = None
        body = self.body()
        if body is not None and body.graphicsEffect() is not None:
            body.setGraphicsEffect(None)

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
        """Tab / Shift+Tab stay on the panel's controls (Join) only."""
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
              behind: QRect | None, animate: bool = True) -> None:
        """Size the panel and set where it docks (`docked`) and where it hides behind
        the popup (`tucked`); `behind` is the popup's rect it slides behind (None: no
        slide, e.g. over the popup on a narrow screen). While a slide runs, the slide
        owns the position: only its end moves. Fade-only mode: always docked."""
        now = self._apply_size(size, animate)
        if self.isHidden():
            self._mode = slide_mode()   # read per opening: no restart needed to switch
        if self._mode == "fade":
            tucked, behind = docked, None
        self._docked, self._tucked = QPoint(docked), QPoint(tucked)
        self._behind = QRect(behind) if behind is not None else None
        self._side = "right" if docked.x() > tucked.x() else "left"
        if self.isHidden():
            if now is not None:
                self.setFixedSize(now)
            return                      # nothing on screen: no move, no window region
        if self._anim.state() == QPropertyAnimation.State.Running:
            if now is not None:
                self.setFixedSize(now)  # the slide owns the position
            self._anim.setEndValue(self._tucked if self._closing else self._docked)
        elif self._mode == "fade" or not self._closing:
            # Docked: size and position together, once (e.g. following the popup's
            # height tween, where the popup's top moves every frame).
            self._set_geometry(QRect(self._docked, now if now is not None else self.size()))
            return
        elif now is not None:
            self.setFixedSize(now)
        self._update_mask()

    # -- the slide ---------------------------------------------------------------
    def sliding(self) -> bool:
        running = QPropertyAnimation.State.Running
        return self._anim.state() == running or self._fade.state() == running

    def closing(self) -> bool:
        """Sliding back: still visible, already closed for the popup."""
        return self._closing

    def mode(self) -> str:
        """"slide" (out from behind the popup, masked) or "fade" (docked, fades)."""
        return self._mode

    def slide_open(self, ms: int) -> None:
        """Show (tucked behind the popup) and slide out to the docked position; while
        sliding back, turn around from where it is. `ms` 0: dock at once. In fade-only
        mode: shown docked, fading in."""
        self._closing = False
        if self.isHidden():
            self._stop_animations()
            self._prepare_first_frame()
            if self._mode == "fade":
                self.move(self._docked)
                self._set_opacity(0.0 if ms > 0 else 1.0)
            else:
                # Instant (animations off): straight to the dock, never masked. Opaque
                # all along: never a layered window that turns opaque on screen.
                self._set_opacity(1.0)
                self.move(self._tucked if ms > 0 else self._docked)
                self._apply_mask(self._visible_region(self.pos()))   # before it shows
            self.show()                 # WA_ShowWithoutActivating: the popup keeps focus
        if not self._run(opening=True, ms=ms):
            self._move_to(self._docked)
            self._set_opacity(1.0)
            self._update_mask()

    def slide_close(self, ms: int) -> None:
        """Slide back behind the popup (fade-only: fade out), then hide. `ms` 0: hide at
        once."""
        self._closing = True
        if not self._run(opening=False, ms=ms):
            self._finish_close(notify=False)

    def hide_now(self) -> None:
        """Hidden at once, no slide (the popup went away)."""
        self._stop_animations()
        self._closing = False
        self.hide()                     # hidden first: unmasking never flashes it over
        self._clear_mask()              # the popup
        self._set_opacity(1.0)

    def _prepare_first_frame(self) -> None:
        """Before showing: styled and laid out, so its first paint has everything ready;
        the mask and position are set before show() too, so Qt applies them when it
        creates the native window, before Windows first shows it (v0.8.4)."""
        self.ensurePolished()
        lay = self.layout()
        if lay is not None:
            lay.activate()

    def nativeEvent(self, event_type, message):
        # Never the base class's nativeEvent: its (False, None) crashes Qt (see nativebg).
        return nativebg.native_event(event_type, message, theme.BG)

    def _stop_animations(self) -> None:
        self._anim.stop()
        self._fade.stop()

    def _run(self, opening: bool, ms: int) -> bool:
        """Animate toward docked (opening) or tucked; the time is `ms` scaled by the
        share of the way left to go (a turnaround midway takes part of it). False:
        nothing to animate."""
        self._stop_animations()
        # v0.8.4: the slide eases out with weight both ways and never passes its end
        # (GLIDE; overshooting would open a gap or poke out behind the popup); the
        # fade-only fallback's opacity eases out plainly (FADE).
        curve = motion.curve(motion.FADE if self._mode == "fade" else motion.GLIDE)
        if self._mode == "fade":
            start, end = self.windowOpacity(), (1.0 if opening else 0.0)
            ms = int(round(ms * min(1.0, abs(end - start))))
            if ms <= 0:
                return False
            anim = self._fade
        else:
            start = self.pos()
            end = QPoint(self._docked if opening else self._tucked)
            span = (abs(self._docked.x() - self._tucked.x())
                    + abs(self._docked.y() - self._tucked.y()))
            left = abs(end.x() - start.x()) + abs(end.y() - start.y())
            if span > 0:
                ms = int(round(ms * min(1.0, left / span)))
            if ms <= 0 or left == 0:
                return False
            anim = self._anim
        anim.setDuration(ms)
        anim.setStartValue(start)
        anim.setEndValue(end)
        anim.setEasingCurve(curve)
        anim.start()
        return True

    def _slide_finished(self) -> None:
        if self._closing:
            self._finish_close(notify=True)
        else:
            self._update_mask()         # docked: nothing to mask
            self._set_opacity(1.0)

    def _finish_close(self, notify: bool) -> None:
        was_active = self._popup.active_window() is self
        self._stop_animations()
        self._closing = False
        self.hide()                     # hidden first: unmasking never flashes it over
        self._clear_mask()              # the popup
        self._set_opacity(1.0)          # the next (maybe instant) open starts opaque
        if notify:
            self._popup._flyout_slid_back(was_active)

    def _set_opacity(self, value: float) -> None:
        if self.windowOpacity() != value:
            self.setWindowOpacity(value)

    def _get_slide_pos(self) -> QPoint:
        return self.pos()

    def _set_slide_pos(self, pos: QPoint) -> None:
        # Never past the dock nor past "tucked", whatever the curve (no gap beside the
        # popup, nothing poking out behind its far edge).
        self._move_to(motion.clamp_between(pos, self._docked, self._tucked))

    slide_pos = pyqtProperty(QPoint, fget=_get_slide_pos, fset=_set_slide_pos)

    def _visible_region(self, pos: QPoint) -> QRegion | None:
        """The part of the panel at `pos` that is not behind the popup (local coords),
        rounded in device pixels so it never reaches over the popup's edge at 125% or
        150% scaling; None when nothing is behind it."""
        if self._behind is None:
            return None
        if not QRect(pos, self.size()).intersects(self._behind):
            return None
        dpr = self.devicePixelRatioF() or 1.0
        w, h = self.width(), self.height()
        if self._side == "right":       # shows right of the popup's right edge
            hidden = min(w, px_until_edge(pos.x(), self._behind.right() + 1, dpr))
            return QRegion(hidden, 0, w - hidden, h) if hidden < w else QRegion()
        shown = min(w, px_before_edge(pos.x(), self._behind.left(), dpr))
        return QRegion(0, 0, shown, h) if shown > 0 else QRegion()

    def _move_to(self, pos: QPoint) -> None:
        """Move, keeping the part behind the popup masked away in every frame.
        Growing (sliding out): move first, with the old, smaller mask (its pixels at the
        new position are still clear of the popup), then grow the mask and paint the
        newly shown strip right away. Shrinking (sliding back): shrink the mask first,
        then move. Masking first when growing would, for one frame, show the panel over
        the popup by the step's width."""
        if pos == self.pos():
            self._update_mask()
            return
        old = self._visible_region(self.pos())
        new = self._visible_region(pos)
        if _area(new, self.rect()) < _area(old, self.rect()):
            self._apply_mask(new)
            self.move(pos)
        else:
            self.move(pos)
            self._apply_mask(new)
            exposed = (new if new is not None else QRegion(self.rect())).subtracted(
                old if old is not None else QRegion(self.rect()))
            if not exposed.isEmpty() and self.isVisible():
                self.repaint(exposed)   # painted in this frame, never a dark strip

    def _update_mask(self) -> None:
        if self.isHidden():
            return
        self._apply_mask(self._visible_region(self.pos()))

    def _apply_mask(self, region: QRegion | None) -> None:
        if region is None:
            self._clear_mask()
            return
        # Fully behind the popup: a mask outside the window (0 px show). The window
        # opacity is never touched here: it stays 1.0 (see NOTHING_SHOWN).
        self.setMask(NOTHING_SHOWN if region.isEmpty() else region)
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
        self._stop_animations()         # never keeps moving a hidden window
        self._stop_height_anim()        # ...nor resizing or fading one
        if self._height_target is not None and self.height() != self._height_target:
            self.setFixedSize(self.width(), self._height_target)
        self._end_crossfade()
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
