"""v0.11.0: the Day / Week / Month window. Readable tiles (padding, gaps, minimum height,
hierarchy, eliding), month cells that never cram ("+N more"), the current-time line above
the tiles, the diffed refresh (nothing recreated, nothing moved when nothing changed),
and its motion: the open build, the switch / step slide, hover and press, all gated by
the animation setting, interruptible and landing exactly in the unanimated state.
(The real-display run, screen grabs included, is test_real_display_window_motion.)"""
import os
import subprocess
import sys
import textwrap
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from PyQt6 import sip
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from smartcal.ui import layout, motion, theme
from smartcal.ui import window as wm
from smartcal.ui.cards import CardBuild, CardEffect
from test_window import NY, ev, make

D = date(2026, 11, 4)


def t(h, m=0, d=D):
    return datetime(d.year, d.month, d.day, h, m, tzinfo=NY)


def busy():
    """A busy Wednesday: back-to-back, concurrent, 15-minute, all-day, long titles,
    two calendars, a declined one."""
    return [
        ev("ad", "Offsite planning", t(0), t(0, d=D + timedelta(days=1)), True),
        ev("s", "Standup", t(8), t(8, 15)),
        ev("q", "Quick sync", t(8, 15), t(8, 30), color="#0b8043"),
        ev("p", "Quarterly planning review with the leadership team and finance partners",
           t(8, 30), t(9, 30), location="Conference Room 4B / Zoom"),
        ev("a", "1:1 with Alex", t(9), t(10), color="#0b8043"),
        ev("v", "Vendor call", t(9, 30), t(10)),
        ev("c", "Customer escalation", t(10), t(10, 30)),
        ev("i", "Interview", t(10, 30), t(11)),
        ev("x", "Architecture deep dive", t(10, 30), t(11, 30)),
        ev("k", "Coffee chat", t(11), t(11, 15)),
        ev("l", "Lunch & learn", t(12), t(13), declined=True),
        ev("r", "Sprint review", t(13), t(13, 30)),
        ev("o", "Retro", t(13, 30), t(14)),
        ev("y", "Payroll approval", t(13, 30), t(13, 45)),
        ev("f", "Focus time", t(14), t(15)),
    ]


def dense_month():
    out = []
    for day in range(2, 28):
        d = date(2026, 11, day)
        if d.weekday() >= 5:
            continue
        for i in range(2 + day % 5):
            out.append(ev(f"m{day}-{i}", f"Meeting {day}-{i}", t(9 + i, d=d), t(9 + i, 30, d=d)))
    return out


@pytest.fixture
def animated(monkeypatch):
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)
    monkeypatch.delenv(motion.ENV_LEVEL, raising=False)


@pytest.fixture
def basic(monkeypatch):
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)
    monkeypatch.setenv(motion.ENV_LEVEL, "basic")


def card(w):
    fx = w.graphicsEffect()
    return fx if isinstance(fx, CardEffect) else None


def tiles(w):
    return w.time_view.tiles() if w.view != "month" else \
        [c for cell in w.month_view.tiles() for c in cell.all_chips]


def settle(qtbot, w, timeout=4000):
    qtbot.waitUntil(lambda: not w.animating(), timeout=timeout)
    QApplication.processEvents()


def no_effects(w) -> bool:
    widgets = [w.toolbar] + list(w.time_view.headers) + w.time_view.tiles() + \
        list(w.month_view.cells.values()) + list(w.month_view.weekday_labels) + \
        [c for cell in w.month_view.cells.values() for c in cell.all_chips]
    return all(x.graphicsEffect() is None for x in widgets if not sip.isdeleted(x))


def chips_by_id(w):
    return {c.event.id: c for col in w.time_view.columns for c in col.chips}


# -- readability: padding, gaps, minimum height ------------------------------------------
def test_tiles_keep_gaps_and_never_overlap(qtbot, tmp_path):
    w = make(qtbot, tmp_path, busy(), "day")
    col = w.time_view.columns[0]
    min_h = layout.MIN_BLOCK_MIN * wm.HOUR_H // 60 - wm.TILE_GAP_Y
    rects = {c.event.id: c.geometry() for c in col.chips}
    assert len(rects) == 14
    for c in col.chips:
        r = c.geometry()
        assert r.height() >= min_h, (c.event.title, r)
        assert r.left() >= wm.TILE_MARGIN_L and r.right() <= col.width() - wm.TILE_MARGIN_R
    ids = list(rects)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            ra, rb = rects[a], rects[b]
            assert not ra.intersects(rb), (a, b, ra, rb)
            side_by_side = ra.top() < rb.bottom() and rb.top() < ra.bottom()
            if side_by_side:      # concurrent: TILE_GAP_X apart (1 px of rounding)
                gap = rb.left() - ra.right() - 1 if ra.left() < rb.left() else ra.left() - rb.right() - 1
                assert gap >= wm.TILE_GAP_X - 1, (a, b, gap)
    # back to back (same column): exactly TILE_GAP_Y between them
    assert rects["c"].top() - rects["v"].bottom() - 1 == wm.TILE_GAP_Y
    assert rects["f"].top() - rects["o"].bottom() - 1 == wm.TILE_GAP_Y


