"""Day / Week / Month window. Read-only; reads only from the cache."""
from __future__ import annotations

import html
from datetime import date, datetime, timedelta

from PyQt6.QtCore import QByteArray, QRect, QSettings, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices, QGuiApplication, QPainter
from PyQt6.QtWidgets import (QButtonGroup, QDialog, QFrame, QGraphicsOpacityEffect, QGridLayout, QHBoxLayout, QLabel,
                             QMainWindow, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy,
                             QStackedWidget, QVBoxLayout, QWidget)

from .. import agenda
from ..config import data_dir
from . import layout, theme

HOUR_H = 48
GUTTER_W = 52
VIEWS = ("day", "week", "month")
DEFAULT_SCROLL_HOUR = 7

GRID = "#2e3033"
TODAY_BG = "#262d3a"
NOW_LINE = "#ea4335"


def _text_on(color: str) -> str:
    c = QColor(color)
    lum = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
    return "#202124" if lum > 150 else "#ffffff"


def plain_tip(text: str) -> str:
    """A tooltip string Qt will not interpret as rich text markup."""
    return f"<p style='white-space:pre'>{html.escape(text)}</p>"


def _clear(lay) -> None:
    while lay.count():
        item = lay.takeAt(0)
        w = item.widget()
        if w is not None:
            w.hide()
            w.deleteLater()


class EventChip(QLabel):
    """A colored, clickable label for one event."""

    clicked = pyqtSignal(object)

    def __init__(self, event, text: str, wrap: bool = False, parent=None):
        super().__init__(text, parent)
        self.event = event
        color = theme.stripe_color(event.color)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setWordWrap(wrap)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.setStyleSheet(f"background:{color};color:{_text_on(color)};border-radius:3px;"
                           f"padding:1px 4px;font-size:11px;")
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(plain_tip(event.title))

    def mousePressEvent(self, e):
        e.accept()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit(self.event)
        e.accept()


class DayHeader(QLabel):
    clicked = pyqtSignal(object)

    def __init__(self, day: date, is_today: bool, text: str, parent=None):
        super().__init__(text, parent)
        self.day = day
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setStyleSheet(f"padding:4px;font-weight:bold;"
                           f"color:{theme.LINK if is_today else theme.TEXT};")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

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
        p.setPen(QColor(theme.MUTED))
        for h in range(1, 24):
            label = f"{h % 12 or 12} {'AM' if h < 12 else 'PM'}"
            p.drawText(QRect(0, h * HOUR_H - 8, GUTTER_W - 6, 16),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, label)


class TimelineColumn(QWidget):
    """One day's 24-hour column; event blocks are positioned from layout.Block."""

    def __init__(self, day: date, blocks: list[layout.Block], is_today: bool,
                 now_min: int | None, ref: datetime, on_click, parent=None):
        super().__init__(parent)
        self.day, self.blocks, self.is_today, self.now_min = day, blocks, is_today, now_min
        self.setFixedHeight(24 * HOUR_H)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.chips: list[EventChip] = []
        for b in blocks:
            ev = b.event
            s = layout.to_local(ev.start, ref)
            chip = EventChip(ev, f"{layout.short_time(s)} {ev.title}", wrap=True, parent=self)
            chip.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
            chip.clicked.connect(on_click)
            chip.show()
            self.chips.append(chip)
        self._place()

    def _place(self) -> None:
        w = self.width()
        for chip, b in zip(self.chips, self.blocks):
            x0 = w * b.col // b.ncols
            x1 = w * (b.col + 1) // b.ncols
            y0 = b.start_min * HOUR_H // 60
            y1 = b.end_min * HOUR_H // 60
            chip.setGeometry(x0 + 1, y0 + 1, max(8, x1 - x0 - 2), max(10, y1 - y0 - 2))

    def resizeEvent(self, e):
        self._place()
        super().resizeEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        if self.is_today:
            p.fillRect(self.rect(), QColor(TODAY_BG))
        p.setPen(QColor(GRID))
        for h in range(24):
            p.drawLine(0, h * HOUR_H, self.width(), h * HOUR_H)
        p.drawLine(0, 0, 0, self.height())
        if self.now_min is not None:
            y = self.now_min * HOUR_H // 60
            p.setPen(QColor(NOW_LINE))
            p.drawLine(0, y, self.width(), y)
            p.drawLine(0, y - 1, self.width(), y - 1)


