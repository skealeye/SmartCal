"""Day / Week / Month window. Read-only; reads only from the cache.

v0.11.0: readable tiles and motion. Meetings are painted tiles (EventChip) with the
popup's look (Side stripe or Whole tile, the owner's "Color style"): the title first, in
semibold, then the time and the location in a softer tone, each line elided (the full
text is in the tooltip) and the secondary lines dropped when a tile is too short; a
15-minute meeting still gets one readable line. Tiles keep TILE_GAP_Y px between
back-to-back meetings and TILE_GAP_X px between concurrent ones. The current-time line is
drawn above the tiles. Month cells are rounded cards that show as many meetings as fit
and a "+N more" chip (opens the day) for the rest.

A refresh is diffed (like the popup since v0.10.0): days, columns, cells and tiles whose
content did not change keep their widgets (no flicker, scroll position kept); the minute
tick only moves the current-time line. Motion (motion.py, v0.11.0 section) moves child
widgets only: the window opens and closes the Windows way."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from PyQt6 import sip
from PyQt6.QtCore import (QByteArray, QEvent, QPoint, QPointF, QRect, QRectF, QSettings,
                          QSize, Qt, QTimer, QUrl, pyqtSignal)
from PyQt6.QtGui import (QColor, QDesktopServices, QFont, QFontMetrics, QGuiApplication,
                         QPainter, QPainterPath, QPalette, QPixmap)
from PyQt6.QtWidgets import (QButtonGroup, QDialog, QFrame, QGridLayout, QHBoxLayout,
                             QLabel, QMainWindow, QPushButton, QScrollArea, QSizePolicy,
                             QStackedWidget, QVBoxLayout, QWidget)

from .. import agenda
from ..config import data_dir
from ..settings import color_key
from ..sync import synced_days
from ..textutil import calendar_label, html_to_text, meeting_label, plain_tip, safe_http_url
from . import cards, layout, meeting_menus, motion, nativebg, placement, theme, winframe
from .linktext import LinkText

HOUR_H = 60                 # px per hour (v0.11.0: was 48; 1 px per minute)
GUTTER_W = 56
VIEWS = ("day", "week", "month")
DEFAULT_SCROLL_HOUR = 7
SCROLL_HEADROOM = 10        # px above the hour scrolled to: its label shows whole
DEFAULT_SIZE = QSize(1000, 700)

DECLINED_OPACITY = 0.45
GRID = "#2e3033"            # hour lines, column edges
GRID_HALF = "#25272a"       # half-hour lines (fainter)
TODAY_BG = "#262d3a"
WEEKEND_BG = "#1b1c1f"      # Saturday and Sunday: a shade darker than theme.BG
NOW_LINE = "#ea4335"

# -- tiles (v0.11.0) ------------------------------------------------------------------
TILE_GAP_Y = 3              # px between back-to-back meetings
TILE_GAP_X = 4              # px between concurrent meetings (side by side)
TILE_MARGIN_L = 2           # from the column's left grid line
TILE_MARGIN_R = 6           # to the column's right edge (the empty slot stays clickable)
TILE_PAD_X = 7              # inner padding
TILE_PAD_Y = 4
TILE_RADIUS = 5
STRIPE_W = 3                # the "Side stripe" bar
STRIPE_TINT = 0.70          # Side stripe tile: the color this far toward theme.BG
TITLE_PX = 12
META_PX = 11
TITLE_MAX_LINES = 3
CHIP_H = 19                 # all-day bars and month chips
CHIP_GAP = 2
HEADER_H = 34

# -- month cells -------------------------------------------------------------------
MONTH_GAP = 3
CELL_RADIUS = theme.RADIUS_SMALL
CELL_BG = "#242529"
CELL_WEEKEND_BG = "#222327"
CELL_OUT_BG = "#19191c"     # days of the next / previous month
CELL_NUMBER_H = 22
CELL_PAD = 4
CELL_MIN_H = CELL_NUMBER_H + CHIP_H + 2 * CELL_PAD

BLOCK, BAR, CHIP = "block", "bar", "chip"     # timeline tile, all-day bar, month chip


def _calendar_color(event) -> str:
    return theme.stripe_color(event.color)


def _clear(lay) -> None:
    while lay.count():
        item = lay.takeAt(0)
        w = item.widget()
        if w is not None:
            w.hide()
            w.deleteLater()


def _gone(obj) -> bool:
    try:
        return obj is None or sip.isdeleted(obj)
    except (RuntimeError, TypeError):
        return True


def _font(base: QFont, px: int, weight=QFont.Weight.Normal, strike: bool = False) -> QFont:
    f = QFont(base)
    f.setPixelSize(px)
    f.setWeight(weight)
    f.setStrikeOut(strike)
    return f


def event_key(ev) -> tuple:
    return (ev.account, ev.calendar_id, ev.id, ev.start)


def wrap_lines(text: str, fm: QFontMetrics, width: int, max_lines: int) -> list[str]:
    """`text` word-wrapped into at most `max_lines` lines of `width` px; a line that
    still does not fit (the last one, a very long word) ends in an ellipsis."""
    words = (text or "").split()
    if not words or max_lines <= 0 or width <= 0:
        return []
    elide = Qt.TextElideMode.ElideRight
    lines: list[str] = []
    cur, i = "", 0
    while i < len(words):
        trial = words[i] if not cur else f"{cur} {words[i]}"
        if fm.horizontalAdvance(trial) <= width:
            cur, i = trial, i + 1
            continue
        if not cur:                     # one word wider than the line
            cur, i = words[i], i + 1
        if len(lines) == max_lines - 1:
            break
        lines.append(fm.elidedText(cur, elide, width))
        cur = ""
    rest = " ".join(([cur] if cur else []) + words[i:])
    if rest:
        lines.append(fm.elidedText(rest, elide, width))
    return lines[:max_lines]


def tile_colors(color: str, style: str, kind: str, hover: float = 0.0):
    """(background or None, text, secondary text, stripe or None) of a tile of `kind`
    in `style` ("stripe" / "fill"), `hover` 0..1 of the way to its hover shade. Text is
    >= 4.5:1 on its background in every case."""
    color = theme.stripe_color(color)
    if style == "fill":
        fg = theme.text_on(color)
        away = theme.ON_LIGHT if fg == theme.ON_DARK else theme.ON_DARK
        bg = theme.mix(color, away, 0.10 * hover) if hover else color
        return QColor(bg), fg, theme.muted_on(color), None
    if kind == CHIP:                    # month: a dot, no tile (a faint one on hover)
        bg = None
        if hover > 0:
            bg = QColor(theme.mix(theme.BG, "#ffffff", 0.08))
            bg.setAlphaF(min(1.0, hover))
        return bg, theme.TEXT, theme.MUTED, color
    base = theme.mix(color, theme.BG, STRIPE_TINT)
    bg = theme.mix(base, "#ffffff", 0.08 * hover) if hover else base
    meta = theme.shade_keeping_contrast(theme.TEXT, base, base, (0.38, 0.3, 0.2, 0.1))
    return QColor(bg), theme.TEXT, meta, color


class EventChip(QLabel):
    """One meeting: a timeline tile (BLOCK), an all-day bar (BAR) or a month chip
    (CHIP). Painted by hand (see the module doc); text() is the plain one-line label
    ("10a Standup"), also its accessible name. Hover fades its shade (motion.FAST), a
    press shrinks it (cards.PressFeedback)."""

    clicked = pyqtSignal(object)
    menu_requested = pyqtSignal(object, QPoint)   # event, global position (right-click)
    touched = pyqtSignal(object)                  # pressed (lands it if it is building)

    def __init__(self, event, text: str, wrap: bool = False, parent=None,
                 color: str | None = None, kind: str = BAR, style: str = "stripe",
                 time_text: str = "", range_text: str = "", meta: str = ""):
        super().__init__(text, parent)
        self.event = event
        self.kind = kind
        self.style = style if style in theme.TILE_STYLES else "stripe"
        self.color = theme.stripe_color(color or event.color)
        self.title = event.title or ""
        self.time_text, self.range_text, self.meta = time_text, range_text, meta
        self.block: layout.Block | None = None
        self.signature = None
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setWordWrap(wrap)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(text)
        if kind != BLOCK:
            self.setFixedHeight(CHIP_H)
        self.declined = bool(getattr(event, "declined", False))
        if self.declined:                   # dimmed (painted) and struck through
            f = self.font()
            f.setStrikeOut(True)
            self.setFont(f)
        self._opacity = DECLINED_OPACITY if self.declined else 1.0
        self._hover = 0.0
        self._hover_anim = None
        self.press_feedback = cards.PressFeedback(self)
        tip = "\n".join(t for t in (self.title + (" (declined)" if self.declined else ""),
                                    range_text or time_text, meta) if t)
        self.setToolTip(plain_tip(tip))

    # -- look ------------------------------------------------------------------------
    def opacity(self) -> float:
        return self._opacity

    def set_opacity(self, value: float) -> None:
        if self.declined:
            value = min(value, DECLINED_OPACITY)
        self._opacity = float(value)
        self.update()

    def hover_level(self) -> float:
        return self._hover

    def colors(self):
        return tile_colors(self.color, self.style, self.kind, self._hover)

    def _set_hover(self, value) -> None:
        if _gone(self):
            return
        self._hover = float(value)
        self.update()

    def _hover_to(self, target: float) -> None:
        motion.stop(self._hover_anim)
        self._hover_anim = None
        ms = motion.micro_duration(motion.FAST) if self.isVisible() else 0
        if self._hover == target:
            return
        self._hover_anim = motion.tween(self, self._hover, float(target), ms,
                                        self._set_hover, kind=motion.FADE)

    def enterEvent(self, e):
        self._hover_to(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover_to(0.0)
        super().leaveEvent(e)

    def paintEvent(self, _e):
        w, h = self.width(), self.height()
        if w < 2 or h < 2:
            return
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setOpacity(self._opacity)
            bg, fg, meta, bar = self.colors()
            radius = TILE_RADIUS if self.kind == BLOCK else 4
            shape = QPainterPath()
            shape.addRoundedRect(QRectF(0, 0, w, h), radius, radius)
            if bg is not None:
                p.fillPath(shape, bg)
            left = TILE_PAD_X
            if bar is not None and self.kind != CHIP:
                p.save()
                p.setClipPath(shape)
                p.fillRect(QRectF(0, 0, STRIPE_W, h), QColor(bar))
                p.restore()
                left += STRIPE_W - 1
            if self.kind == BLOCK:
                self._paint_block(p, left, fg, meta)
            else:
                self._paint_line(p, left, fg, meta, bar)
        finally:
            p.end()

    def _title_font(self, px: int = TITLE_PX) -> QFont:
        return _font(self.font(), px, QFont.Weight.DemiBold, self.declined)

    def _paint_line(self, p: QPainter, left: int, fg: str, meta: str, bar) -> None:
        """BAR / CHIP: one line, vertically centered."""
        w, h = self.width(), self.height()
        x = 6 if self.kind == CHIP else left
        if self.kind == CHIP and self.style != "fill" and bar is not None:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(bar))
            p.drawEllipse(QPointF(x + 3, h / 2), 3.0, 3.0)
            x += 10
        right = w - 5
        if self.time_text:
            mf = _font(self.font(), META_PX)
            fmm = QFontMetrics(mf)
            tw = fmm.horizontalAdvance(self.time_text)
            if right - x > tw + 12:
                p.setFont(mf)
                p.setPen(QColor(meta))
                p.drawText(QRect(x, 0, tw + 1, h), Qt.AlignmentFlag.AlignVCenter, self.time_text)
                x += tw + 4
        tf = self._title_font(META_PX)
        fm = QFontMetrics(tf)
        if right - x < 4:
            return
        p.setFont(tf)
        p.setPen(QColor(fg))
        p.drawText(QRect(x, 0, right - x, h), Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText(self.title, Qt.TextElideMode.ElideRight, right - x))

    def _paint_block(self, p: QPainter, left: int, fg: str, meta: str) -> None:
        """Timeline tile: title (wrapped, up to TITLE_MAX_LINES), then the time, then the
        location, as far as the height allows; too short for two lines: the title and,
        if it fits, the start time on one line."""
        w, h = self.width(), self.height()
        tw = w - left - (TILE_PAD_X - 2)
        if tw < 6:
            return
        tf, mf = self._title_font(), _font(self.font(), META_PX)
        ftm, fmm = QFontMetrics(tf), QFontMetrics(mf)
        lt, lm = ftm.height(), fmm.height()
        inner = h - 2 * TILE_PAD_Y
        if inner < lt + lm:
            y = max(0, (h - lt) // 2)
            time = self.time_text
            time_w = fmm.horizontalAdvance(time) if time else 0
            title_w = tw
            show_time = bool(time) and tw >= time_w + 6 + ftm.horizontalAdvance("Mmmmmm")
            if show_time:
                title_w = tw - time_w - 6
            title = ftm.elidedText(self.title, Qt.TextElideMode.ElideRight, title_w)
            p.setFont(tf)
            p.setPen(QColor(fg))
            p.drawText(QRect(left, y, title_w, lt), Qt.AlignmentFlag.AlignVCenter, title)
            if show_time:
                x = left + ftm.horizontalAdvance(title) + 6
                p.setFont(mf)
                p.setPen(QColor(meta))
                p.drawText(QRect(x, y, max(0, left + tw - x), lt),
                           Qt.AlignmentFlag.AlignVCenter, time)
            return
        rows = max(1, (inner - lm) // lt)
        lines = wrap_lines(self.title, ftm, tw, min(TITLE_MAX_LINES, rows)) or [""]
        y = TILE_PAD_Y
        p.setFont(tf)
        p.setPen(QColor(fg))
        for line in lines:
            p.drawText(QRect(left, y, tw, lt), Qt.AlignmentFlag.AlignVCenter, line)
            y += lt
        p.setFont(mf)
        p.setPen(QColor(meta))
        p.drawText(QRect(left, y, tw, lm), Qt.AlignmentFlag.AlignVCenter,
                   fmm.elidedText(self.range_text or self.time_text,
                                  Qt.TextElideMode.ElideRight, tw))
        y += lm
        if self.meta and y + lm <= h - TILE_PAD_Y + 1:
            p.drawText(QRect(left, y, tw, lm), Qt.AlignmentFlag.AlignVCenter,
                       fmm.elidedText(self.meta, Qt.TextElideMode.ElideRight, tw))

    def visible_lines(self) -> int:
        """How many text lines the tile shows at its current height (tests)."""
        ftm = QFontMetrics(self._title_font())
        fmm = QFontMetrics(_font(self.font(), META_PX))
        inner = self.height() - 2 * TILE_PAD_Y
        if self.kind != BLOCK or inner < ftm.height() + fmm.height():
            return 1
        return 2 + (1 if self.meta and inner >= ftm.height() + 2 * fmm.height() else 0)

    # -- input -----------------------------------------------------------------------
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.touched.emit(self)
            self.press_feedback.press()
        e.accept()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.press_feedback.release()
            if self.rect().contains(e.position().toPoint()):
                self.clicked.emit(self.event)
        e.accept()

    def contextMenuEvent(self, e):
        pos = e.globalPos()
        if e.reason() == e.Reason.Keyboard:
            pos = self.mapToGlobal(self.rect().center())
        self.menu_requested.emit(self.event, pos)
        e.accept()

    def hideEvent(self, e):
        motion.stop(self._hover_anim)
        self._hover_anim = None
        self._hover = 0.0
        self.press_feedback.cancel()
        super().hideEvent(e)


def _chip_label(ev, ref: datetime) -> tuple[str, str, str]:
    """(one-line text, start time, time range) of a timed event."""
    s, e = layout.to_local(ev.start, ref), layout.to_local(ev.end, ref)
    start = layout.short_time(s)
    return f"{start} {ev.title}", start, f"{start} – {layout.short_time(e)}"


def _content_sig(ev, color: str, style: str) -> tuple:
    return (ev.title, ev.start, ev.end, ev.all_day, color, style,
            bool(getattr(ev, "declined", False)), ev.location or "")


class DayHeader(QLabel):
    """"Wed 4" above a day's column; today: the number in an accent pill. Click: that
    day in the Day view."""

    clicked = pyqtSignal(object)

    def __init__(self, day: date, is_today: bool, text: str, parent=None,
                 weekend: bool = False):
        super().__init__(text, parent)
        self.day, self.is_today, self.weekend = day, is_today, weekend
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(HEADER_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"{day:%A, %B} {day.day}: open in Day view")

    def paintEvent(self, _e):
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            wf = _font(self.font(), 11, QFont.Weight.Medium)
            nf = _font(self.font(), 13, QFont.Weight.DemiBold)
            wd, num = f"{self.day:%a}", str(self.day.day)
            fw, fn = QFontMetrics(wf), QFontMetrics(nf)
            pill_w = max(24, fn.horizontalAdvance(num) + 12)
            total = fw.horizontalAdvance(wd) + 6 + pill_w
            x = max(0, (self.width() - total) // 2)
            h = self.height()
            if self.is_today:
                wcol = theme.LINK
            elif self.weekend:
                wcol = theme.mix(theme.MUTED, theme.BG, 0.25)
            else:
                wcol = theme.MUTED
            p.setFont(wf)
            p.setPen(QColor(wcol))
            p.drawText(QRect(x, 0, fw.horizontalAdvance(wd) + 1, h),
                       Qt.AlignmentFlag.AlignVCenter, wd)
            x += fw.horizontalAdvance(wd) + 6
            pill = QRectF(x, (h - 24) / 2, pill_w, 24)
            if self.is_today:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(theme.ACCENT))
                p.drawRoundedRect(pill, 12, 12)
                p.setPen(QColor(theme.text_on(theme.ACCENT)))
            else:
                p.setPen(QColor(theme.TEXT))
            p.setFont(nf)
            p.drawText(pill, Qt.AlignmentFlag.AlignCenter, num)
        finally:
            p.end()

    def mousePressEvent(self, e):
        e.accept()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.day)
        e.accept()


class HourGutter(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(GUTTER_W, 24 * HOUR_H)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setFont(_font(self.font(), 11))
        p.setPen(QColor(theme.MUTED))
        for h in range(1, 24):
            label = f"{h % 12 or 12} {'AM' if h < 12 else 'PM'}"
            p.drawText(QRect(0, h * HOUR_H - 8, GUTTER_W - 9, 16),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, label)
        p.end()


class NowLine(QWidget):
    """The current-time line (red, with a dot at its start), above the tiles."""

    H = 10

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFixedHeight(self.H)
        self._anim = None
        self.target_y = 0

    def center_y(self) -> int:
        return self.y() + self.H // 2

    def move_to(self, y: int, animate: bool) -> None:
        motion.stop(self._anim)
        self._anim = None
        self.target_y = y
        start = self.center_y()
        ms = motion.micro_duration(motion.WIN_NOW_MS) if animate and self.isVisible() else 0
        if start == y:
            ms = 0
        self._anim = motion.tween(self, start, y, ms,
                                  lambda v: None if _gone(self) else self.move(0, int(v) - self.H // 2),
                                  kind=motion.FADE)

    def animating(self) -> bool:
        return motion.alive(self._anim)

    def finish(self) -> None:
        motion.stop(self._anim)
        self._anim = None
        self.move(0, self.target_y - self.H // 2)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        c = QColor(NOW_LINE)
        mid = self.H / 2
        p.fillRect(QRectF(0, mid - 1, self.width(), 2), c)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawEllipse(QPointF(4.5, mid), 4.5, 4.5)
        p.end()


class TimelineColumn(QWidget):
    """One day's 24-hour column; meeting tiles are placed from layout.Block."""

    def __init__(self, day: date, blocks: list[layout.Block] | None = None,
                 is_today: bool = False, now_min: int | None = None,
                 ref: datetime | None = None, on_click=None, parent=None, color_for=None,
                 on_menu=None, weekend: bool = False, on_touch=None, style: str = "stripe"):
        super().__init__(parent)
        self.day, self.is_today, self.weekend = day, is_today, weekend
        self.setFixedHeight(24 * HOUR_H)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.chips: list[EventChip] = []
        self.blocks: list[layout.Block] = []
        self._sigs: list | None = None
        self._on_click, self._on_menu, self._on_touch = on_click, on_menu, on_touch
        self.now_min: int | None = None
        self.now_line: NowLine | None = None
        if blocks is not None and ref is not None:
            self.set_blocks(blocks, ref, color_for or _calendar_color, style)
        self.set_now(now_min, animate=False)

    def set_blocks(self, blocks: list[layout.Block], ref: datetime, color_for,
                   style: str) -> bool:
        """Show these blocks; tiles whose meeting and look did not change are kept (only
        moved if their place changed). False: nothing changed at all."""
        sigs = [(event_key(b.event), _content_sig(b.event, color_for(b.event), style),
                 b.start_min, b.end_min, b.col, b.ncols) for b in blocks]
        if sigs == self._sigs:
            return False
        old: dict = {}
        for chip in self.chips:
            old.setdefault(chip.signature, chip)
        chips = []
        for b, sig in zip(blocks, sigs):
            key = sig[:2]
            chip = old.pop(key, None)
            if chip is None:
                text, start, rng = _chip_label(b.event, ref)
                chip = EventChip(b.event, text, wrap=True, parent=self,
                                 color=color_for(b.event), kind=BLOCK, style=style,
                                 time_text=start, range_text=rng,
                                 meta=b.event.location or "")
                chip.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
                chip.signature = key
                if self._on_click is not None:
                    chip.clicked.connect(self._on_click)
                if self._on_menu is not None:
                    chip.menu_requested.connect(self._on_menu)
                if self._on_touch is not None:
                    chip.touched.connect(self._on_touch)
                chip.show()
            chip.block = b
            chips.append(chip)
        for chip in old.values():
            chip.hide()
            chip.deleteLater()
        self.chips, self.blocks, self._sigs = chips, list(blocks), sigs
        self._place()
        if self.now_line is not None:
            self.now_line.raise_()
        return True

    def tile_rect(self, b: layout.Block) -> QRect:
        """Where a block's tile goes: TILE_GAP_X between concurrent tiles, TILE_GAP_Y
        between back-to-back ones, at least one readable line tall."""
        avail = max(0, self.width() - TILE_MARGIN_L - TILE_MARGIN_R)
        n = max(1, b.ncols)
        gap = TILE_GAP_X if avail - TILE_GAP_X * (n - 1) >= 8 * n else 0
        cw = (avail - gap * (n - 1)) / n
        x0 = round(TILE_MARGIN_L + b.col * (cw + gap))
        x1 = round(TILE_MARGIN_L + b.col * (cw + gap) + cw)
        y0 = b.start_min * HOUR_H // 60 + 1
        y1 = b.end_min * HOUR_H // 60 - (TILE_GAP_Y - 1)
        return QRect(x0, y0, max(4, x1 - x0), max(10, y1 - y0))

    def _place(self) -> None:
        for chip in self.chips:
            if chip.block is not None:
                chip.setGeometry(self.tile_rect(chip.block))
        if self.now_line is not None:
            self.now_line.setFixedWidth(max(0, self.width()))

    def set_now(self, minute: int | None, animate: bool = True) -> None:
        self.now_min = minute
        if minute is None:
            if self.now_line is not None:
                self.now_line.hide()
            return
        if self.now_line is None:
            self.now_line = NowLine(self)
            self.now_line.setFixedWidth(max(0, self.width()))
            self.now_line.move(0, minute * HOUR_H // 60 - NowLine.H // 2)
            self.now_line.target_y = minute * HOUR_H // 60
            animate = False
        self.now_line.show()
        if self.children()[-1] is not self.now_line:
            self.now_line.raise_()
        self.now_line.move_to(minute * HOUR_H // 60, animate)

    def resizeEvent(self, e):
        self._place()
        super().resizeEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        if self.is_today:
            p.fillRect(self.rect(), QColor(TODAY_BG))
        elif self.weekend:
            p.fillRect(self.rect(), QColor(WEEKEND_BG))
        w = self.width()
        p.setPen(QColor(GRID_HALF))
        for h in range(24):
            y = h * HOUR_H + HOUR_H // 2
            p.drawLine(0, y, w, y)
        p.setPen(QColor(GRID))
        for h in range(24):
            p.drawLine(0, h * HOUR_H, w, h * HOUR_H)
        p.drawLine(0, 0, 0, self.height())
        p.end()


class AllDayStrip(QWidget):
    """A day's all-day meetings, as bars, top-aligned."""

    def __init__(self, on_click, on_menu, on_touch, parent=None):
        super().__init__(parent)
        self.chips: list[EventChip] = []
        self._sigs = None
        self._cb = (on_click, on_menu, on_touch)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(TILE_MARGIN_L, 0, TILE_MARGIN_R, 4)
        self._lay.setSpacing(CHIP_GAP)
        self._lay.addStretch(1)

    def set_events(self, events: list, color_for, style: str) -> bool:
        sigs = [(event_key(e), _content_sig(e, color_for(e), style)) for e in events]
        if sigs == self._sigs:
            return False
        for chip in self.chips:
            self._lay.removeWidget(chip)
            chip.hide()
            chip.deleteLater()
        self.chips = []
        on_click, on_menu, on_touch = self._cb
        for i, (ev, sig) in enumerate(zip(events, sigs)):
            chip = EventChip(ev, ev.title, parent=self, color=color_for(ev), kind=BAR,
                             style=style, range_text="All day")
            chip.signature = sig
            chip.clicked.connect(on_click)
            chip.menu_requested.connect(on_menu)
            chip.touched.connect(on_touch)
            self._lay.insertWidget(i, chip)
            self.chips.append(chip)
        self._sigs = sigs
        return True


class _EmptyNote(QLabel):
    def __init__(self, parent=None):
        super().__init__("No meetings", parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(f"color:{theme.MUTED};font-size:13px;background:transparent;")
        self.hide()


class TimeView(QWidget):
    """Day (1 column) or week (7 columns) view with an all-day strip on top."""

    event_clicked = pyqtSignal(object)
    event_menu_requested = pyqtSignal(object, QPoint)
    day_clicked = pyqtSignal(object)
    tile_touched = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.columns: list[TimelineColumn] = []
        self.headers: list[DayHeader] = []
        self.strips: list[AllDayStrip] = []
        self.allday_chips: list[EventChip] = []
        self._dates: list[date] = []
        self._key = None
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._outer.setSpacing(0)
        self._top = QWidget()
        self._outer.addWidget(self._top)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        vp = self._scroll.viewport()        # dark whatever the Windows app theme
        pal = vp.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor(theme.BG))
        vp.setPalette(pal)
        vp.setAutoFillBackground(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._outer.addWidget(self._scroll, 1)
        self._want_scroll: int | None = None
        self.color_for = _calendar_color
        self.tile_style = "stripe"
        self.empty_note = _EmptyNote(self)

    def set_data(self, dates: list[date], by_day: dict[date, list], ref: datetime,
                 reset_scroll: bool) -> None:
        prev = self._scroll.verticalScrollBar().value()
        today = agenda.local_date(ref)
        key = (tuple(dates), today)
        rebuilt = key != self._key
        if rebuilt:
            self._rebuild(dates, today)
            self._key = key
        n = layout.to_local(ref, ref)
        now_min = n.hour * 60 + n.minute
        style = self.tile_style
        for d, strip, col in zip(dates, self.strips, self.columns):
            evs = by_day.get(d, [])
            strip.set_events([e for e in evs if e.all_day], self.color_for, style)
            timed = [e for e in evs if not e.all_day]
            col.set_blocks(layout.day_blocks(timed, d, ref), ref, self.color_for, style)
            col.set_now(now_min if d == today else None, animate=not rebuilt)
        self.allday_chips = [c for s in self.strips for c in s.chips]
        self.empty_note.setVisible(len(dates) == 1 and not by_day.get(dates[0]))
        self._place_note()
        want = None
        if reset_scroll:
            hour = DEFAULT_SCROLL_HOUR
            if today in dates:
                hour = max(0, n.hour - 1)
            want = max(0, hour * HOUR_H - SCROLL_HEADROOM)
        elif rebuilt:
            want = prev
        if want is not None:
            self._want_scroll = want
            QTimer.singleShot(0, self._apply_scroll)

    def _rebuild(self, dates: list[date], today: date) -> None:
        self._dates = list(dates)
        self.columns, self.headers, self.strips = [], [], []
        top = QWidget()
        grid = QGridLayout(top)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(0)
        grid.setColumnMinimumWidth(0, GUTTER_W)
        grid.setColumnStretch(0, 0)
        spacer = QWidget()
        spacer.setFixedWidth(GUTTER_W)
        grid.addWidget(spacer, 0, 0)
        inner = QWidget()
        hbox = QHBoxLayout(inner)
        hbox.setContentsMargins(0, 0, 0, 0)
        hbox.setSpacing(0)
        hbox.addWidget(HourGutter())
        for i, d in enumerate(dates):
            weekend = d.weekday() >= 5
            head = DayHeader(d, d == today, f"{d:%a} {d.day}", weekend=weekend)
            head.clicked.connect(self.day_clicked)
            self.headers.append(head)
            grid.addWidget(head, 0, i + 1)
            grid.setColumnStretch(i + 1, 1)
            grid.setColumnMinimumWidth(i + 1, 0)
            strip = AllDayStrip(self.event_clicked.emit, self.event_menu_requested.emit,
                                self.tile_touched.emit)
            self.strips.append(strip)
            grid.addWidget(strip, 1, i + 1)
            col = TimelineColumn(d, is_today=d == today, weekend=weekend,
                                 on_click=self.event_clicked.emit,
                                 on_menu=self.event_menu_requested.emit,
                                 on_touch=self.tile_touched.emit)
            self.columns.append(col)
            hbox.addWidget(col, 1)
        old = self._top
        self._outer.replaceWidget(old, top)
        old.hide()
        old.deleteLater()
        self._top = top
        self._scroll.setWidget(inner)       # (deletes the previous one)
        self.empty_note.raise_()

    def tiles(self) -> list[EventChip]:
        """Every meeting widget, all-day first, then by start time (the build order)."""
        timed = [(c.block.start_min, i, c) for i, col in enumerate(self.columns)
                 for c in col.chips if c.block is not None]
        timed.sort(key=lambda t: (t[0], t[1]))
        return list(self.allday_chips) + [c for _s, _i, c in timed]

    def _apply_scroll(self) -> None:
        if self._want_scroll is not None:
            bar = self._scroll.verticalScrollBar()
            bar.setValue(self._want_scroll)
            if bar.value() == self._want_scroll:
                self._want_scroll = None

    def _place_note(self) -> None:
        if not self.empty_note.isHidden():
            self.empty_note.setGeometry(self._scroll.geometry())
            self.empty_note.raise_()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place_note()

    def showEvent(self, e):
        super().showEvent(e)
        QTimer.singleShot(0, self._apply_scroll)

    def scroll_value(self) -> int:
        return self._scroll.verticalScrollBar().value()

    def set_scroll_value(self, value: int) -> None:
        self._want_scroll = None
        self._scroll.verticalScrollBar().setValue(value)

    def finish_motion(self) -> None:
        for col in self.columns:
            if col.now_line is not None:
                col.now_line.finish()


class MoreChip(QLabel):
    """"+N more" in a month cell: opens that day."""

    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setFixedHeight(CHIP_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Show all meetings of this day")
        self._hover = 0.0
        self._anim = None
        self.hide()

    def _set(self, v) -> None:
        if not _gone(self):
            self._hover = float(v)
            self.update()

    def _to(self, target: float) -> None:
        motion.stop(self._anim)
        ms = motion.micro_duration(motion.FAST) if self.isVisible() else 0
        self._anim = motion.tween(self, self._hover, float(target), ms, self._set,
                                  kind=motion.FADE)

    def enterEvent(self, e):
        self._to(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._to(0.0)
        super().leaveEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self._hover > 0:
            c = QColor(theme.mix(theme.BG, "#ffffff", 0.10))
            c.setAlphaF(min(1.0, self._hover))
            path = QPainterPath()
            path.addRoundedRect(QRectF(self.rect()), 4, 4)
            p.fillPath(path, c)
        p.setFont(_font(self.font(), META_PX, QFont.Weight.DemiBold))
        p.setPen(QColor(theme.mix(theme.MUTED, theme.TEXT, 0.4 * self._hover)))
        p.drawText(self.rect().adjusted(6, 0, -4, 0), Qt.AlignmentFlag.AlignVCenter, self.text())
        p.end()

    def mousePressEvent(self, e):
        e.accept()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        e.accept()


class DayCell(QFrame):
    """A month day: a rounded card with as many meetings as fit and "+N more" (opens
    the day) for the rest. Click anywhere else: that day in the Day view."""

    clicked = pyqtSignal(object)

    def __init__(self, day: date, in_month: bool, is_today: bool, parent=None,
                 weekend: bool = False, on_click=None, on_menu=None, on_touch=None):
        super().__init__(parent)
        self.day = day
        self.dimmed = not in_month
        self.is_today, self.weekend = is_today, weekend
        self.all_chips: list[EventChip] = []
        self._shown = 0
        self._overflow = 0
        self._sigs = None
        self._cb = (on_click, on_menu, on_touch)
        self.setObjectName("cell")
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setMinimumHeight(CELL_MIN_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._more = MoreChip(self)
        self._more.clicked.connect(lambda: self.clicked.emit(self.day))
        self._hover = 0.0
        self._anim = None

    @property
    def chips(self) -> list[EventChip]:
        """The meetings shown (the ones that fit)."""
        return self.all_chips[:self._shown]

    @property
    def more_label(self) -> MoreChip | None:
        return self._more if self._overflow else None

    def set_events(self, events: list, ref: datetime, color_for=None,
                   style: str = "stripe") -> bool:
        color_for = color_for or _calendar_color
        sigs = [(event_key(e), _content_sig(e, color_for(e), style)) for e in events]
        if sigs == self._sigs:
            return False
        for chip in self.all_chips:
            chip.hide()
            chip.deleteLater()
        self.all_chips = []
        on_click, on_menu, on_touch = self._cb
        for ev, sig in zip(events, sigs):
            if ev.all_day:
                chip = EventChip(ev, ev.title, parent=self, color=color_for(ev), kind=BAR,
                                 style=style, range_text="All day")
            else:
                text, start, rng = _chip_label(ev, ref)
                chip = EventChip(ev, text, parent=self, color=color_for(ev), kind=CHIP,
                                 style=style, time_text=start, range_text=rng,
                                 meta=ev.location or "")
            chip.signature = sig
            if self.dimmed:
                chip.set_opacity(0.5 if not chip.declined else 0.3)
            if on_click is not None:
                chip.clicked.connect(on_click)
            if on_menu is not None:
                chip.menu_requested.connect(on_menu)
            if on_touch is not None:
                chip.touched.connect(on_touch)
            self.all_chips.append(chip)
        self._sigs = sigs
        self.fit()
        return True

    def fit(self) -> None:
        """Lay the chips out for the current size: all of them if they fit, else one
        row fewer than fit and "+N more"."""
        w, h = self.width(), self.height()
        row = CHIP_H + CHIP_GAP
        n_fit = max(0, (h - CELL_NUMBER_H - CELL_PAD + CHIP_GAP) // row)
        total = len(self.all_chips)
        if total <= n_fit:
            shown, overflow = total, 0
        else:
            shown = max(0, n_fit - 1)
            overflow = total - shown
        self._shown, self._overflow = shown, overflow
        cw = max(0, w - 2 * CELL_PAD)
        for i, chip in enumerate(self.all_chips):
            if i < shown:
                chip.setGeometry(CELL_PAD, CELL_NUMBER_H + i * row, cw, CHIP_H)
                chip.show()
            else:
                chip.hide()
        if overflow:
            self._more.setText(f"+{overflow} more")
            self._more.setGeometry(CELL_PAD, CELL_NUMBER_H + shown * row, cw, CHIP_H)
            self._more.show()
        else:
            self._more.hide()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.fit()

    # -- look -------------------------------------------------------------------------
    def base_color(self) -> str:
        if self.dimmed:
            return CELL_OUT_BG
        return CELL_WEEKEND_BG if self.weekend else CELL_BG

    def hover_level(self) -> float:
        return self._hover

    def _set(self, v) -> None:
        if not _gone(self):
            self._hover = float(v)
            self.update()

    def _to(self, target: float) -> None:
        motion.stop(self._anim)
        self._anim = None
        if self._hover == target:
            return
        ms = motion.micro_duration(motion.FAST) if self.isVisible() else 0
        self._anim = motion.tween(self, self._hover, float(target), ms, self._set,
                                  kind=motion.FADE)

    def enterEvent(self, e):
        self._to(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._to(0.0)
        super().leaveEvent(e)

    def hideEvent(self, e):
        motion.stop(self._anim)
        self._anim = None
        self._hover = 0.0
        super().hideEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            bg = theme.mix(self.base_color(), "#ffffff", 0.045 * self._hover)
            p.setBrush(QColor(bg))
            p.setPen(QColor(theme.ACCENT) if self.is_today else Qt.PenStyle.NoPen)
            p.drawRoundedRect(r, CELL_RADIUS, CELL_RADIUS)
            nf = _font(self.font(), 12, QFont.Weight.DemiBold if self.is_today
                       else QFont.Weight.Normal)
            num = str(self.day.day)
            box = QRectF(CELL_PAD, 2, max(22, QFontMetrics(nf).horizontalAdvance(num) + 10), 19)
            p.setFont(nf)
            if self.is_today:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(theme.ACCENT))
                p.drawRoundedRect(box, 9.5, 9.5)
                p.setPen(QColor(theme.text_on(theme.ACCENT)))
                p.drawText(box, Qt.AlignmentFlag.AlignCenter, num)
            else:
                p.setPen(QColor(theme.MUTED if self.dimmed else theme.TEXT))
                p.drawText(box.adjusted(3, 0, 0, 0),
                           Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, num)
        finally:
            p.end()

    def mousePressEvent(self, e):
        e.accept()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.day)
        e.accept()


class MonthView(QWidget):
    event_clicked = pyqtSignal(object)
    event_menu_requested = pyqtSignal(object, QPoint)
    day_clicked = pyqtSignal(object)
    tile_touched = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cells: dict[date, DayCell] = {}
        self.weekday_labels: list[QLabel] = []
        self._key = None
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(MONTH_GAP)
        self.color_for = _calendar_color
        self.tile_style = "stripe"

    def set_data(self, dates: list[date], month: int, by_day: dict[date, list], ref: datetime):
        today = agenda.local_date(ref)
        key = (tuple(dates), month, today)
        if key != self._key:
            _clear(self._grid)
            self.cells, self.weekday_labels = {}, []
            for c in range(7):
                d = dates[c]
                lab = QLabel(d.strftime("%a"))
                lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
                color = theme.mix(theme.MUTED, theme.BG, 0.25) if d.weekday() >= 5 \
                    else theme.MUTED
                lab.setStyleSheet(f"color:{color};padding:2px;font-size:11px;"
                                  "font-weight:500;")
                self._grid.addWidget(lab, 0, c)
                self._grid.setColumnStretch(c, 1)
                self.weekday_labels.append(lab)
            for i, d in enumerate(dates):
                cell = DayCell(d, d.month == month, d == today, weekend=d.weekday() >= 5,
                               on_click=self.event_clicked.emit,
                               on_menu=self.event_menu_requested.emit,
                               on_touch=self.tile_touched.emit)
                cell.clicked.connect(self.day_clicked)
                self._grid.addWidget(cell, 1 + i // 7, i % 7)
                self.cells[d] = cell
            for r in range(1, 7):
                self._grid.setRowStretch(r, 1)
            self._key = key
            self._grid.activate()
        for d, cell in self.cells.items():
            cell.set_events(by_day.get(d, []), ref, self.color_for, self.tile_style)

    def tiles(self) -> list[DayCell]:
        """The build order: the day cells, in date order."""
        return [self.cells[d] for d in sorted(self.cells)]


class EventDetails(QDialog):
    """Read-only details for one event. (Hide and Color are on the event's right-click
    menu in the window, not here.) v0.11.0: its lines build in when it opens (micro-
    animations), like the popup's details panel."""

    def __init__(self, event, ref: datetime, parent=None, settings=None):
        super().__init__(parent)
        self.event = event
        self.settings = settings
        self._build: cards.CardBuild | None = None
        self.setWindowTitle(event.title or "Event")
        self.setMinimumWidth(340)
        self.setStyleSheet(f"QDialog{{background:{theme.BG};}}"
                           f"QLabel{{color:{theme.TEXT};}}"
                           f"QTextBrowser{{background:{theme.BG};color:{theme.TEXT};"
                           f"border:1px solid {theme.BORDER};border-radius:{theme.RADIUS_SMALL}px;}}"
                           f"QPushButton{{background:{theme.BORDER};color:{theme.TEXT};"
                           f"border:none;padding:5px 12px;border-radius:{theme.RADIUS_SMALL}px;}}"
                           f"QPushButton:hover{{background:#4a4d51;}}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)
        self.title_label = QLabel(event.title)
        self.title_label.setTextFormat(Qt.TextFormat.PlainText)
        self.title_label.setWordWrap(True)
        self.title_label.setStyleSheet("font-size:16px;font-weight:bold;")
        self.time_label = QLabel(layout.format_range(event, ref))
        self.time_label.setTextFormat(Qt.TextFormat.PlainText)
        self.time_label.setWordWrap(True)
        self.time_label.setStyleSheet(f"color:{theme.MUTED};")
        self.calendar_label = QLabel()
        self.calendar_label.setTextFormat(Qt.TextFormat.RichText)
        self.update_color()
        lay.addWidget(self.title_label)
        lay.addWidget(self.time_label)
        self.join_button = None
        url = event.meeting_url
        if safe_http_url(url) is not None:     # same check as the click handler
            self.join_button = QPushButton(meeting_label(url))
            self.join_button.setObjectName("join")
            self.join_button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.join_button.setStyleSheet(
                f"QPushButton{{background:{theme.LINK};color:#0b0d10;font-weight:bold;"
                f"padding:7px 16px;border:none;border-radius:{theme.RADIUS_SMALL}px;}}"
                "QPushButton:hover{background:#a9c8ff;}")
            self.join_button.setToolTip(plain_tip(url))
            self.join_button.clicked.connect(lambda _c=False, u=url: self.open_link(u))
            lay.addWidget(self.join_button)
        lay.addWidget(self.calendar_label)
        if getattr(event, "declined", False):
            declined = QLabel("You declined this meeting.")
            declined.setStyleSheet(f"color:{theme.MUTED};")
            lay.addWidget(declined)
        self.location_label = None
        if event.location:
            self.location_label = QLabel(event.location)
            self.location_label.setTextFormat(Qt.TextFormat.PlainText)
            self.location_label.setWordWrap(True)
            lay.addWidget(self.location_label)
        self.link_label = self.join_button   # kept for older callers/tests
        self.description_box = None
        description = html_to_text(event.description or "")
        if description:
            # Plain text with clickable http(s) links; see linktext.LinkText.
            self.description_box = LinkText(description)
            self.description_box.setMinimumHeight(90)
            lay.addWidget(self.description_box, 1)

    def build_lines(self) -> list[QWidget]:
        lay = self.layout()
        out = []
        for i in range(lay.count()):
            w = lay.itemAt(i).widget()
            if w is not None and not w.isHidden():
                out.append(w)
        return out

    def prepare_build(self) -> None:
        """Before it is first shown: its lines out of place (micro-animations only)."""
        self.finish_build()
        if not motion.micro_enabled():
            return
        self._build = cards.CardBuild(self, motion.PANEL_BUILD_OFFSET_PX, 1.0,
                                      motion.WIN_DETAILS_START_MS,
                                      motion.PANEL_BUILD_STAGGER_MS, motion.PANEL_BUILD_MS)
        self._build.prepare(self.build_lines())

    def content_build(self) -> cards.CardBuild | None:
        return self._build

    def building(self) -> bool:
        return self._build is not None and self._build.running()

    def finish_build(self) -> None:
        b, self._build = self._build, None
        if b is not None and not _gone(b):
            b.finish()
            b.deleteLater()

    def nativeEvent(self, event_type, message):
        # v0.11.0: Windows' erase is answered with the dark background (no white frame
        # when the window is shown or uncovered). Never the base class's nativeEvent:
        # its (False, None) crashes Qt (see nativebg); always a (bool, int).
        return nativebg.native_event(event_type, message, theme.BG)

    def showEvent(self, e):
        super().showEvent(e)
        winframe.apply_dwm_frame(self)
        if self._build is not None:
            QTimer.singleShot(0, lambda: None if self._build is None else self._build.start())

    def hideEvent(self, e):
        self.finish_build()
        super().hideEvent(e)

    def update_color(self) -> None:
        name = calendar_label(self.event.calendar_name)
        # An email as the calendar name says nothing: no calendar line (nor swatch).
        self.calendar_label.setVisible(bool(name))
        if not name:
            self.calendar_label.setText("")
            return
        color = theme.effective_color(self.event, self.settings)
        self.calendar_label.setText(
            f'<span style="color:{color};">&#9632;</span> ' + _esc(name))

    @staticmethod
    def open_link(url: str) -> None:
        if safe_http_url(url) is not None:
            QDesktopServices.openUrl(QUrl(url))


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


class _Snapshot(QWidget):
    """The old content during a switch / step: a picture of it, sliding away and
    fading out over the new content (never takes a click)."""

    def __init__(self, parent, pixmap: QPixmap):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._pm = pixmap
        self._op, self._dx = 1.0, 0.0

    def state(self) -> tuple[float, float]:
        return self._op, self._dx

    def set_state(self, opacity: float, dx: float) -> None:
        self._op, self._dx = motion.clamp(opacity, 0.0, 1.0), float(dx)
        self.update()

    def paintEvent(self, _e):
        if self._op <= 0:
            return
        p = QPainter(self)
        p.setOpacity(self._op)
        p.drawPixmap(QPointF(self._dx, 0), self._pm)
        p.end()


def _sign(x) -> int:
    return (x > 0) - (x < 0)


TOOLBAR_STYLE = (
    f"QPushButton{{background:{theme.BORDER};color:{theme.TEXT};border:none;"
    f"padding:5px 12px;border-radius:{theme.RADIUS_SMALL}px;}}"
    "QPushButton:hover{background:#4a4d51;}"
    "QPushButton#nav{padding:3px 0px;font-size:17px;min-width:32px;max-width:32px;}"
    "QFrame#seg{background:#2a2c30;border-radius:8px;}"
    f"#seg QPushButton{{background:transparent;color:{theme.MUTED};padding:4px 14px;"
    "border-radius:6px;}"
    "#seg QPushButton:hover{background:#34373c;color:#dfe1e5;}"
    f"#seg QPushButton:checked{{background:{theme.BG_NEXT};color:{theme.TEXT};}}")


class CalendarWindow(QMainWindow):
    meetings_changed = pyqtSignal()   # a meeting was hidden or recolored here

    def __init__(self, cache, settings, qsettings: QSettings | None = None, parent=None):
        super().__init__(parent)
        self.cache, self.settings = cache, settings
        self.now = lambda: datetime.now().astimezone()
        self.tray_geometry = lambda: None   # run.py: the tray icon's rect
        self._qs = qsettings or QSettings(
            str(data_dir() / "window.ini"), QSettings.Format.IniFormat)
        self.view = "week"
        self.anchor: date = agenda.local_date(self.now())
        self.details: EventDetails | None = None
        self._builds: list[cards.CardBuild] = []
        self._snapshot: _Snapshot | None = None
        self._snap_anim = None
        self._frame_hwnd = 0
        self.setWindowTitle("SmartCal")
        self.resize(1000, 700)
        self.setStyleSheet(f"QMainWindow,#central{{background:{theme.BG};}}"
                           f"QLabel{{color:{theme.TEXT};}}" + TOOLBAR_STYLE)
        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 8, 10, 10)
        root.setSpacing(6)
        self.toolbar = QWidget()
        self.toolbar.setObjectName("toolbar")
        bar = QHBoxLayout(self.toolbar)
        bar.setContentsMargins(0, 0, 0, 2)
        bar.setSpacing(6)
        self.prev_btn, self.today_btn, self.next_btn = (
            QPushButton("‹"), QPushButton("Today"), QPushButton("›"))
        for b in (self.prev_btn, self.next_btn):
            b.setObjectName("nav")
        self.prev_btn.clicked.connect(lambda: self.step(-1))
        self.next_btn.clicked.connect(lambda: self.step(1))
        self.today_btn.clicked.connect(self.go_today)
        for b in (self.prev_btn, self.today_btn, self.next_btn):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            bar.addWidget(b)
        self.title_label = QLabel()
        self.title_label.setStyleSheet("font-size:16px;font-weight:bold;padding-left:10px;")
        bar.addWidget(self.title_label, 1)
        seg = QFrame()
        seg.setObjectName("seg")
        seg_lay = QHBoxLayout(seg)
        seg_lay.setContentsMargins(3, 3, 3, 3)
        seg_lay.setSpacing(2)
        self.view_buttons: dict[str, QPushButton] = {}
        group = QButtonGroup(self)
        group.setExclusive(True)
        for name in VIEWS:
            b = QPushButton(name.capitalize())
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _c=False, n=name: self.set_view(n))
            group.addButton(b)
            seg_lay.addWidget(b)
            self.view_buttons[name] = b
        bar.addWidget(seg)
        root.addWidget(self.toolbar)
        self.unsynced_label = QLabel()
        self.unsynced_label.setTextFormat(Qt.TextFormat.PlainText)
        self.unsynced_label.setWordWrap(True)
        self.unsynced_label.setStyleSheet(f"color:{theme.WARN};padding:2px 2px;")
        self.unsynced_label.hide()
        root.addWidget(self.unsynced_label)
        self.notice_label = QLabel()
        self.notice_label.setObjectName("notice")
        self.notice_label.setTextFormat(Qt.TextFormat.PlainText)
        self.notice_label.setStyleSheet(f"color:{theme.MUTED};padding:2px 2px;")
        self.notice_label.hide()
        root.addWidget(self.notice_label)
        self.stack = QStackedWidget()
        self.time_view = TimeView()
        self.month_view = MonthView()
        self.time_view.color_for = self.month_view.color_for = self._color_for
        self.stack.addWidget(self.time_view)
        self.stack.addWidget(self.month_view)
        root.addWidget(self.stack, 1)
        for v in (self.time_view, self.month_view):
            v.event_clicked.connect(self.show_details)
            v.event_menu_requested.connect(self.show_event_menu)
            v.day_clicked.connect(self._open_day)
            v.tile_touched.connect(self._tile_touched)
        self._menu = None
        self._update_tooltips()
        # v0.11.0: opened from hidden on the real display, the window starts fully
        # transparent (uniform window opacity, as the popup) and fades in once its first
        # frame is painted: Windows never shows its white background first.
        self._reveal_pending = False
        self._reveal_anim = None
        self._reveal_timer = QTimer(self)
        self._reveal_timer.setSingleShot(True)
        self._reveal_timer.setInterval(motion.SHOW_START_FALLBACK_MS)
        self._reveal_timer.timeout.connect(self._reveal)
        central.installEventFilter(self)

        self._restore_geometry()
        self._pending_reset = True
        self._stale = True
        self._timer = QTimer(self)
        self._timer.setInterval(60_000)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    # -- geometry -----------------------------------------------------------------
    def _restore_geometry(self) -> None:
        geom = self._qs.value("window/geometry")
        ok = isinstance(geom, (QByteArray, bytes)) and self.restoreGeometry(geom)
        self._needs_default_place = not ok or not self._on_a_screen()
        if self._needs_default_place:
            self._center_default()   # until the first show places it near the tray

    def _on_a_screen(self) -> bool:
        frame = self.frameGeometry()
        return any(s.availableGeometry().intersects(frame) for s in QGuiApplication.screens())

    def _center_default(self) -> None:
        self.resize(1000, 700)
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            self.move(area.center() - self.rect().center())

    def _save_geometry(self) -> None:
        self._qs.setValue("window/geometry", self.saveGeometry())
        self._qs.sync()

    def save_state(self) -> None:
        """Flush the window geometry to disk (used before a forced exit)."""
        self._save_geometry()

    # -- navigation -----------------------------------------------------------
    def set_view(self, view: str) -> None:
        if view not in VIEWS:
            raise ValueError(f"unknown view {view!r}")
        direction = _sign(VIEWS.index(view) - VIEWS.index(self.view))
        self._navigate(lambda: setattr(self, "view", view), direction, "switch")

    def show_view(self, view: str) -> None:
        self.set_view(view)
        first = self._needs_default_place   # first open, nothing saved: next to the tray
        self._needs_default_place = False
        if first:
            placement.place_window_near_tray(self, self.tray_geometry(), DEFAULT_SIZE)
        if not self.isVisible() and winframe.native_windows():
            self._stop_reveal()
            self._reveal_pending = True
            self.setWindowOpacity(0.0)
            self._reveal_timer.start()      # (no paint seen: shown anyway)
        if self.isMinimized():
            self.showNormal()
        self.show()
        if first:   # again, now that the real frame and minimum size are known
            placement.place_window_near_tray(self, self.tray_geometry(), self.size())
        self.raise_()
        self.activateWindow()

    def go_to(self, d: date) -> None:
        def change():
            self.anchor = d
        self._navigate(change, _sign((d - self.anchor).days), "step")

    def go_today(self) -> None:
        self.go_to(agenda.local_date(self.now()))

    def step(self, direction: int) -> None:
        def change():
            if self.view == "month":
                self.anchor = layout.shift_month(self.anchor, direction)
            else:
                self.anchor += timedelta(days=direction * (7 if self.view == "week" else 1))
        self._navigate(change, _sign(direction), "step")

    def _open_day(self, d: date) -> None:
        direction = _sign(VIEWS.index("day") - VIEWS.index(self.view))

        def change():
            self.anchor = d
            self.view = "day"
        self._navigate(change, direction or _sign((d - self.anchor).days), "switch")

    def _navigate(self, change, direction: int, kind: str) -> None:
        """Apply `change` (the view and/or the date) and show it. On screen, with
        animations: the old content slides away (`direction`: 1 = it leaves to the
        left, the new one comes from the right) and fades out while the new one builds
        in (see motion.py)."""
        before = (self.view, tuple(self.visible_dates()))
        snap = None
        if self.isVisible() and direction and motion.animations_enabled():
            self.finish_animations()
            snap = self._grab_content()
        change()
        self._update_tooltips()
        self.refresh(reset_scroll=True)
        if snap is not None and (self.view, tuple(self.visible_dates())) != before:
            self._play_transition(snap, direction, kind)

    def _update_tooltips(self) -> None:
        unit = {"day": "day", "week": "week", "month": "month"}[self.view]
        self.prev_btn.setToolTip(f"Previous {unit}")
        self.next_btn.setToolTip(f"Next {unit}")

    # -- rendering --------------------------------------------------------------
    def visible_dates(self) -> list[date]:
        if self.view == "month":
            return layout.month_grid(self.anchor.year, self.anchor.month)
        if self.view == "week":
            return layout.week_dates(self.anchor)
        return [self.anchor]

    def _load(self, dates: list[date], ref: datetime) -> list:
        first, last = layout.day_bounds(dates[0], ref)[0], layout.day_bounds(dates[-1], ref)[1]
        # Declined meetings stay on the grid, drawn dimmed and struck through.
        return agenda.visible_events(
            self.cache, self.settings,
            self.cache.events_between(first - timedelta(days=1), last + timedelta(days=1)),
            include_declined=True, hide_user_hidden=True)

    def _color_for(self, event) -> str:
        return theme.effective_color(event, self.settings)

    def tile_style(self) -> str:
        style = getattr(self.settings, "tile_style", "stripe")
        return style if style in theme.TILE_STYLES else "stripe"

    def refresh(self, reset_scroll: bool = False) -> None:
        """Re-read and show. Diffed: what did not change keeps its widgets (a minute
        tick or a sync that changed nothing changes nothing on screen but the time
        line). While hidden, just remember to do it on show."""
        if reset_scroll:
            self._pending_reset = True
        title = layout.title_for(self.view, self.anchor)
        if self.title_label.text() != title:
            self.title_label.setText(title)
        if not self.view_buttons[self.view].isChecked():
            self.view_buttons[self.view].setChecked(True)
        self._update_unsynced(self.visible_dates(), self.now())
        if not self.isVisible():
            self._stale = True
            return
        self._stale = False
        reset_scroll = reset_scroll or self._pending_reset
        self._pending_reset = False
        ref = self.now()
        dates = self.visible_dates()
        by_day = layout.events_by_day(self._load(dates, ref), dates, ref)
        style = self.tile_style()
        self.time_view.tile_style = self.month_view.tile_style = style
        if self.view == "month":
            self.month_view.set_data(dates, self.anchor.month, by_day, ref)
            if self.stack.currentWidget() is not self.month_view:
                self.stack.setCurrentWidget(self.month_view)
                self.month_view.layout().activate()
                for cell in self.month_view.cells.values():
                    cell.fit()
        else:
            self.time_view.set_data(dates, by_day, ref, reset_scroll)
            if self.stack.currentWidget() is not self.time_view:
                self.stack.setCurrentWidget(self.time_view)

    def _update_unsynced(self, dates: list[date], ref: datetime) -> None:
        """Warn when the shown days reach past the synced range (they would look empty)."""
        first, last = synced_days(ref)
        outside = dates[0] < first or dates[-1] > last
        if outside:
            text = (f"Not synced for these dates. SmartCal keeps events from "
                    f"{layout.month_short(first)} {first.day} to {layout.month_short(last)} "
                    f"{last.day}, {last.year}.")
            if self.unsynced_label.text() != text:
                self.unsynced_label.setText(text)
        if self.unsynced_label.isHidden() == outside:
            self.unsynced_label.setVisible(outside)

    def unsynced_shown(self) -> bool:
        return not self.unsynced_label.isHidden()

    def _tick(self) -> None:
        if self.isVisible():
            self.refresh()

    # -- motion (v0.11.0) ----------------------------------------------------------------
    def builds(self) -> list[cards.CardBuild]:
        self._builds = [b for b in self._builds if not _gone(b) and b.running()]
        return list(self._builds)

    def building(self) -> bool:
        return bool(self.builds())

    def transitioning(self) -> bool:
        return self._snapshot is not None

    def animating(self) -> bool:
        return self.building() or self.transitioning()

    def snapshot_widget(self) -> _Snapshot | None:
        return self._snapshot

    def current_tiles(self) -> list[QWidget]:
        """The widgets that build in: meeting tiles (Day / Week) or day cells (Month)."""
        if self.view == "month":
            return self.month_view.tiles()
        return self.time_view.tiles()

    def _grab_content(self) -> tuple[QPixmap, QRect]:
        st = self.stack
        dpr = st.devicePixelRatioF()
        pm = QPixmap(max(1, round(st.width() * dpr)), max(1, round(st.height() * dpr)))
        pm.setDevicePixelRatio(dpr)
        pm.fill(QColor(theme.BG))
        st.render(pm)
        return pm, st.geometry()

    def _start_build(self, widgets, *, offset: int, scale: float, start: int, stagger: int,
                     span: int, item: int, offset_x: int = 0) -> None:
        widgets = [w for w in widgets if not _gone(w)]
        if not widgets:
            return
        b = cards.CardBuild(self, offset, scale, start, stagger, item,
                            offset_x=offset_x, max_span_ms=span)
        b.prepare(widgets)
        self._builds.append(b)
        QTimer.singleShot(0, lambda b=b: None if _gone(b) else b.start())

    def _play_open(self) -> None:
        """Shown from hidden: the toolbar, then the day headers, then the tiles build in
        (micro-animations; the window itself opens the Windows way)."""
        self.finish_animations()
        if not motion.micro_enabled():
            return
        self._start_build([self.toolbar], offset=motion.WIN_TOOLBAR_OFFSET_PX, scale=1.0,
                          start=0, stagger=0, span=0, item=motion.WIN_TOOLBAR_MS)
        heads = self.time_view.headers if self.view != "month" else self.month_view.weekday_labels
        self._start_build(heads, offset=motion.WIN_HEADER_OFFSET_PX, scale=1.0,
                          start=motion.WIN_HEADER_START_MS,
                          stagger=motion.WIN_HEADER_STAGGER_MS,
                          span=6 * motion.WIN_HEADER_STAGGER_MS, item=motion.WIN_HEADER_MS)
        self._start_build(self.current_tiles(), offset=motion.WIN_TILE_OFFSET_PX,
                          scale=motion.WIN_TILE_SCALE, start=motion.WIN_TILE_START_MS,
                          stagger=motion.WIN_TILE_STAGGER_MS, span=motion.WIN_TILE_SPAN_MS,
                          item=motion.WIN_TILE_MS)

    def _play_transition(self, snap, direction: int, kind: str) -> None:
        pm, rect = snap
        ms = motion.duration(motion.WIN_SWITCH_MS if kind == "switch" else motion.WIN_NAV_MS)
        slide = motion.WIN_SWITCH_SLIDE_PX if kind == "switch" else motion.WIN_NAV_SLIDE_PX
        if not ms:
            return
        overlay = _Snapshot(self.centralWidget(), pm)
        # Over the new content, where it is now (the "not synced" line above it may
        # have appeared or gone: the picture never covers that line).
        overlay.setGeometry(QRect(self.stack.geometry().topLeft(), rect.size()))
        overlay.show()
        overlay.raise_()
        self._snapshot = overlay

        def frame(t, overlay=overlay) -> None:
            if _gone(overlay):
                return
            e = motion.ease(motion.FADE, t)
            overlay.set_state(1.0 - e, -direction * slide * e)
        self._snap_anim = motion.tween(self, 0.0, 1.0, ms, frame, self._end_transition,
                                       kind=motion.LINEAR)
        if motion.micro_enabled():
            widgets = list(self.current_tiles())
            if kind == "step" and self.view != "month":
                widgets = list(self.time_view.headers) + widgets
            self._start_build(widgets, offset=0,
                              scale=motion.WIN_SWITCH_SCALE if kind == "switch" else 1.0,
                              start=motion.WIN_REBUILD_START_MS,
                              stagger=motion.WIN_REBUILD_STAGGER_MS,
                              span=motion.WIN_REBUILD_SPAN_MS, item=motion.WIN_REBUILD_MS,
                              offset_x=direction * slide)

    def _end_transition(self) -> None:
        self._snap_anim = None
        overlay, self._snapshot = self._snapshot, None
        if overlay is not None and not _gone(overlay):
            overlay.hide()
            overlay.deleteLater()

    def finish_animations(self) -> None:
        """Everything in its final state now: no build, no snapshot (safe to repeat)."""
        anim, self._snap_anim = self._snap_anim, None
        motion.stop(anim)
        self._end_transition()
        builds, self._builds = self._builds, []
        for b in builds:
            if not _gone(b):
                b.finish()
                b.deleteLater()
        self.time_view.finish_motion()

    def eventFilter(self, obj, e) -> bool:
        if self._reveal_pending and e.type() == QEvent.Type.Paint and \
                obj is self.centralWidget():
            QTimer.singleShot(0, self._reveal)  # right after this frame is on screen
        return False

    def revealing(self) -> bool:
        return self._reveal_pending or motion.alive(self._reveal_anim)

    def _reveal(self) -> None:
        """The first frame is painted: fade the window in (at once with animations
        off)."""
        if not self._reveal_pending:
            return
        self._reveal_pending = False
        self._reveal_timer.stop()
        motion.stop(self._reveal_anim)
        self._reveal_anim = motion.tween(
            self, 0.0, 1.0, motion.duration(motion.WIN_REVEAL_MS),
            lambda v: None if _gone(self) else self.setWindowOpacity(float(v)),
            kind=motion.FADE)

    def _stop_reveal(self) -> None:
        self._reveal_pending = False
        self._reveal_timer.stop()
        motion.stop(self._reveal_anim)
        self._reveal_anim = None
        if self.windowOpacity() != 1.0:
            self.setWindowOpacity(1.0)

    def _tile_touched(self, w) -> None:
        """A tile is pressed mid-build: it (and its month cell) lands now."""
        parent = w.parentWidget() if not _gone(w) else None
        for b in self.builds():
            b.finish_widget(w)
            if isinstance(parent, DayCell):
                b.finish_widget(parent)

    def resizeEvent(self, e):
        if self._snapshot is not None:      # the picture no longer matches
            motion.stop(self._snap_anim)
            self._snap_anim = None
            self._end_transition()
        super().resizeEvent(e)

    # -- details -----------------------------------------------------------------
    def show_details(self, event) -> None:
        if self.details is not None:
            self.details.close()
            self.details.deleteLater()
        self.details = EventDetails(event, self.now(), self, settings=self.settings)
        placement.center_over(self.details, self)   # opened from the window: over it
        self.details.prepare_build()
        self.details.show()
        self.details.raise_()

    # -- hide / color (SmartCal-local) ---------------------------------------------
    def menu(self):
        """The open right-click menu of an event (None until the first one)."""
        return self._menu

    def show_event_menu(self, event, global_pos: QPoint) -> None:
        """Right-click on an event: Hide / Hide series / Color ▸, as in the popup."""
        meeting_menus.discard(self._menu)
        self._menu = meeting_menus.context_menu(
            self, event, self.settings.event_color(event),
            lambda scope, e=event: self.hide_meeting(e, scope),
            lambda color, e=event: self.color_meeting(e, color),
            self.tile_style(), self.set_tile_style)
        meeting_menus.delete_when_closed(self._menu)
        self._menu.popup(global_pos)

    def show_notice(self, text: str) -> None:
        if self.notice_label.text() != text:
            self.notice_label.setText(text)
        if self.notice_label.isHidden() == bool(text):
            self.notice_label.setVisible(bool(text))

    def notice_text(self) -> str:
        return self.notice_label.text() if not self.notice_label.isHidden() else ""

    def hide_meeting(self, event, scope: str) -> None:
        try:
            self.settings.hide_event(event, scope)
        except OSError:                 # rolled back by Settings; say so, change nothing
            self.show_notice(meeting_menus.SAVE_FAILED)
            return
        if self.details is not None and self.settings.is_event_hidden(self.details.event):
            self._close_details()       # its meeting is gone
        self.show_notice(meeting_menus.HIDDEN_NOTICE)
        self.refresh()
        self.meetings_changed.emit()

    def set_tile_style(self, style: str) -> None:
        """Color style ▸ in the right-click menu: the popup's rows and (v0.11.0) this
        window's tiles change; meetings_changed refreshes every view."""
        try:
            self.settings.set_tile_style(style)
        except OSError:
            self.show_notice(meeting_menus.SAVE_FAILED)
            return
        self.refresh()
        self.meetings_changed.emit()

    def color_meeting(self, event, color) -> None:
        try:
            if color is None:
                self.settings.clear_event_color(event)
            else:
                self.settings.set_event_color(event, color)
        except OSError:
            self.show_notice(meeting_menus.SAVE_FAILED)
            return
        if self.details is not None and color_key(self.details.event) == color_key(event):
            self.details.update_color()
        self.refresh()
        self.meetings_changed.emit()

    def nativeEvent(self, event_type, message):
        # v0.11.0: Windows' erase is answered with the dark background (no white frame
        # when the window is shown or uncovered). Never the base class's nativeEvent:
        # its (False, None) crashes Qt (see nativebg); always a (bool, int).
        return nativebg.native_event(event_type, message, theme.BG)

    def showEvent(self, e):
        super().showEvent(e)
        hwnd = int(self.winId()) if winframe.native_windows() else 0
        if hwnd and hwnd != self._frame_hwnd:
            self._frame_hwnd = hwnd
            winframe.apply_dwm_frame(self)      # rounded, with the accent edge
        self.refresh()
        if not e.spontaneous():                 # opened (not restored from minimized)
            self._play_open()

    def _close_details(self) -> None:
        if self.details is not None:
            self.details.close()

    def hideEvent(self, e):
        self.finish_animations()
        self._stop_reveal()
        self._save_geometry()
        self._close_details()
        self.show_notice("")
        super().hideEvent(e)

    def closeEvent(self, e):
        self.finish_animations()
        self._save_geometry()
        self._close_details()
        super().closeEvent(e)
