"""v0.12.0: the dialogs opened from the tray menu (Settings, Reminders) open the way the
Day/Week/Month window and its details dialog do: never a white frame.

Recorded on v0.11.3 (real display, DXGI capture of every composed frame plus a WinEvent
monitor; see tests/test_dialog_flash.py): Settings was shown fully opaque before Qt had
painted it, so for 2 to 4 composed frames (35 to 65 ms) the whole dialog was the
system's white erase; it was also shown 8 px off its spot and moved there right after
(placed with a guessed frame, then again with the real one). Reminders did the same and,
since it rebuilt its rows in showEvent after it had been placed, kept growing for four
more frames after it was shown, down past the work area: its last rows ended up under
the taskbar.

Now (TrayDialog.open_near_tray): the native window is made while still hidden, so its
real frame is known and it is placed once, where it ends up; Windows' erase is answered
with the dark background (nativebg); on the real display it is shown fully transparent
and fades in as a unit once its first frame is painted (at once with animations off); on
Windows 11 its corners are rounded and its edge gets the accent color once it is visible
(winframe). Its content builds in like the details dialog's lines (micro-animations
only), and its push buttons shrink a touch while pressed."""
from __future__ import annotations

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QObject, QPoint, QRect, Qt, QTimer
from PyQt6.QtWidgets import QDialog, QWidget

from . import cards, motion, nativebg, placement, theme, winframe


def _gone(obj) -> bool:
    try:
        return obj is None or sip.isdeleted(obj)
    except TypeError:
        return False


class _PressScale(QObject):
    """Press feedback for a stock button (cards.PressFeedback: shrinks while held,
    springs back on release; micro-animations only)."""

    def __init__(self, button):
        super().__init__(button)
        self.feedback = cards.PressFeedback(button)
        button.installEventFilter(self)

    def eventFilter(self, obj, e) -> bool:
        try:
            t = e.type()
            if t == QEvent.Type.MouseButtonPress and e.button() == Qt.MouseButton.LeftButton:
                self.feedback.press()
            elif t == QEvent.Type.MouseButtonRelease and \
                    e.button() == Qt.MouseButton.LeftButton:
                QTimer.singleShot(0, self._release)     # after the click ran
            elif t == QEvent.Type.Hide:
                self.feedback.cancel()
        except Exception:  # noqa: BLE001 - a look never breaks a button
            pass
        return False

    def _release(self) -> None:
        if not _gone(self) and not _gone(self.feedback):
            self.feedback.release()


def add_press_scale(button) -> None:
    """Give `button` the press-scale feedback (once)."""
    if button.findChild(_PressScale, options=Qt.FindChildOption.FindDirectChildrenOnly) is None:
        _PressScale(button)