class TimeView(QWidget):
    """Day (1 column) or week (7 columns) view with an all-day strip on top."""

    event_clicked = pyqtSignal(object)
    day_clicked = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.columns: list[TimelineColumn] = []
        self.headers: list[DayHeader] = []
        self.allday_chips: list[EventChip] = []
        self._dates: list[date] = []
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._outer.setSpacing(0)
        self._top = QWidget()
        self._outer.addWidget(self._top)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._outer.addWidget(self._scroll, 1)
        self._want_scroll: int | None = None

    def set_data(self, dates: list[date], by_day: dict[date, list], ref: datetime,
                 reset_scroll: bool) -> None:
        prev = self._scroll.verticalScrollBar().value()
        today = agenda._local_date(ref)
        self._dates = dates
        self.columns, self.headers, self.allday_chips = [], [], []

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
            is_today = d == today
            head = DayHeader(d, is_today, f"{d:%a} {d.day}")
            head.clicked.connect(self.day_clicked)
            self.headers.append(head)
            grid.addWidget(head, 0, i + 1)
            grid.setColumnStretch(i + 1, 1)
            grid.setColumnMinimumWidth(i + 1, 0)

            strip = QWidget()
            strip.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
            sl = QVBoxLayout(strip)
            sl.setContentsMargins(1, 1, 1, 1)
            sl.setSpacing(1)
            for ev in by_day.get(d, []):
                if ev.all_day:
                    chip = EventChip(ev, ev.title)
                    chip.clicked.connect(self.event_clicked)
                    sl.addWidget(chip)
                    self.allday_chips.append(chip)
            grid.addWidget(strip, 1, i + 1)

            timed = [e for e in by_day.get(d, []) if not e.all_day]
            now_min = None
            if is_today:
                n = layout.to_local(ref, ref)
                now_min = n.hour * 60 + n.minute
            col = TimelineColumn(d, layout.day_blocks(timed, d, ref), is_today, now_min,
                                 ref, self.event_clicked.emit)
            self.columns.append(col)
            hbox.addWidget(col, 1)

        self._outer.replaceWidget(self._top, top)
        self._top.deleteLater()
        self._top = top
        self._scroll.setWidget(inner)

        if reset_scroll:
            hour = DEFAULT_SCROLL_HOUR
            if today in dates:
                n = layout.to_local(ref, ref)
                hour = max(0, n.hour - 1)
            self._want_scroll = hour * HOUR_H
        else:
            self._want_scroll = prev
        QTimer.singleShot(0, self._apply_scroll)

    def _apply_scroll(self) -> None:
        if self._want_scroll is not None:
            self._scroll.verticalScrollBar().setValue(self._want_scroll)

    def showEvent(self, e):
        super().showEvent(e)
        QTimer.singleShot(0, self._apply_scroll)

    def scroll_value(self) -> int:
        return self._scroll.verticalScrollBar().value()


class DayCell(QFrame):
    clicked = pyqtSignal(object)

    def __init__(self, day: date, in_month: bool, is_today: bool, parent=None):
        super().__init__(parent)
        self.day = day
        self.chips: list[EventChip] = []
        self.more_label: QLabel | None = None
        self.setObjectName("cell")
        bg = TODAY_BG if is_today else "transparent"
        border = theme.LINK if is_today else GRID
        self.setStyleSheet(f"QFrame#cell{{background:{bg};border:1px solid {border};}}")
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setMinimumHeight(18 + 3 * 17 + 16)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(2, 2, 2, 2)
        self._lay.setSpacing(1)
        color = theme.TEXT if in_month else theme.MUTED
        weight = "bold" if is_today else "normal"
        self.number = QLabel(str(day.day))
        self.number.setStyleSheet(f"color:{color};font-weight:{weight};border:none;"
                                  f"background:transparent;")
        self.number.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._lay.addWidget(self.number)
        self.dimmed = not in_month

    def set_events(self, events: list, on_click, ref: datetime) -> None:
        shown, more = layout.split_for_month(events)
        for ev in shown:
            text = ev.title if ev.all_day else f"{layout.short_time(layout.to_local(ev.start, ref))} {ev.title}"
            chip = EventChip(ev, text)
            if self.dimmed:
                effect = QGraphicsOpacityEffect(chip)
                effect.setOpacity(0.5)
                chip.setGraphicsEffect(effect)
            chip.clicked.connect(on_click)
            self._lay.addWidget(chip)
            self.chips.append(chip)
        if more:
            self.more_label = QLabel(f"+{more} more")
            self.more_label.setStyleSheet(f"color:{theme.MUTED};font-size:11px;border:none;"
                                          f"background:transparent;")
            self._lay.addWidget(self.more_label)
        self._lay.addStretch(1)

    def mousePressEvent(self, e):
        e.accept()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.day)
        e.accept()


