"""Small animated widgets shared by the popup and the details panel."""
from __future__ import annotations

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QPushButton

from . import motion, theme


class HoverButton(QPushButton):
    """A flat button whose background fades between `base` and `hover` (motion.FAST)
    when the mouse enters or leaves, and darkens briefly while pressed. With
    micro-animations off it switches at once. Colors can be changed later (restyle)."""

    def __init__(self, text: str, base: str, hover: str, fg: str, extra: str = "",
                 focus: str = "", parent=None):
        super().__init__(text, parent)
        self._extra, self._focus = extra, focus
        self._anim = None
        self._hovered = False
        self.restyle(base, hover, fg)

    def restyle(self, base: str, hover: str, fg: str) -> None:
        self._base, self._hover, self._fg = base, hover, fg
        motion.stop(self._anim)
        self._set_bg(self._hover if self._hovered else self._base)

    def background(self) -> str:
        return self._bg

    def hover_target(self) -> str:
        return self._hover

    def _set_bg(self, color) -> None:
        self._bg = QColor(color).name()
        focus = f"QPushButton:focus{{{self._focus}}}" if self._focus else ""
        pressed = theme.mix(self._bg, "#000000", 0.18)
        self.setStyleSheet(f"QPushButton{{background:{self._bg};color:{self._fg};"
                           f"border:none;{self._extra}}}"
                           f"QPushButton:pressed{{background:{pressed};}}{focus}")

    def _fade_to(self, color: str) -> None:
        motion.stop(self._anim)
        self._anim = motion.tween(self, QColor(self._bg), QColor(color),
                                  motion.micro_duration(motion.FAST), self._set_bg,
                                  kind="enter")

    def enterEvent(self, e):
        self._hovered = True
        self._fade_to(self._hover)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hovered = False
        self._fade_to(self._base)
        super().leaveEvent(e)

    def hideEvent(self, e):
        motion.stop(self._anim)
        self._hovered = False
        self._set_bg(self._base)
        super().hideEvent(e)