def test_short_tiles_show_one_readable_line_long_ones_more(qtbot, tmp_path):
    w = make(qtbot, tmp_path, busy(), "day")
    chips = chips_by_id(w)
    short = chips["k"]                                       # 15 minutes
    assert short.visible_lines() == 1
    from PyQt6.QtGui import QFontMetrics
    title_h = QFontMetrics(short._title_font()).height()
    assert short.height() >= title_h + 2                      # the title fits whole
    assert chips["f"].visible_lines() >= 2                    # 1 hour: title + time
    assert chips["p"].visible_lines() == 3                    # + its location
    assert chips["p"].range_text == "8:30a – 9:30a"
    # the full text is in the tooltip (the tile elides it)
    assert "finance partners" in chips["p"].toolTip() and "Room 4B" in chips["p"].toolTip()


def test_wrap_lines_elides_the_last_line():
    from PyQt6.QtGui import QFont, QFontMetrics
    fm = QFontMetrics(QFont())
    lines = wm.wrap_lines("one two three four five six seven eight nine ten", fm,
                          fm.horizontalAdvance("one two three"), 2)
    assert len(lines) == 2 and lines[1].endswith("…")
    assert wm.wrap_lines("", fm, 100, 2) == [] and wm.wrap_lines("x", fm, 100, 0) == []
    long = wm.wrap_lines("Supercalifragilistic short", fm, fm.horizontalAdvance("Super"), 1)
    assert len(long) == 1 and long[0].endswith("…")


def test_tile_text_contrast_both_styles():
    for _name, color in theme.PALETTE:
        for style in theme.TILE_STYLES:
            for kind in (wm.BLOCK, wm.BAR):
                bg, fg, meta, _bar = wm.tile_colors(color, style, kind)
                assert theme.contrast_ratio(bg.name(), fg) >= 4.5, (color, style, kind)
                assert theme.contrast_ratio(bg.name(), meta) >= 4.5, (color, style, kind)


def test_window_follows_the_tile_style(qtbot, tmp_path):
    w = make(qtbot, tmp_path, busy(), "week")
    chip = chips_by_id(w)["s"]
    assert chip.style == "stripe" and chip.colors()[3] == "#4285f4"
    w.set_tile_style("fill")
    chip2 = chips_by_id(w)["s"]
    assert chip2.style == "fill" and chip2.colors()[3] is None
    assert chip2.colors()[0].name() == "#4285f4"


def test_now_line_is_above_the_tiles_and_only_today(qtbot, tmp_path):
    w = make(qtbot, tmp_path, busy(), "week")       # NOW = 9:00 on Wed Nov 4
    cols = w.time_view.columns
    today = cols[3]
    assert today.now_line is not None and today.now_line.isVisible()
    assert today.children()[-1] is today.now_line   # stacked last: drawn above the tiles
    assert today.now_line.center_y() == 9 * wm.HOUR_H
    assert all(c.now_line is None or not c.now_line.isVisible() for c in cols if c is not today)


def test_day_view_empty_note(qtbot, tmp_path):
    w = make(qtbot, tmp_path, [], "day")
    assert w.time_view.empty_note.isVisible()
    w.set_view("week")
    assert not w.time_view.empty_note.isVisible()


def test_month_cells_never_cram_and_more_opens_the_day(qtbot, tmp_path):
    w = make(qtbot, tmp_path, dense_month() + busy(), "month")
    for size in ((1000, 700), (760, 520), (600, 420)):
        w.resize(*size)
        QApplication.processEvents()
        for d, cell in w.month_view.cells.items():
            total = len(cell.all_chips)
            shown = len(cell.chips)
            more = cell.more_label
            assert shown + (int(more.text()[1:].split()[0]) if more else 0) == total
            bottom = max([c.geometry().bottom() for c in cell.chips] +
                         ([more.geometry().bottom()] if more else []) + [0])
            assert bottom < cell.height(), (d, size)
            if total and more is None:
                assert shown == total
    cell = w.month_view.cells[D]
    assert cell.more_label is not None and cell.more_label.isVisible()
    qtbot.mouseClick(cell.more_label, Qt.MouseButton.LeftButton)
    assert w.view == "day" and w.anchor == D