class MonthView(QWidget):
    event_clicked = pyqtSignal(object)
    day_clicked = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cells: dict[date, DayCell] = {}
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(0)

    def set_data(self, dates: list[date], month: int, by_day: dict[date, list], ref: datetime):
        _clear(self._grid)
        self.cells = {}
        today = agenda._local_date(ref)
        for c in range(7):
            lab = QLabel(dates[c].strftime("%a"))
            lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lab.setStyleSheet(f"color:{theme.MUTED};padding:3px;")
            self._grid.addWidget(lab, 0, c)
            self._grid.setColumnStretch(c, 1)
        for i, d in enumerate(dates):
            cell = DayCell(d, d.month == month, d == today)
            cell.set_events(by_day.get(d, []), self.event_clicked.emit, ref)
            cell.clicked.connect(self.day_clicked)
            self._grid.addWidget(cell, 1 + i // 7, i % 7)
            self.cells[d] = cell
        for r in range(1, 7):
            self._grid.setRowStretch(r, 1)


class EventDetails(QDialog):
    """Read-only details for one event."""

    def __init__(self, event, ref: datetime, parent=None):
        super().__init__(parent)
        self.event = event
        self.setWindowTitle(event.title or "Event")
        self.setMinimumWidth(340)
        self.setStyleSheet(f"QDialog{{background:{theme.BG};}}"
                           f"QLabel{{color:{theme.TEXT};}}"
                           f"QPlainTextEdit{{background:{theme.BG};color:{theme.TEXT};"
                           f"border:1px solid {theme.BORDER};}}")
        lay = QVBoxLayout(self)
        self.title_label = QLabel(event.title)
        self.title_label.setTextFormat(Qt.TextFormat.PlainText)
        self.title_label.setWordWrap(True)
        self.title_label.setStyleSheet("font-size:16px;font-weight:bold;")
        self.time_label = QLabel(layout.format_range(event, ref))
        self.time_label.setTextFormat(Qt.TextFormat.PlainText)
        self.time_label.setWordWrap(True)
        color = theme.stripe_color(event.color)
        self.calendar_label = QLabel(
            f'<span style="color:{color};">&#9632;</span> ' + _esc(event.calendar_name))
        self.calendar_label.setTextFormat(Qt.TextFormat.RichText)
        for w in (self.title_label, self.time_label, self.calendar_label):
            lay.addWidget(w)
        self.location_label = None
        if event.location:
            self.location_label = QLabel(event.location)
            self.location_label.setTextFormat(Qt.TextFormat.PlainText)
            self.location_label.setWordWrap(True)
            lay.addWidget(self.location_label)
        self.link_label = None
        url = event.meeting_url
        if url and url.lower().startswith(("http://", "https://")):
            self.link_label = QLabel(
                f'<a href="{_esc(url)}" style="color:{theme.LINK};">Join meeting</a>')
            self.link_label.setTextFormat(Qt.TextFormat.RichText)
            self.link_label.setToolTip(plain_tip(url))
            self.link_label.linkActivated.connect(self.open_link)
            lay.addWidget(self.link_label)
        self.description_box = None
        if event.description:
            self.description_box = QPlainTextEdit(event.description)
            self.description_box.setReadOnly(True)
            self.description_box.setMinimumHeight(90)
            lay.addWidget(self.description_box, 1)

    @staticmethod
    def open_link(url: str) -> None:
        if url.lower().startswith(("http://", "https://")):
            QDesktopServices.openUrl(QUrl(url))


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


class CalendarWindow(QMainWindow):
    def __init__(self, cache, settings, qsettings: QSettings | None = None, parent=None):
        super().__init__(parent)
        self.cache, self.settings = cache, settings
        self.now = lambda: datetime.now().astimezone()
        self._qs = qsettings or QSettings(
            str(data_dir() / "window.ini"), QSettings.Format.IniFormat)
        self.view = "week"
        self.anchor: date = agenda._local_date(self.now())
        self.details: EventDetails | None = None
        self.setWindowTitle("SmartCal")
        self.resize(1000, 700)
        self.setStyleSheet(f"QMainWindow,#central{{background:{theme.BG};}}"
                           f"QLabel{{color:{theme.TEXT};}}"
                           f"QPushButton{{background:{theme.BORDER};color:{theme.TEXT};"
                           f"border:none;padding:5px 12px;border-radius:4px;}}"
                           f"QPushButton:hover{{background:#4a4d51;}}"
                           f"QPushButton:checked{{background:{theme.BG_NEXT};color:{theme.LINK};}}")
        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        bar = QHBoxLayout()
        self.prev_btn, self.today_btn, self.next_btn = (
            QPushButton("<"), QPushButton("Today"), QPushButton(">"))
        self.prev_btn.setToolTip("Previous")
        self.next_btn.setToolTip("Next")
        self.prev_btn.clicked.connect(lambda: self.step(-1))
        self.next_btn.clicked.connect(lambda: self.step(1))
        self.today_btn.clicked.connect(self.go_today)
        for b in (self.prev_btn, self.today_btn, self.next_btn):
            bar.addWidget(b)
        self.title_label = QLabel()
        self.title_label.setStyleSheet("font-size:16px;font-weight:bold;padding-left:10px;")
        bar.addWidget(self.title_label, 1)
        self.view_buttons: dict[str, QPushButton] = {}
        group = QButtonGroup(self)
        group.setExclusive(True)
        for name in VIEWS:
            b = QPushButton(name.capitalize())
            b.setCheckable(True)
            b.clicked.connect(lambda _c=False, n=name: self.set_view(n))
            group.addButton(b)
            bar.addWidget(b)
            self.view_buttons[name] = b
        root.addLayout(bar)
        self.stack = QStackedWidget()
        self.time_view = TimeView()
        self.month_view = MonthView()
        self.stack.addWidget(self.time_view)
        self.stack.addWidget(self.month_view)
        root.addWidget(self.stack, 1)
        for v in (self.time_view, self.month_view):
            v.event_clicked.connect(self.show_details)
            v.day_clicked.connect(self._open_day)

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
        if not ok or not self._on_a_screen():
            self._center_default()

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

    # -- navigation -----------------------------------------------------------
    def set_view(self, view: str) -> None:
        if view not in VIEWS:
            raise ValueError(f"unknown view {view!r}")
        self.view = view
        self.refresh(reset_scroll=True)

    def show_view(self, view: str) -> None:
        self.set_view(view)
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def go_to(self, d: date) -> None:
        self.anchor = d
        self.refresh(reset_scroll=True)

    def go_today(self) -> None:
        self.go_to(agenda._local_date(self.now()))

    def step(self, direction: int) -> None:
        if self.view == "month":
            self.anchor = layout.shift_month(self.anchor, direction)
        else:
            self.anchor += timedelta(days=direction * (7 if self.view == "week" else 1))
        self.refresh(reset_scroll=True)

    def _open_day(self, d: date) -> None:
        self.anchor = d
        self.set_view("day")

    # -- rendering --------------------------------------------------------------
    def visible_dates(self) -> list[date]:
        if self.view == "month":
            return layout.month_grid(self.anchor.year, self.anchor.month)
        if self.view == "week":
            return layout.week_dates(self.anchor)
        return [self.anchor]

    def _load(self, dates: list[date], ref: datetime) -> list:
        first, last = layout.day_bounds(dates[0], ref)[0], layout.day_bounds(dates[-1], ref)[1]
        primary = agenda._primary_ids(self.cache)
        return [e for e in self.cache.events_between(first - timedelta(days=1),
                                                     last + timedelta(days=1))
                if not agenda._is_hidden(e, self.settings, primary)]

    def refresh(self, reset_scroll: bool = False) -> None:
        """Re-read and rebuild. While hidden, just remember to do it on show."""
        if reset_scroll:
            self._pending_reset = True
        self.title_label.setText(layout.title_for(self.view, self.anchor))
        self.view_buttons[self.view].setChecked(True)
        if not self.isVisible():
            self._stale = True
            return
        self._stale = False
        reset_scroll = reset_scroll or self._pending_reset
        self._pending_reset = False
        ref = self.now()
        dates = self.visible_dates()
        by_day = layout.events_by_day(self._load(dates, ref), dates, ref)
        self.title_label.setText(layout.title_for(self.view, self.anchor))
        self.view_buttons[self.view].setChecked(True)
        if self.view == "month":
            self.month_view.set_data(dates, self.anchor.month, by_day, ref)
            self.stack.setCurrentWidget(self.month_view)
        else:
            self.time_view.set_data(dates, by_day, ref, reset_scroll)
            self.stack.setCurrentWidget(self.time_view)

    def _tick(self) -> None:
        if self.isVisible():
            self.refresh()

    # -- details -----------------------------------------------------------------
    def show_details(self, event) -> None:
        if self.details is not None:
            self.details.close()
            self.details.deleteLater()
        self.details = EventDetails(event, self.now(), self)
        self.details.show()
        self.details.raise_()

    def showEvent(self, e):
        super().showEvent(e)
        self.refresh()

    def _close_details(self) -> None:
        if self.details is not None:
            self.details.close()

    def hideEvent(self, e):
        self._save_geometry()
        self._close_details()
        super().hideEvent(e)

    def closeEvent(self, e):
        self._save_geometry()
        self._close_details()
        super().closeEvent(e)
