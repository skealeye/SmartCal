"""v0.9.0 "card build": agenda rows (and the details panel's lines) building into place,
and the press feedback of a row or a Join button.

Everything here moves CHILD widgets inside the opaque popup and panel windows; the
top-level windows themselves are never made translucent, masked or faded by it.

How a widget is drawn moved, scaled and faded while its layout keeps it where it is: a
CardEffect (a QGraphicsEffect) on the widget. Qt renders the widget, children included,
into a pixmap and the effect paints that pixmap with the current offset, scale (around
the widget's center) and opacity. The layout never sees any of it (no geometry changes,
no layout jumps, rows stay clickable where they are), and a relayout mid-build (a
refresh, the list growing) just moves the effect along with its widget. The effect is
removed as soon as its widget's build (or press) ends: rendering through a pixmap loses
ClearType text, so a widget only goes through it while it actually moves.

Lifetime: a CardBuild belongs to the window that started it and its timeline belongs to
the build (both deleted with their parent). Every frame checks that the widget and its
effect still exist and that the effect is still the widget's (a collapse or a fade may
have replaced it): anything gone is skipped, never touched."""
from __future__ import annotations

from PyQt6 import sip
from PyQt6.QtCore import QObject, QPointF, QRectF, Qt
from PyQt6.QtGui import QPainter, QTransform
from PyQt6.QtWidgets import QGraphicsEffect

from . import motion


def _gone(obj) -> bool:
    try:
        return obj is None or sip.isdeleted(obj)
    except (RuntimeError, TypeError):
        return True


class CardEffect(QGraphicsEffect):
    """Paints its widget offset by `dy` px, scaled by `scale` around its center and at
    `opacity`. At (1.0, 0, 1.0) it draws the widget as is."""

    def __init__(self, parent=None, pad_below: int = 0):
        super().__init__(parent)
        self._opacity, self._dy, self._scale = 1.0, 0.0, 1.0
        # The area it may paint: the widget's rect, plus below it (the offset) and a few
        # px around it (an overshoot above it, a scale past 1).
        self._pad_below = max(0, int(pad_below)) + 4

    def state(self) -> tuple[float, float, float]:
        return self._opacity, self._dy, self._scale

    def set_state(self, opacity: float, dy: float, scale: float) -> None:
        opacity = motion.clamp(float(opacity), 0.0, 1.0)
        if (opacity, dy, scale) == (self._opacity, self._dy, self._scale):
            return
        self._opacity, self._dy, self._scale = opacity, float(dy), float(scale)
        self.update()

    def boundingRectFor(self, rect: QRectF) -> QRectF:
        grow = max(4.0, rect.width() * 0.03)
        return rect.adjusted(-grow, -6.0, grow, float(self._pad_below))

    def draw(self, painter: QPainter) -> None:
        try:
            op, dy, s = self._opacity, self._dy, self._scale
            if op <= 0.0:
                return
            if op >= 1.0 and dy == 0.0 and s == 1.0:
                self.drawSource(painter)
                return
            pm, offset = self.sourcePixmap(Qt.CoordinateSystem.LogicalCoordinates,
                                           QGraphicsEffect.PixmapPadMode.NoPad)
            if pm.isNull():
                return
            src = self.sourceBoundingRect(Qt.CoordinateSystem.LogicalCoordinates)
            c = src.center()
            t = QTransform()
            t.translate(c.x(), c.y() + dy)
            t.scale(s, s)
            t.translate(-c.x(), -c.y())
            painter.save()
            try:
                painter.setOpacity(painter.opacity() * op)
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
                painter.setTransform(t, True)
                painter.drawPixmap(QPointF(offset), pm)
            finally:
                painter.restore()
        except Exception:  # noqa: BLE001 - a frame of an animation never breaks a paint
            pass