# -- the diffed refresh ------------------------------------------------------------------
def snapshot(w):
    if w.view == "month":
        cells = list(w.month_view.cells.values())
        return ([id(c) for c in cells], [c.geometry() for c in cells],
                [id(x) for c in cells for x in c.all_chips],
                [x.geometry() for c in cells for x in c.all_chips])
    ts = w.time_view.tiles()
    return ([id(c) for c in w.time_view.columns], [id(h) for h in w.time_view.headers],
            [id(x) for x in ts], [x.geometry() for x in ts], w.time_view.scroll_value())


@pytest.mark.parametrize("view", ["day", "week", "month"])
def test_refresh_with_unchanged_data_changes_nothing(qtbot, tmp_path, animated, view):
    w = make(qtbot, tmp_path, busy() + dense_month(), view)
    settle(qtbot, w)
    if view != "month":
        w.time_view.set_scroll_value(5 * wm.HOUR_H + 7)
    before = snapshot(w)
    for _ in range(3):
        w.refresh()
        w._tick()
    QApplication.processEvents()
    assert snapshot(w) == before
    assert not w.animating() and no_effects(w)


def test_refresh_after_one_change_recreates_only_that_tile(qtbot, tmp_path):
    w = make(qtbot, tmp_path, busy(), "week")
    old = chips_by_id(w)
    w.settings.set_event_color(old["c"].event, "#e8594f")
    w.refresh()
    new = chips_by_id(w)
    assert new["c"] is not old["c"] and new["c"].color == "#e8594f"
    assert all(new[k] is old[k] for k in old if k != "c")


def test_minute_tick_only_moves_the_now_line(qtbot, tmp_path, animated):
    w = make(qtbot, tmp_path, busy(), "week")
    settle(qtbot, w)
    before = snapshot(w)
    line = w.time_view.columns[3].now_line
    w.now = lambda: datetime(2026, 11, 4, 9, 30, tzinfo=NY)
    w._tick()
    assert line.animating()                                   # eases to its place
    assert snapshot(w)[:4] == before[:4]
    qtbot.waitUntil(lambda: not line.animating(), timeout=2000)
    assert line.center_y() == 9 * wm.HOUR_H + wm.HOUR_H // 2


# -- motion: gating ------------------------------------------------------------------------
def test_open_builds_toolbar_headers_then_tiles(qtbot, tmp_path, animated):
    w = make(qtbot, tmp_path, busy(), "week")
    builds = w.builds()
    assert len(builds) == 3
    toolbar, heads, ts = builds
    assert toolbar.widgets() == [w.toolbar]
    assert heads.widgets() == w.time_view.headers
    assert ts.widgets() == w.time_view.tiles()
    d = ts.delays()
    assert d[0] == motion.WIN_TILE_START_MS and d == sorted(d)
    assert d[-1] - d[0] <= motion.WIN_TILE_SPAN_MS
    if not ts.started():                                      # out of place before paint
        assert all(card(x).state() == (0.0, motion.WIN_TILE_OFFSET_PX, motion.WIN_TILE_SCALE)
                   for x in w.time_view.tiles())
    settle(qtbot, w)
    assert no_effects(w)


def test_tiles_build_in_order_of_start_time(qtbot, tmp_path):
    w = make(qtbot, tmp_path, busy(), "week")
    starts = [c.block.start_min for c in w.time_view.tiles() if c.block is not None]
    assert starts == sorted(starts)
    assert w.time_view.tiles()[0].kind == wm.BAR              # all-day first


def test_animations_off_nothing_moves(qtbot, tmp_path):
    w = make(qtbot, tmp_path, busy(), "week")
    assert w.builds() == [] and no_effects(w)
    w.step(1)
    assert not w.transitioning() and w.builds() == []
    w.step(-1)
    chip = w.time_view.tiles()[-1]
    qtbot.mousePress(chip, Qt.MouseButton.LeftButton)
    assert chip.graphicsEffect() is None
    qtbot.mouseRelease(chip, Qt.MouseButton.LeftButton)


