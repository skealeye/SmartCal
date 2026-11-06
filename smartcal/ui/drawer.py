"""v0.10.0: the Ask Claude drawer. The chat slides DOWN from the popup's top edge and
retracts back up (motion.DRAWER_*).

A CHILD widget of the popup, never a window of its own: it lives inside the opaque
popup window, is clipped to it (Windows 11 rounds that window; the fallback mask clips
it too) and paints the popup's frame lines where it covers them. No mask, no window
opacity, no translucent window: only the child moves (Qt composes it inside the popup's
own backing store, every frame complete).

It covers the agenda from the top down to just above the popup's "Hide chat" row (the
agenda does not move; nothing is laid out again per frame). Its geometry follows the
popup's height (place()); its position is one number, the eased openness e (0 closed,
1 open), so a height change or a reversal mid-way never jumps.

State: closed (hidden) -> opening -> open -> closing -> closed; open_drawer()/close_drawer() from any
state continue from where it is (rapid toggles never leave it half open: every slide
ends in one of the two final states, and snap() lands there at once)."""
from __future__ import annotations

from PyQt6 import sip
from PyQt6.QtCore import QRect, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QLinearGradient, QPainter
from PyQt6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
                             QWidget)

from . import cards, motion, theme, uitrace, winframe


class ChatDrawer(QWidget):
    closed = pyqtSignal()            # fully retracted (hidden)
    close_requested = pyqtSignal()   # its own close button

    def __init__(self, popup, chat):
        super().__init__(popup)
        self._popup = popup
        self.chat = chat
        self.setObjectName("chatdrawer")
        self.hide()
        pad, shadow = motion.DRAWER_PAD_PX, motion.DRAWER_SHADOW_PX
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, pad + 10, 10, 8 + shadow)
        lay.setSpacing(6)
        self.header = QWidget(self)
        self.header.setObjectName("drawerhead")
        hl = QHBoxLayout(self.header)
        hl.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Ask Claude", self.header)
        title.setStyleSheet(f"font-weight:bold;font-size:13px;color:{theme.TEXT};")
        hl.addWidget(title)
        hl.addStretch(1)
        self.close_button = QPushButton("✕", self.header)
        self.close_button.setObjectName("drawerclose")
        self.close_button.setToolTip("Close chat (Esc)")
        self.close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.close_button.setStyleSheet(
            f"QPushButton{{background:transparent;color:{theme.MUTED};border:none;"
            "padding:0 4px;font-size:12px;}"
            f"QPushButton:hover{{color:{theme.TEXT};}}"
            f"QPushButton:focus{{color:{theme.TEXT};border:1px solid {theme.LINK};"
            "border-radius:3px;}")
        self.close_button.clicked.connect(lambda _c=False: self.close_requested.emit())
        hl.addWidget(self.close_button)
        lay.addWidget(self.header)
        chat.setParent(self)
        lay.addWidget(chat, 1)
        chat.show()
        chat.animate_arrivals = True
        self._e = 0.0                   # openness (eased): 0 closed, 1 open
        self._open = False              # where it is going (or is)
        self._anim = None
        self._build: cards.CardBuild | None = None
        self._visible_h = 0             # its visible height when open (without pad/shadow)

    # -- state -----------------------------------------------------------------------
    def is_open(self) -> bool:
        """Open or opening."""
        return self._open

    def openness(self) -> float:
        return self._e

    def animating(self) -> bool:
        return motion.alive(self._anim)

    def state(self) -> str:
        if self.animating():
            return "opening" if self._open else "closing"
        return "open" if self._open else "closed"

    def building(self) -> bool:
        b = self._build
        return b is not None and not cards._gone(b) and b.running()

    def content_build(self):
        return self._build

    # -- geometry --------------------------------------------------------------------
    def place(self, visible_h: int) -> None:
        """The popup's size changed (or it is about to open): `visible_h` px of drawer
        when open (from the popup's top to just above its chat row)."""
        self._visible_h = max(1, int(visible_h))
        self._apply()

    def _y_for(self, e: float) -> int:
        pad = motion.DRAWER_PAD_PX
        h = self._visible_h + pad + motion.DRAWER_SHADOW_PX
        y_open, y_closed = -pad, -h
        y = round(y_closed + (y_open - y_closed) * e)
        return min(0, y)                # the settle overshoot never opens a gap on top

    def _apply(self) -> None:
        if sip.isdeleted(self):
            return
        w = self._popup.width()
        h = self._visible_h + motion.DRAWER_PAD_PX + motion.DRAWER_SHADOW_PX
        rect = QRect(0, self._y_for(self._e), w, h)
        if rect != self.geometry():
            self.setGeometry(rect)

    # -- open / close ----------------------------------------------------------------
    def open_drawer(self, ms: int | None = None) -> None:
        if self._open and not self.isHidden():
            return
        self._open = True
        from_closed = self.isHidden()
        ms = motion.duration(motion.DRAWER_OPEN_MS) if ms is None else ms
        uitrace.trace("drawer.open", from_e=round(self._e, 3), ms=ms)
        if from_closed:
            self._e = 0.0
            self._apply()
            self._prepare_build()
            self.show()
        self.raise_()
        self._run(1.0, ms, motion.SETTLE)

    def close_drawer(self, ms: int | None = None) -> None:
        if not self._open:
            return
        self._open = False
        ms = motion.duration(motion.DRAWER_CLOSE_MS) if ms is None else ms
        uitrace.trace("drawer.close", from_e=round(self._e, 3), ms=ms)
        self._finish_build()            # the content as it is laid out, then up it goes
        if self.isHidden():
            self._done()
            return
        self._run(0.0, ms, motion.ACCEL)

    def toggle(self) -> None:
        self.close_drawer() if self._open else self.open_drawer()

    def _run(self, target: float, ms: int, kind: str) -> None:
        motion.stop(self._anim)
        self._anim = None
        e0 = self._e
        # A reversal mid-way takes the part of the slide that is left.
        dist = abs(target - e0)
        ms = int(ms * max(0.35, dist)) if ms and dist < 1.0 else ms
        if not ms or dist == 0.0:
            self._set_e(target)
            self._landed()
            return
        self._start_build()

        def frame(t, e0=e0, target=target, kind=kind) -> None:
            if sip.isdeleted(self):
                return
            self._set_e(e0 + (target - e0) * motion.ease(kind, float(t)))

        self._anim = motion.tween(self, 0.0, 1.0, ms, frame, self._landed,
                                  kind=motion.LINEAR)

    def _set_e(self, e: float) -> None:
        self._e = float(e)
        self._apply()

    def _landed(self) -> None:
        if sip.isdeleted(self):
            return
        self._anim = None
        self._set_e(1.0 if self._open else 0.0)
        if not self._open:
            self._done()
        else:
            self._start_build()

    def _done(self) -> None:
        self._finish_build()
        self.chat.finish_arrivals()
        self.hide()
        self._e = 0.0
        self.closed.emit()

    def snap(self) -> None:
        """Every animation ends now, in the state it was going to (the popup hid)."""
        motion.stop(self._anim)
        self._anim = None
        self._finish_build()
        self.chat.finish_arrivals()
        if self._open:
            self._set_e(1.0)
            if self.isHidden():
                self.show()
        elif not self.isHidden():
            self._done()

    # -- the content build -----------------------------------------------------------
    def _prepare_build(self) -> None:
        self._finish_build()
        if not motion.micro_enabled():
            return
        self.layout().activate()
        widgets = [self.header] + self.chat.build_widgets()
        self._build = cards.CardBuild(
            self, motion.DRAWER_BUILD_OFFSET_PX, motion.DRAWER_BUILD_SCALE,
            motion.DRAWER_BUILD_START_MS, motion.DRAWER_BUILD_STAGGER_MS,
            motion.DRAWER_BUILD_MS)
        self._build.prepare(widgets)

    def _start_build(self) -> None:
        b = self._build
        if b is not None and not cards._gone(b):
            b.start()

    def _finish_build(self) -> None:
        b, self._build = self._build, None
        if b is not None and not cards._gone(b):
            b.finish()
            b.deleteLater()

    # -- keyboard: Tab stays inside the drawer ------------------------------------------
    def focusNextPrevChild(self, nxt: bool) -> bool:
        chain = [w for w in self.findChildren(QWidget)
                 if w.isVisible() and w.isEnabled()
                 and w.focusPolicy() & Qt.FocusPolicy.TabFocus]
        chain.sort(key=lambda w: (w.mapTo(self, w.rect().topLeft()).y(),
                                  w.mapTo(self, w.rect().topLeft()).x()))
        if not chain:
            return False
        cur = QApplication.focusWidget()
        i = chain.index(cur) if cur in chain else -1
        chain[(i + (1 if nxt else -1)) % len(chain)].setFocus(
            Qt.FocusReason.TabFocusReason if nxt else Qt.FocusReason.BacktabFocusReason)
        return True

    # -- look ------------------------------------------------------------------------
    def paintEvent(self, e):
        p = QPainter(self)
        try:
            w = self.width()
            body = self.height() - motion.DRAWER_SHADOW_PX
            p.fillRect(QRect(0, 0, w, body), QColor(theme.BG))
            # The popup's frame lines where the drawer covers them (popup coordinates).
            p.save()
            p.setClipRect(QRect(0, 0, w, body))
            p.translate(-self.x(), -self.y())
            winframe.paint_frame_lines(p, self._popup.width(), self._popup.height(),
                                       getattr(self._popup, "frame_by_dwm", False))
            p.restore()
            p.fillRect(QRect(2, body - 1, max(0, w - 4), 1), QColor(theme.ACCENT_SOFT))
            # A soft shadow over the agenda below its edge (painted inside the opaque
            # popup: never a translucent window).
            g = QLinearGradient(0, body, 0, self.height())
            g.setColorAt(0.0, QColor(0, 0, 0, 90))
            g.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.fillRect(QRect(2, body, max(0, w - 4), motion.DRAWER_SHADOW_PX), g)
        finally:
            p.end()

    def hideEvent(self, e):
        if self.animating():
            self.snap()             # (the popup snaps it first; this is a backstop)
        super().hideEvent(e)