class CardBuild(QObject):
    """One staggered build of `widgets` (top to bottom): each starts transparent,
    `offset` px lower and at `scale_start`, and springs into place, `stagger` ms after
    the previous one. prepare() puts them in their start state (before the window first
    paints them); start() runs the timeline. Widgets are identified by `keys` (to carry a
    running build over to rebuilt widgets, see rebind)."""

    def __init__(self, owner: QObject, offset: int, scale_start: float, start_ms: int,
                 stagger_ms: int, item_ms: int, cap: int = motion.BUILD_MAX_STAGGER):
        super().__init__(owner)
        self.offset, self.scale_start = offset, scale_start
        self.start_ms, self.stagger_ms, self.item_ms, self.cap = (start_ms, stagger_ms,
                                                                  item_ms, cap)
        self._items: list[dict] = []
        self._anim = None
        self._elapsed = 0.0
        self._started = False
        self._over = False

    # -- set up ----------------------------------------------------------------------
    def prepare(self, widgets, keys=None) -> None:
        widgets = list(widgets)
        keys = list(keys) if keys is not None else [None] * len(widgets)
        delays = motion.build_delays(len(widgets), self.start_ms, self.stagger_ms, self.cap)
        for w, key, delay in zip(widgets, keys, delays):
            item = {"w": w, "key": key, "delay": delay, "fx": None, "done": False}
            self._items.append(item)
            self._install(item)
        self._apply(0.0)

    def _install(self, item) -> None:
        w = item["w"]
        if _gone(w) or w.graphicsEffect() is not None:
            item["done"] = True             # something else animates it: leave it alone
            return
        fx = CardEffect(w, pad_below=self.offset)
        w.setGraphicsEffect(fx)
        item["fx"] = fx

    def start(self) -> None:
        """Run the timeline (once; a second call does nothing)."""
        if self._started or self._over:
            return
        self._started = True
        total = self.total_ms()
        if total <= 0 or not self._live_items():
            self.finish()
            return
        self._anim = motion.tween(self, 0.0, float(total), total, self._frame,
                                  self._timeline_done, kind=motion.LINEAR)

    # -- state -----------------------------------------------------------------------
    def total_ms(self) -> int:
        return motion.build_total_ms(len(self._items), self.start_ms, self.stagger_ms,
                                     self.item_ms, self.cap)

    def started(self) -> bool:
        return self._started

    def running(self) -> bool:
        """Prepared and not over (waiting to start, or animating)."""
        return not self._over and bool(self._live_items())

    def elapsed(self) -> float:
        return self._elapsed

    def widgets(self) -> list:
        return [i["w"] for i in self._items]

    def building(self, w) -> bool:
        """`w` is still on its way in."""
        return any(i["w"] is w for i in self._live_items())

    def delays(self) -> list[int]:
        return [i["delay"] for i in self._items]

    def _alive(self, item) -> bool:
        if item["done"]:
            return False
        w, fx = item["w"], item["fx"]
        if _gone(w) or _gone(fx):
            return False
        try:
            return w.graphicsEffect() is fx
        except RuntimeError:
            return False

    def _live_items(self) -> list[dict]:
        return [i for i in self._items if self._alive(i)]

    # -- frames ----------------------------------------------------------------------
    def _frame(self, value) -> None:
        if self._over:
            return
        self._apply(float(value))

    def _apply(self, elapsed: float) -> None:
        self._elapsed = elapsed
        for item in self._items:
            if not self._alive(item):
                item["done"] = True
                continue
            op, dy, s, done = motion.card_state(elapsed, item["delay"], self.item_ms,
                                                self.offset, self.scale_start)
            if done:
                self._release(item)
            else:
                try:
                    item["fx"].set_state(op, dy, s)
                except RuntimeError:
                    item["done"] = True

    def _release(self, item) -> None:
        """The item in its final state: its effect removed (Qt deletes it)."""
        item["done"] = True
        w, fx = item["w"], item["fx"]
        item["fx"] = None
        if _gone(w) or _gone(fx):
            return
        try:
            if w.graphicsEffect() is fx:
                w.setGraphicsEffect(None)
        except RuntimeError:
            pass

    def _timeline_done(self) -> None:
        self._anim = None
        self.finish()

    # -- ending ----------------------------------------------------------------------
    def finish_widget(self, w) -> None:
        """`w` jumps to its final state (e.g. it was pressed mid-build)."""
        for item in self._items:
            if item["w"] is w and not item["done"]:
                self._release(item)

    def finish(self) -> None:
        """Everything in its final state at once; the timeline stops (safe to repeat)."""
        self._over = True
        anim, self._anim = self._anim, None
        motion.stop(anim)
        for item in self._items:
            self._release(item)

    def rebind(self, widgets_by_key: dict) -> None:
        """The widgets were rebuilt (a refresh mid-build): the ones still on their way
        in carry on, at the same point of the timeline, on their new widgets. New
        widgets with no item stay as they are (final)."""
        if self._over:
            return
        for item in self._items:
            if item["key"] is None or item["key"] not in widgets_by_key:
                continue
            new = widgets_by_key[item["key"]]
            if new is item["w"]:
                continue
            p = (self._elapsed - item["delay"]) / self.item_ms if self.item_ms else 1.0
            was_done = item["done"]
            self._release(item)             # (the old widget is on its way out)
            item["w"] = new
            if was_done or p >= 1.0:
                continue
            item["done"] = False
            self._install(item)
        self._apply(self._elapsed)


class PressFeedback(QObject):
    """`widget` shrinks to motion.PRESS_SCALE while pressed and springs back on release
    (micro-animations only). Its CardEffect exists only while that runs. Never touches
    a widget that has another effect (a build, a fade) at that moment."""

    def __init__(self, widget):
        super().__init__(widget)
        self._w = widget
        self._fx = None
        self._anim = None
        self._scale = 1.0

    def active(self) -> bool:
        """Pressed, or springing back (its effect is on the widget)."""
        return self._fx is not None and not _gone(self._fx)

    def scale(self) -> float:
        return self._scale

    def press(self) -> None:
        self._go(motion.PRESS_SCALE, motion.PRESS_IN_MS, motion.FADE)

    def release(self) -> None:
        if self._fx is None:
            return
        self._go(1.0, motion.PRESS_OUT_MS, motion.SPRING)

    def _go(self, target: float, ms: int, kind: str) -> None:
        w = self._w
        if _gone(w):
            return
        motion.stop(self._anim)
        self._anim = None
        ms = motion.micro_duration(ms) if w.isVisible() else 0
        if not ms:
            self.cancel()
            return
        if self._fx is None or _gone(self._fx) or w.graphicsEffect() is not self._fx:
            if w.graphicsEffect() is not None:
                self._fx = None
                return                      # something else animates it right now
            self._fx = CardEffect(w)
            w.setGraphicsEffect(self._fx)
            self._scale = 1.0
        self._anim = motion.tween(self, self._scale, target, ms, self._set_scale,
                                  self._done if target == 1.0 else None, kind=kind)

    def _set_scale(self, value) -> None:
        self._scale = float(value)
        fx = self._fx
        if fx is None or _gone(fx):
            return
        try:
            fx.set_state(1.0, 0.0, self._scale)
        except RuntimeError:
            pass

    def _done(self) -> None:
        self._anim = None
        self.cancel()

    def cancel(self) -> None:
        """Back to normal at once: no animation, no effect."""
        motion.stop(self._anim)
        self._anim = None
        self._scale = 1.0
        fx, self._fx = self._fx, None
        w = self._w
        if fx is None or _gone(w) or _gone(fx):
            return
        try:
            if w.graphicsEffect() is fx:
                w.setGraphicsEffect(None)
        except RuntimeError:
            pass