def test_basic_keeps_only_the_slide(qtbot, tmp_path, basic):
    w = make(qtbot, tmp_path, busy(), "week")
    assert w.builds() == [] and no_effects(w)                 # no open build
    w.step(1)
    assert w.transitioning() and w.builds() == []             # the slide, no stagger
    settle(qtbot, w)
    w.step(-1)
    settle(qtbot, w)
    chip = chips_by_id(w)["s"]
    qtbot.mousePress(chip, Qt.MouseButton.LeftButton)         # no press scale
    assert chip.graphicsEffect() is None
    qtbot.mouseRelease(chip, Qt.MouseButton.LeftButton)


def test_step_slides_old_content_out_and_tiles_in(qtbot, tmp_path, animated):
    w = make(qtbot, tmp_path, busy(), "week")
    settle(qtbot, w)
    w.step(-1)                                                # previous week
    snap = w.snapshot_widget()
    assert snap is not None and snap.isVisible()
    # over the new content (the "not synced" line that appeared above it stays visible)
    assert w.unsynced_shown()
    assert snap.geometry().topLeft() == w.stack.geometry().topLeft()
    assert snap.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    b = w.builds()[0]
    assert b.offset_x == -motion.WIN_NAV_SLIDE_PX             # comes from the left
    assert all(card(x) is not None for x in b.widgets())
    w._snap_anim.setCurrentTime(motion.WIN_NAV_MS // 2)
    op, dx = snap.state()
    assert 0 < op < 1 and dx > 0                              # the old one leaves right
    settle(qtbot, w)
    assert w.snapshot_widget() is None and no_effects(w)


def test_switch_direction_follows_view_order(qtbot, tmp_path, animated):
    w = make(qtbot, tmp_path, busy(), "week")
    settle(qtbot, w)
    w.set_view("month")
    assert w.builds()[0].offset_x == motion.WIN_SWITCH_SLIDE_PX
    assert w.builds()[0].widgets() == w.month_view.tiles()    # the day cells
    settle(qtbot, w)
    w.set_view("day")
    assert w.builds()[0].offset_x == -motion.WIN_SWITCH_SLIDE_PX
    settle(qtbot, w)
    w.set_view("day")                                         # same view: nothing plays
    assert not w.animating()


# -- motion: final state, interruption --------------------------------------------------
def rendered(w):
    QApplication.processEvents()
    return w.centralWidget().grab().toImage()


def test_final_state_is_exactly_the_unanimated_one(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)
    w = make(qtbot, tmp_path / "a", busy(), "week")
    w.step(1)
    w.step(-1)
    w.set_view("month")
    w.set_view("week")
    settle(qtbot, w)
    animated_state = (snapshot(w)[3], rendered(w))
    monkeypatch.setattr(motion, "animations_enabled", lambda: False)
    w2 = make(qtbot, tmp_path / "b", busy(), "week")
    w2.move(w.pos())
    QApplication.processEvents()
    assert snapshot(w2)[3] == animated_state[0]
    assert rendered(w2) == animated_state[1]


def test_rapid_navigation_ends_clean(qtbot, tmp_path, animated):
    w = make(qtbot, tmp_path, busy() + dense_month(), "week")
    for i in range(12):
        w.step(1 if i % 3 else -1)
        if i % 4 == 0:
            w.set_view(("day", "week", "month")[i % 3])
        if i % 5 == 0:
            w.refresh()                                       # a sync mid-animation
        if i % 2:
            qtbot.wait(20)
    assert len([s for s in w.centralWidget().findChildren(wm._Snapshot)
                if not sip.isdeleted(s) and s.isVisible()]) <= 1
    settle(qtbot, w)
    assert no_effects(w) and w.snapshot_widget() is None
    assert all(sip.isdeleted(b) or not b.running() for b in w.findChildren(CardBuild))


def test_refresh_mid_build_does_not_replay_it(qtbot, tmp_path, animated):
    w = make(qtbot, tmp_path, busy(), "week")
    b = w.builds()[-1]
    qtbot.waitUntil(b.started, timeout=1000)
    b._frame(60.0)
    ts = w.time_view.tiles()
    w.refresh()
    assert w.builds()[-1] is b and w.time_view.tiles() == ts and b.elapsed() == 60.0
    settle(qtbot, w)
    assert no_effects(w)


def test_hide_and_close_mid_animation_leave_nothing(qtbot, tmp_path, animated):
    w = make(qtbot, tmp_path, busy(), "week")
    w.step(1)
    assert w.animating()
    w.hide()
    assert not w.animating() and no_effects(w) and w.snapshot_widget() is None
    w.show()
    assert w.building()
    w.close()
    assert not w.animating() and no_effects(w)


def test_press_mid_build_lands_the_tile_and_scales(qtbot, tmp_path, animated):
    w = make(qtbot, tmp_path, busy(), "week")
    b = w.builds()[-1]
    qtbot.waitUntil(b.started, timeout=1000)
    b._frame(40.0)
    chip = chips_by_id(w)["f"]
    assert b.building(chip)
    qtbot.mousePress(chip, Qt.MouseButton.LeftButton)
    assert not b.building(chip) and chip.press_feedback.active()
    qtbot.mouseRelease(chip, Qt.MouseButton.LeftButton)
    assert w.details is not None and w.details.event.id == "f"   # the click still opens
    settle(qtbot, w)
    qtbot.waitUntil(lambda: chip.graphicsEffect() is None, timeout=1000)


def test_hover_fades_tile_and_cell(qtbot, tmp_path, animated):
    from PyQt6.QtCore import QEvent, QPointF
    from PyQt6.QtGui import QEnterEvent
    w = make(qtbot, tmp_path, busy(), "week")
    settle(qtbot, w)
    chip = chips_by_id(w)["f"]
    QApplication.sendEvent(chip, QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))
    assert chip._hover_anim is not None
    qtbot.waitUntil(lambda: chip.hover_level() == 1.0, timeout=1000)
    QApplication.sendEvent(chip, QEvent(QEvent.Type.Leave))
    qtbot.waitUntil(lambda: chip.hover_level() == 0.0, timeout=1000)
    w.set_view("month")
    settle(qtbot, w)
    cell = w.month_view.cells[D]
    QApplication.sendEvent(cell, QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))
    qtbot.waitUntil(lambda: cell.hover_level() == 1.0, timeout=1000)


