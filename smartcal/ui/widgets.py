"""Small animated widgets shared by the popup and the details panel."""
from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QPushButton

from . import motion, theme


class HoverButton(QPushButton):
    """A flat button whose background fades between `base` and `hover` (motion.FAST)
    when the mouse enters or leaves, and shows `pressed` while held down. The
    background is painted in paintEvent (the style sheet is set once: text color,
    padding, focus border), so a fade frame is just a repaint. With micro-animations
    off it switches at once. Colors can be changed later (restyle)."""

    def __init__(self, text: str, base: str, hover: str, fg: str, extra: str = "",
                 focus: str = "", pressed: str | None = None, radius: int = 4,
                 parent=None, press_scale: bool = False):
        super().__init__(text, parent)
        self._radius = radius
        self._anim = None
        self._hovered = False
        # v0.9.0: `press_scale` (the Join buttons): shrinks a little while held and
        # springs back on release (cards.PressFeedback; micro-animations only).
        self.press_feedback = None
        if press_scale:
            from .cards import PressFeedback
            self.press_feedback = PressFeedback(self)
        self.setStyleSheet(f"QPushButton{{background:transparent;color:{fg};border:none;"
                           f"{extra}}}"
                           + (f"QPushButton:focus{{{focus}}}" if focus else ""))
        self.restyle(base, hover, fg, pressed)

    def restyle(self, base: str, hover: str, fg: str, pressed: str | None = None) -> None:
        self._base, self._hover, self._fg = base, hover, fg
        self._pressed = pressed or theme.mix(base, "#000000", 0.18)
        motion.stop(self._anim)
        self._set_bg(self._hover if self._hovered else self._base)

    def background(self) -> str:
        return self._bg

    def hover_target(self) -> str:
        return QColor(self._hover).name()

    def pressed_color(self) -> str:
        return QColor(self._pressed).name()

    def fading(self) -> bool:
        return motion.alive(self._anim)

    def _set_bg(self, color) -> None:
        self._bg = QColor(color).name()
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self._pressed if self.isDown() else self._bg))
        p.drawRoundedRect(QRectF(self.rect()), self._radius, self._radius)
        p.end()
        super().paintEvent(e)           # text and focus border, from the style sheet

    def _fade_to(self, color: str) -> None:
        motion.stop(self._anim)
        self._anim = motion.tween(self, QColor(self._bg), QColor(color),
                                  motion.micro_duration(motion.FAST), self._set_bg,
                                  kind=motion.FADE)

    def enterEvent(self, e):
        self._hovered = True
        self._fade_to(self._hover)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hovered = False
        self._fade_to(self._base)
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        super().mousePressEvent(e)
        if self.press_feedback is not None and e.button() == Qt.MouseButton.LeftButton:
            self.press_feedback.press()

    def mouseReleaseEvent(self, e):
        super().mouseReleaseEvent(e)        # (a click may hide or delete the window)
        if self.press_feedback is not None and e.button() == Qt.MouseButton.LeftButton:
            from .cards import _gone
            if not _gone(self):
                self.press_feedback.release()

    def hideEvent(self, e):
        if self.press_feedback is not None:
            self.press_feedback.cancel()
        motion.stop(self._anim)
        self._hovered = False
        self._bg = QColor(self._base).name()
        super().hideEvent(e)