class TrayDialog(QDialog):
    """Base of Settings and Reminders. Subclasses fill the dialog, then the owner calls
    open_near_tray(); build_lines() lists what builds in (top to bottom)."""

    OBJECT_NAME = "trayDialog"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName(self.OBJECT_NAME)
        self.setStyleSheet(theme.dialog_style(self.OBJECT_NAME))
        self.setAutoFillBackground(True)
        self._build: cards.CardBuild | None = None
        self._framed = False
        self._frame_hwnd = 0
        self._reveal_pending = False
        self._reveal_anim = None
        self._reveal_timer = QTimer(self)
        self._reveal_timer.setSingleShot(True)
        self._reveal_timer.setInterval(motion.SHOW_START_FALLBACK_MS)
        self._reveal_timer.timeout.connect(self._reveal)
        self._tray_geometry: QRect | None = None
        self._cloaked = False

    # -- opening ------------------------------------------------------------------
    def build_lines(self) -> list[QWidget]:
        return []

    def open_near_tray(self, tray_geometry) -> None:
        """Place it next to the tray icon (fully inside the work area), then show it:
        placed, framed and transparent before show(); revealed once painted."""
        if self.isVisible():
            self.raise_()
            self.activateWindow()
            return
        self._tray_geometry = QRect(tray_geometry) if tray_geometry is not None else None
        self._stop_reveal()
        self.finish_build()
        native = winframe.native_windows()
        if native:
            try:
                self.winId()                # the native window, hidden: its real frame
            except Exception:  # noqa: BLE001
                pass
        placement.place_near_tray(self, self._tray_geometry)
        self.prepare_build()
        if native:
            try:
                self._framed = winframe.apply_dwm_frame(self)   # edge color once shown
                self._frame_hwnd = int(self.winId()) if self._framed else 0
            except Exception:  # noqa: BLE001 - a frame must never break the dialog
                self._framed = False
            self._reveal_pending = True
            self.setWindowOpacity(0.0)
            # Shown cloaked: DWM would otherwise draw the frame's outline once at the
            # primary screen's top-left (see winframe.set_cloak).
            self._cloaked = winframe.set_cloak(self, True)
            self._reveal_timer.start()      # (no paint seen: shown anyway)
        self.show()
        # Only if Windows' frame differs from what was measured (unseen: transparent).
        placement.place_near_tray(self, self._tray_geometry)
        self.raise_()
        self.activateWindow()

    def prepare_build(self) -> None:
        self.finish_build()
        if not motion.micro_enabled():
            return
        lines = [w for w in self.build_lines() if w is not None and not _gone(w)]
        if not lines:
            return
        self._build = cards.CardBuild(self, motion.PANEL_BUILD_OFFSET_PX, 1.0,
                                      motion.WIN_DETAILS_START_MS,
                                      motion.PANEL_BUILD_STAGGER_MS, motion.PANEL_BUILD_MS)
        self._build.prepare(lines)

    def content_build(self) -> cards.CardBuild | None:
        return self._build

    def building(self) -> bool:
        return self._build is not None and self._build.running()

    def finish_build(self) -> None:
        b, self._build = self._build, None
        if b is not None and not _gone(b):
            b.finish()
            b.deleteLater()

    # -- reveal -------------------------------------------------------------------
    def revealing(self) -> bool:
        return self._reveal_pending or motion.alive(self._reveal_anim)

    def _reveal(self) -> None:
        if not self._reveal_pending or _gone(self):
            return
        self._reveal_pending = False
        self._reveal_timer.stop()
        self._uncloak()
        motion.stop(self._reveal_anim)
        self._reveal_anim = motion.tween(
            self, 0.0, 1.0, motion.duration(motion.WIN_REVEAL_MS),
            lambda v: None if _gone(self) else self.setWindowOpacity(float(v)),
            kind=motion.FADE)

    def _uncloak(self) -> None:
        if self._cloaked:
            self._cloaked = False
            winframe.set_cloak(self, False)

    def _stop_reveal(self) -> None:
        self._reveal_pending = False
        self._reveal_timer.stop()
        self._uncloak()
        motion.stop(self._reveal_anim)
        self._reveal_anim = None
        if self.windowOpacity() != 1.0:
            self.setWindowOpacity(1.0)

    # -- keeping it on screen ------------------------------------------------------
    def settle_geometry(self) -> None:
        """Its content changed while open (rows added or removed): the new size, its
        edge on the tray side kept where it was, still fully inside the work area."""
        if not self.isVisible():
            return
        old = self.frameGeometry()
        self.adjustSize()
        new = self.frameGeometry()
        area = placement.area_for(self._tray_geometry) if self._tray_geometry is not None \
            else (self.screen().availableGeometry() if self.screen() is not None else None)
        if area is None:
            return
        y = old.y()
        if old.center().y() > area.center().y():     # nearer the bottom: keep the bottom
            y = old.bottom() - new.height() + 1
        pos = placement.clamp(QPoint(old.x(), y), new.size(), area)
        if pos != new.topLeft():
            self.move(pos)                  # (a window's pos() is its frame's top-left)

    # -- Qt -----------------------------------------------------------------------
    def paintEvent(self, e):
        super().paintEvent(e)
        if self._reveal_pending:
            # A moment after this frame is composed (DWM's stray outline comes with
            # the first composition after the show, while still cloaked).
            QTimer.singleShot(winframe.UNCLOAK_AFTER_PAINT_MS if self._cloaked else 0,
                              self._reveal)

    def nativeEvent(self, event_type, message):
        # Windows' erase answered with the dark background (no white frame when shown
        # or uncovered). Never the base class's nativeEvent: always a (bool, int).
        return nativebg.native_event(event_type, message, theme.BG)

    def showEvent(self, e):
        super().showEvent(e)
        if winframe.native_windows():
            try:
                hwnd = int(self.winId())
                if hwnd and hwnd != self._frame_hwnd:     # (shown some other way)
                    self._frame_hwnd = hwnd
                    self._framed = winframe.apply_dwm_frame(self)
            except Exception:  # noqa: BLE001
                pass
        if self._build is not None:
            QTimer.singleShot(0, lambda: None if self._build is None or _gone(self)
                              else self._build.start())

    def hideEvent(self, e):
        self.finish_build()
        self._stop_reveal()
        super().hideEvent(e)