def test_details_lines_build_in_only_with_micro_animations(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)
    e = ev("1", "Standup", t(10), t(10, 30), location="Room 4", description="Notes",
           meeting_url="https://meet.example/abc")
    w = make(qtbot, tmp_path, [e], "week")
    settle(qtbot, w)
    w.show_details(e)
    d = w.details
    assert d.content_build() is not None and d.building()
    assert d.content_build().widgets() == d.build_lines()
    qtbot.waitUntil(lambda: not d.building(), timeout=3000)
    assert all(x.graphicsEffect() is None for x in d.build_lines())
    monkeypatch.setenv(motion.ENV_LEVEL, "basic")
    w.show_details(e)
    assert w.details.content_build() is None


def test_spread_delays():
    assert motion.spread_delays(4, 100, 25, 300) == [100, 125, 150, 175]
    d = motion.spread_delays(40, 100, 25, 300)
    assert d[0] == 100 and d[-1] == 400 and d == sorted(d)
    assert motion.spread_delays(0, 0, 25, 300) == [] and motion.spread_delays(1, 7, 25, 0) == [7]


def test_cardeffect_sideways_offset(qtbot):
    from PyQt6.QtCore import QRectF
    from PyQt6.QtWidgets import QLabel
    lab = QLabel("x")
    qtbot.addWidget(lab)
    fx = CardEffect(lab, pad_x=24)
    fx.set_state(0.5, 0.0, 1.0, -12.0)
    assert fx.dx() == -12.0 and fx.state() == (0.5, 0.0, 1.0)
    r = fx.boundingRectFor(QRectF(0, 0, 100, 20))
    assert r.left() <= -24 and r.right() >= 124


# -- the real Windows display (opt in: it opens real windows) ---------------------------
RUN = textwrap.dedent(r"""
    import faulthandler, json, subprocess, sys
    faulthandler.enable()
    from pathlib import Path
    tmp = Path(sys.argv[1]); sys.path[:0] = [sys.argv[2], sys.argv[3]]
    from PyQt6.QtCore import QElapsedTimer, QEvent, QRect, QSettings, Qt, qInstallMessageHandler
    from PyQt6.QtWidgets import QApplication, QWidget
    msgs = []
    qInstallMessageHandler(lambda t, c, m: msgs.append(m))
    app = QApplication([])
    from smartcal.ui import motion
    motion.animations_enabled = lambda: True
    from smartcal.db import Cache
    from smartcal.models import CalendarInfo
    from smartcal.settings import Settings
    from smartcal.ui.cards import CardEffect
    from smartcal.ui.window import CalendarWindow
    from test_window import NOW
    from test_window_motion import busy, dense_month
    cache = Cache(tmp / "c.db")
    cache.upsert_calendars("a", [CalendarInfo("primary", "Primary", "#4285f4", True),
                                 CalendarInfo("other", "Other", "#0b8043", True)])
    cache.upsert_events("a", busy() + dense_month())
    w = CalendarWindow(cache, Settings(tmp / "s.json"),
                       QSettings(str(tmp / "q.ini"), QSettings.Format.IniFormat))
    w.now = lambda: NOW
    def pump(ms):
        t = QElapsedTimer(); t.start()
        while t.elapsed() < ms:
            app.processEvents()
            app.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    scr = app.primaryScreen().availableGeometry()
    r = QRect(scr.left() + 60, scr.top() + 60, 900, 620)
    back = QWidget()                    # a black backdrop: any white is ours
    back.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
    back.setStyleSheet("background:#000;"); back.setGeometry(r.adjusted(-30, -50, 30, 30))
    back.show(); pump(300)
    w.anchor = NOW.date(); w._needs_default_place = False
    w.setGeometry(r); w.show_view("week"); pump(900)
    g = w.geometry()                    # the client area (the title bar is Windows')
    cap = subprocess.Popen([sys.executable, str(tmp / "capture.py"), str(g.x()), str(g.y()),
                            str(g.width()), str(g.height()), "10", str(tmp / "cap.npy")])
    pump(400)
    def clean():
        widgets = [w.toolbar] + w.time_view.headers + w.time_view.tiles() + \
            list(w.month_view.cells.values())
        return all(not isinstance(x.graphicsEffect(), CardEffect) for x in widgets)
    t = QElapsedTimer(); t.start(); k = 0; checks = []
    while t.elapsed() < 8500:
        for i in range(6):                                  # prev / next spam
            w.step(1 if (i + k) % 3 else -1); pump(15 if i % 2 else 45)
        w.refresh(); pump(30)                               # a sync mid-animation
        w.set_view(("day", "month", "week")[k % 3]); pump(60)
        w.set_view(("month", "week", "day")[k % 3]); pump(25)   # rapid switching
        w.go_today(); pump(120 if k % 2 else 40)
        if k % 3 == 1:
            w.close(); pump(80)                             # close mid-animation...
            w.setGeometry(r); w.show_view("week"); pump(60)  # ...and open again
        pump(400)
        t2 = QElapsedTimer(); t2.start()                    # the open build: < 1 s
        while w.animating() and t2.elapsed() < 1500:
            pump(20)
        checks.append(not w.animating() and clean())
        k += 1
    cap.wait()
    info = {"clean": all(checks), "loops": k, "checks": checks,
            "geometry_warnings": [m for m in msgs if "geometry" in m.lower()][:5]}
    (tmp / "info.json").write_text(json.dumps(info))
    print("survived", flush=True)
""")


@pytest.mark.skipif(sys.platform != "win32" or os.environ.get("SMARTCAL_TESTS_REAL_DISPLAY") != "1",
                    reason="opens real windows: SMARTCAL_TESTS_REAL_DISPLAY=1")
def test_real_display_window_motion(tmp_path):
    import json
    import numpy as np
    from test_rounded_stack import CAPTURE
    tests = Path(__file__).resolve().parent
    env = dict(os.environ, QT_QPA_PLATFORM="windows", SMARTCAL_NO_SYSTEM_WRITES="1",
               SMARTCAL_DATA_DIR=str(tmp_path / "data"))
    env.pop("SMARTCAL_NO_ANIMATIONS", None)
    env.pop(motion.ENV_LEVEL, None)
    (tmp_path / "capture.py").write_text(CAPTURE, encoding="utf-8")
    script = tmp_path / "run.py"
    script.write_text(RUN, encoding="utf-8")
    r = subprocess.run([sys.executable, str(script), str(tmp_path), str(tests.parent), str(tests)],
                       env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                       timeout=240)
    assert r.returncode == 0 and "survived" in r.stdout, (r.returncode, r.stderr[-3000:])
    info = json.loads((tmp_path / "info.json").read_text())
    assert info["clean"] and info["loops"] > 3, info
    assert info["geometry_warnings"] == [], info
    frames = np.load(tmp_path / "cap.npy")
    assert len(frames) > 150
    white = frames.min(axis=3) > 200
    # a white block: a column more than 30% white (text never fills that much)
    bad = np.nonzero((white.mean(axis=1) > 0.3).sum(axis=1) > 2)[0]
    assert len(bad) == 0, f"{len(bad)} frames with white, first at {bad[:5]}"
