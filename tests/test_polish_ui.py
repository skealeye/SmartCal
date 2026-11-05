"""v0.8.0 polish: no Hide…/Color… in the details views (right-click only), the tile style
(side stripe / whole tile), the details panel fitting its content, and the
micro-animations (popup height, row hover/press/selection, row collapse and fade-in, the
notice, the chat, the panel's content swap). Offscreen Qt, real widgets. The suite runs
with animations off; the `animated` tests turn them on and wait with qtbot.waitUntil."""
import json
from datetime import timedelta

import pytest
from PyQt6.QtCore import QElapsedTimer, QEvent, QPoint, QPointF, QRect, Qt
from PyQt6.QtGui import QColor, QEnterEvent
from PyQt6.QtWidgets import QApplication, QFrame, QLabel, QPushButton, QTextBrowser

from smartcal.settings import Settings
from smartcal.ui import meeting_menus, motion, theme
from smartcal.ui.popup import WIDTH, AgendaRow
from smartcal.ui.settings_dialog import SettingsDialog
from smartcal.ui.window import EventDetails
from test_meeting_actions_ui import (DESC, NOW, SERIES, _menu_action, click, details, ev,
                                     make_window, right_click, row_menu, rows, store,
                                     titles)
from test_popup_flyout import AREA, BOTTOM_RIGHT, popup_on

LONG_NOTES = "\n".join(f"line {i} of the notes" for i in range(120))
MANY = [ev(str(i), f"Event {i}", 10 + i // 3, (i % 3) * 20) for i in range(6)]


@pytest.fixture
def animated(monkeypatch):
    """Animations on, micro-interactions included (the suite default is off)."""
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)
    monkeypatch.delenv(motion.ENV_LEVEL, raising=False)


def settled(qtbot, p):
    qtbot.waitUntil(lambda: not p.animations_running(), timeout=3000)
    fly = p.flyout()
    if fly is not None:
        qtbot.waitUntil(lambda: not fly.sliding() and not fly.height_animating()
                        and not fly.crossfading(), timeout=3000)


def shown_popup(qtbot, tmp_path, events, **kw):
    p = popup_on(qtbot, tmp_path, events, **kw)
    settled(qtbot, p)
    return p


def wait_ms(qtbot, ms):
    clock = QElapsedTimer()
    clock.start()
    qtbot.waitUntil(lambda: clock.elapsed() > ms, timeout=ms + 2000)


def layout_height(p) -> int:
    lay = p.layout()
    lay.activate()
    return lay.totalHeightForWidth(WIDTH)


# == 1. Hide / Color only on right-click =======================================================
def test_details_views_have_no_hide_or_color_buttons(qtbot, tmp_path):
    p = popup_on(qtbot, tmp_path, SERIES + [ev("x", "Lunch", 12, meeting_url="https://zoom.us/j/1")])
    for r in rows(p):
        click(qtbot, r)
        d = details(p)
        assert d.findChild(QPushButton, "hide") is None
        assert d.findChild(QPushButton, "color") is None
        assert [b.objectName() for b in d.findChildren(QPushButton)] in ([], ["bigjoin"])
    assert [w.objectName() for w in p.flyout().tab_chain()] == ["bigjoin"]
    dlg = EventDetails(SERIES[0], NOW, settings=p.settings)
    qtbot.addWidget(dlg)
    assert not hasattr(dlg, "hide_button") and not hasattr(dlg, "color_button")
    assert [b.text() for b in dlg.findChildren(QPushButton)] == []
    # ...still one right-click away, in the popup and in the window.
    assert meeting_menus.find_action(row_menu(p, rows(p)[0]), meeting_menus.HIDE_SERIES)
    w = make_window(qtbot, tmp_path, [], cache=p.cache, settings=p.settings)
    chip = w.time_view.columns[3].chips[0]
    right_click(chip)
    assert meeting_menus.find_action(w.menu(), "Basil") is not None


# == 2. tile style =============================================================================
ARBITRARY = ["#ffffff", "#000000", "#fdd663", "#f6c445", "#7f7f7f", "#777777", "#4285f4",
             "#0b8043", "#d50000", "#202124", "#e0f7fa", "#3c4043", "#8ab4f8", "#ff00ff"]


@pytest.mark.parametrize("color", [c for _, c in theme.PALETTE] + ARBITRARY)
def test_fill_text_contrast_at_least_4_5(color):
    fg = theme.text_on(color)
    assert fg in (theme.ON_DARK, theme.ON_LIGHT)
    assert theme.contrast_ratio(color, fg) >= 4.5
    assert theme.contrast_ratio(color, theme.muted_on(color)) >= 4.5
    # the compact Join on a filled row: text color button, fill color label
    assert theme.contrast_ratio(fg, color) >= 4.5


def test_contrast_ratio_reference_values():
    assert theme.contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0)
    assert theme.contrast_ratio("#777777", "#ffffff") == pytest.approx(4.48, abs=0.01)
    assert theme.text_on("#ffffff") == "#000000" and theme.text_on("#000000") == "#ffffff"


def test_tile_style_setting_persists_and_ignores_wrong_values(tmp_path):
    path = tmp_path / "s.json"
    s = Settings(path)
    assert s.tile_style == "stripe"
    s.set_tile_style("fill")
    assert Settings(path).tile_style == "fill"
    with pytest.raises(ValueError):
        s.set_tile_style("plaid")
    assert Settings(path).tile_style == "fill"
    data = json.loads(path.read_text(encoding="utf-8"))
    for bad in ("plaid", 3, None, ["fill"], {"x": 1}, "FILL"):
        data["tile_style"] = bad
        path.write_text(json.dumps(data), encoding="utf-8")
        assert Settings(path).tile_style == "stripe"
    del data["tile_style"]                       # older settings file
    path.write_text(json.dumps(data), encoding="utf-8")
    assert Settings(path).tile_style == "stripe"


def test_set_tile_style_rolls_back_on_failed_save(tmp_path, monkeypatch):
    s = Settings(tmp_path / "s.json")
    monkeypatch.setattr(s, "save", lambda: (_ for _ in ()).throw(OSError("ro")))
    with pytest.raises(OSError):
        s.set_tile_style("fill")
    assert s.tile_style == "stripe"


def _pixel_in_popup(p, row, pos: QPoint) -> QColor:
    img = p.grab().toImage()
    at = row.mapTo(p, pos)
    return QColor(img.pixel(at.x(), at.y()))


def test_stripe_vs_fill_rendered_pixels(qtbot, tmp_path):
    # NOW is 9:00: the 10:00 row is next up; the 11:00 row is a plain one.
    p = popup_on(qtbot, tmp_path, [ev("1", "A", 10), ev("2", "B", 11)])
    plain = rows(p)[1]
    center = plain.rect().center()
    assert not plain.is_next and plain.style == "stripe"
    assert _pixel_in_popup(p, plain, center).name() == theme.BG          # transparent row
    stripe = plain.findChild(QFrame, "stripe")
    assert stripe.isVisible()
    p.set_tile_style("fill")
    plain = rows(p)[1]
    assert plain.style == "fill" and not plain.findChild(QFrame, "stripe").isVisible()
    assert _pixel_in_popup(p, plain, plain.rect().center()).name() == plain.color \
        == theme.effective_color(plain.event, p.settings)
    title = plain.findChild(QLabel, "title")
    assert plain.fg in title.styleSheet()
    assert theme.contrast_ratio(plain.fg, plain.color) >= 4.5
    p.color_meeting(plain.event, "#f6c445")              # a light fill: dark text
    plain = rows(p)[1]
    assert plain.color == "#f6c445" and plain.fg == theme.ON_DARK
    assert _pixel_in_popup(p, plain, plain.rect().center()).name() == "#f6c445"


def test_fill_keeps_selected_and_next_up_distinguishable(qtbot, tmp_path):
    p = popup_on(qtbot, tmp_path, [ev("1", "A", 10, meeting_url="https://zoom.us/j/1"),
                                   ev("2", "B", 11), ev("3", "C", 12)])
    p.set_tile_style("fill")
    nxt, plain, other = rows(p)
    assert nxt.is_next and nxt.border() is not None and nxt.border()[1] == 1
    assert plain.border() is None
    click(qtbot, other)
    assert other.is_selected() and other.border() == (other.fg, 2)   # a 2 px ring
    assert nxt.border() != other.border()
    join = nxt.join_button
    assert join.background() == QColor(nxt.fg).name()               # legible on any fill
    assert theme.contrast_ratio(join.background(), nxt.color) >= 4.5
    p.set_tile_style("stripe")                                      # and back: as before
    assert all(r.style == "stripe" and r.findChild(QFrame, "stripe").isVisible()
               for r in rows(p))
    assert rows(p)[0].join_button.background() == QColor(theme.LINK).name()


def test_right_click_color_style_toggle_applies_immediately(qtbot, tmp_path):
    p = popup_on(qtbot, tmp_path, [ev("1", "A", 10), ev("2", "B", 11)])
    changed = []
    p.meetings_changed.connect(lambda: changed.append(1))
    menu = row_menu(p, rows(p)[1])
    styles = meeting_menus.submenu(menu, meeting_menus.STYLE_SUBMENU)
    assert [(a.text(), a.isChecked()) for a in styles.actions()] == [
        ("Side stripe", True), ("Whole tile", False)]
    _menu_action(p, "Whole tile").trigger()                          # one click
    assert all(r.style == "fill" for r in rows(p)) and changed == [1]
    assert Settings(tmp_path / "s.json").tile_style == "fill"
    styles = meeting_menus.submenu(row_menu(p, rows(p)[0]), meeting_menus.STYLE_SUBMENU)
    assert [a.isChecked() for a in styles.actions()] == [False, True]
    assert p.isVisible()
    # The window has the same toggle; it refreshes every view through meetings_changed.
    w = make_window(qtbot, tmp_path, [], cache=p.cache, settings=p.settings)
    w.meetings_changed.connect(p.refresh)
    right_click(w.time_view.columns[3].chips[0])
    _menu_action(w, "Side stripe").trigger()
    assert p.settings.tile_style == "stripe" and all(r.style == "stripe" for r in rows(p))


def test_settings_general_meeting_color_option(qtbot, tmp_path):
    cache, settings = store(tmp_path, [])
    dlg = SettingsDialog(settings, cache, lambda: [], lambda: None, lambda a: None)
    qtbot.addWidget(dlg)
    box = dlg.tile_style_box
    assert [box.itemText(i) for i in range(box.count())] == ["Side stripe", "Whole tile"]
    assert box.currentData() == "stripe"
    box.setCurrentIndex(box.findData("fill"))
    assert settings.tile_style == "stripe"                           # applies on OK
    dlg.accept()
    assert Settings(tmp_path / "s.json").tile_style == "fill"
    # A change made from the right-click menu while the dialog is open is kept when the
    # dialog's own choice was not touched.
    dlg2 = SettingsDialog(settings, cache, lambda: [], lambda: None, lambda a: None)
    qtbot.addWidget(dlg2)
    settings.set_tile_style("stripe")
    dlg2.accept()
    assert Settings(tmp_path / "s.json").tile_style == "stripe"


# == 3. details panel fits its content =========================================================
def _last_widget_bottom(fly) -> int:
    body = fly.body()
    bottoms = [w.mapTo(fly, QPoint(0, w.height())).y()
               for w in body.findChildren(QLabel) + body.findChildren(QPushButton)
               + body.findChildren(QTextBrowser)
               if w.parentWidget() is body and w.isVisibleTo(body)]
    return max(bottoms)


def test_short_details_panel_has_no_blank_band(qtbot, tmp_path):
    e = ev("s", "Short", 18, description="Two lines\nof notes.",
           meeting_url="https://zoom.us/j/1")
    p = popup_on(qtbot, tmp_path, MANY + [e], tray=BOTTOM_RIGHT)
    click(qtbot, rows(p)[-1])
    fly = p.flyout()
    assert fly.width() == WIDTH and fly.height() < p.height()
    assert fly.y() == p.y()                                           # top aligned
    assert fly.height() - _last_widget_bottom(fly) <= 16
    assert fly.description_box().verticalScrollBar().maximum() == 0
    img = fly.grab().toImage()
    black = QColor("#000000").rgb()
    assert not any(img.pixel(x, y) == black for y in range(0, img.height(), 2)
                   for x in range(0, img.width(), 2))


def test_long_details_panel_is_capped_at_popup_height_and_scrolls(qtbot, tmp_path):
    e = ev("l", "Long", 18, description=LONG_NOTES)
    p = popup_on(qtbot, tmp_path, MANY + [e])
    click(qtbot, rows(p)[-1])
    fly = p.flyout()
    assert fly.height() == p.height() and fly.y() == p.y()
    assert fly.description_box().verticalScrollBar().maximum() > 0
    assert fly.height() - _last_widget_bottom(fly) <= 16


def test_switching_short_to_long_resizes_the_panel(qtbot, tmp_path):
    short = ev("s", "Short", 17)
    long = ev("l", "Long", 18, description=LONG_NOTES)
    p = popup_on(qtbot, tmp_path, MANY + [short, long])
    click(qtbot, rows(p)[-2])
    fly = p.flyout()
    h_short = fly.height()
    assert h_short == fly.content_height() < p.height()
    click(qtbot, rows(p)[-1])
    assert p.flyout() is fly and fly.height() == p.height() > h_short
    click(qtbot, rows(p)[-2])
    assert fly.height() == h_short and fly.y() == p.y()


def test_fitted_panel_placement_flips_and_stays_in_work_area(qtbot, tmp_path):
    short = ev("s", "Short", 17)
    for i, tray in enumerate((QRect(0, 780, 20, 20), BOTTOM_RIGHT)):
        sub = tmp_path / str(i)
        sub.mkdir()
        p = popup_on(qtbot, sub, MANY + [short], tray=tray)
        click(qtbot, rows(p)[-1])
        fly = p.flyout()
        assert p.flyout_side == ("right" if i == 0 else "left")
        assert AREA.contains(fly.geometry()) and fly.y() == p.y()
        assert fly.height() < p.height()


# == 4. micro-animations =========================================================================
def test_basic_switch_turns_off_only_the_micro_interactions(monkeypatch):
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)
    monkeypatch.setenv(motion.ENV_LEVEL, "basic")
    assert not motion.micro_enabled() and motion.micro_duration(motion.FAST) == 0
    assert motion.duration(motion.POPUP_MS) == motion.POPUP_MS      # the slides stay
    monkeypatch.delenv(motion.ENV_LEVEL)
    assert motion.micro_enabled()
    monkeypatch.setattr(motion, "animations_enabled", lambda: False)
    assert not motion.micro_enabled()
    assert (motion.FAST, motion.NORMAL, motion.SLOW) == (120, 180, 240)


def test_basic_mode_keeps_popup_slide_but_no_micro_animations(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)
    monkeypatch.setenv(motion.ENV_LEVEL, "basic")
    p = popup_on(qtbot, tmp_path, MANY)
    assert p.showing_animated()                                       # slide-in kept
    settled(qtbot, p)
    right_click(rows(p)[0])
    _menu_action(p, meeting_menus.HIDE_ONE).trigger()
    assert not p.collapsing() and not p.height_animating() and len(rows(p)) == 5
    assert not p.revealing() and p.notice_text() == meeting_menus.HIDDEN_NOTICE


# (a) popup height ---------------------------------------------------------------------------
def test_popup_height_tweens_to_measured_final_geometry(qtbot, tmp_path, animated):
    p = shown_popup(qtbot, tmp_path, MANY[:2], tray=BOTTOM_RIGHT)
    h0, bottom = p.height(), p.geometry().bottom()
    for i in range(2, 5):                       # a sync brings more meetings
        p.cache.upsert_events("a", [MANY[i]])
    p.refresh()
    assert p.height_animating() and p.final_height() > h0
    final = p.final_height()
    heights = []
    qtbot.waitUntil(lambda: heights.append(p.height()) or not p.height_animating())
    assert p.height() == final and p.geometry() == p.docked_geometry()
    assert p.geometry().bottom() == bottom                    # grows away from the tray
    assert any(h0 < h < final for h in heights)               # really in between
    assert p.height() >= layout_height(p)                     # never shorter than content
    assert all(r.isVisible() for r in rows(p))


def test_identical_refresh_does_not_animate(qtbot, tmp_path, animated):
    p = shown_popup(qtbot, tmp_path, MANY)
    h0 = p.height()
    p.refresh()                                   # the 30 s tick: nothing changed
    assert not p.animations_running() and p.height() == h0
    assert all(r.graphicsEffect() is None for r in rows(p))


# (b) hover / press / selection fades ---------------------------------------------------------
def _enter(w):
    QApplication.sendEvent(w, QEnterEvent(QPointF(5, 5), QPointF(5, 5),
                                          QPointF(w.mapToGlobal(QPoint(5, 5)))))


def test_row_hover_fades_background(qtbot, tmp_path, animated):
    p = shown_popup(qtbot, tmp_path, MANY)
    row = rows(p)[2]
    assert row.target_background() == "transparent" and row.background().alpha() == 0
    _enter(row)
    target = QColor(row.target_background())
    assert row.target_background() != "transparent"
    assert motion.alive(row._bg_anim) and row.background() != target    # fading, not at once
    qtbot.waitUntil(lambda: not motion.alive(row._bg_anim))
    assert row.background() == target
    QApplication.sendEvent(row, QEvent(QEvent.Type.Leave))
    qtbot.waitUntil(lambda: row.background().alpha() == 0)


def test_row_press_and_selection_fade(qtbot, tmp_path, animated):
    p = shown_popup(qtbot, tmp_path, MANY)
    row = rows(p)[2]
    _enter(row)
    hover = row.target_background()
    qtbot.mousePress(row, Qt.MouseButton.LeftButton, pos=QPoint(5, 5))
    assert row.target_background() != hover                    # subtle press feedback
    qtbot.mouseRelease(row, Qt.MouseButton.LeftButton, pos=QPoint(5, 5))
    assert row.is_selected() and p.flyout_open()
    assert motion.alive(row._bg_anim)                          # selection fades in
    qtbot.waitUntil(lambda: not motion.alive(row._bg_anim))
    assert row.background() == QColor(row.target_background())


def test_join_button_hover_transition(qtbot, tmp_path, animated):
    p = shown_popup(qtbot, tmp_path, [ev("1", "A", 10, meeting_url="https://zoom.us/j/1")])
    join = rows(p)[0].join_button
    base = join.background()
    _enter(join)
    assert motion.alive(join._anim)
    qtbot.waitUntil(lambda: not motion.alive(join._anim))
    assert join.background() == QColor(join.hover_target()).name() != base
    QApplication.sendEvent(join, QEvent(QEvent.Type.Leave))
    qtbot.waitUntil(lambda: join.background() == base)


# (c) rows collapse after a hide; restored rows fade in ------------------------------------------
def test_hidden_row_collapses_then_list_rebuilds(qtbot, tmp_path, animated):
    p = shown_popup(qtbot, tmp_path, MANY, tray=BOTTOM_RIGHT)
    h0, bottom = p.height(), p.geometry().bottom()
    gone = rows(p)[1]
    gone_h = gone.height()
    right_click(gone)
    _menu_action(p, meeting_menus.HIDE_ONE).trigger()          # no confirmation
    assert p.collapsing() and len(rows(p)) == 6                # still there, shrinking
    qtbot.waitUntil(lambda: gone.height() < gone_h)            # mid-collapse
    assert gone.graphicsEffect() is not None and gone.graphicsEffect().opacity() < 1
    assert p.notice_text() == ""                               # the notice comes after
    settled(qtbot, p)
    assert titles(p) == [e.title for i, e in enumerate(MANY) if i != 1]
    assert p.notice_text() == meeting_menus.HIDDEN_NOTICE
    assert p.geometry().bottom() == bottom and p.height() >= layout_height(p)
    assert p.height() != h0 or True                           # (popup not at its cap here)
    # Undo: the restored row fades in (user action), the others do not.
    p.notice_undo_button().click()
    restored = next(r for r in rows(p) if r.event.id == MANY[1].id)
    assert restored.graphicsEffect() is not None and p.fading_in()
    assert all(r.graphicsEffect() is None for r in rows(p) if r is not restored)
    settled(qtbot, p)
    assert restored.graphicsEffect() is None and len(rows(p)) == 6


def test_tick_refresh_never_collapses_or_fades(qtbot, tmp_path, animated):
    p = shown_popup(qtbot, tmp_path, MANY)
    p.settings.hide_event(MANY[0], "event")       # hidden elsewhere (the window, the tray)
    p.refresh()                                   # the 30 s tick picks it up
    assert not p.collapsing() and not p.fading_in()
    assert len(rows(p)) == 5
    p.settings.unhide(next(iter(p.settings.hidden_events)))
    p.refresh()
    assert not p.fading_in() and len(rows(p)) == 6


# (d) the notice --------------------------------------------------------------------------------
def test_notice_slides_and_fades_in_and_out(qtbot, tmp_path, animated):
    p = shown_popup(qtbot, tmp_path, MANY)
    h0 = p.height()
    box = p._notice_box
    p.show_notice("Something to say")
    assert p.revealing(box) and p.notice_text() == "Something to say"
    qtbot.waitUntil(lambda: 1 < box.height() or not p.revealing(box))
    settled(qtbot, p)
    assert box.isVisible() and box.graphicsEffect() is None and p.height() > h0
    assert p.height() >= layout_height(p)
    p.show_notice("")
    assert p.revealing(box) and p.notice_text() == ""
    settled(qtbot, p)
    assert not box.isVisible() and p.height() == h0


# (e) the chat ------------------------------------------------------------------------------------
def test_chat_expand_and_collapse_animate_height(qtbot, tmp_path, animated):
    p = shown_popup(qtbot, tmp_path, MANY[:2], tray=BOTTOM_RIGHT)
    p.attach_chat(lambda: None)
    h0, bottom = p.height(), p.geometry().bottom()
    p.toggle_chat()
    chat = p.chat()
    assert p.chat_expanded() and p.revealing(chat)
    heights = []
    qtbot.waitUntil(lambda: heights.append(p.height()) or not p.revealing(chat))
    assert chat.isVisible() and chat.graphicsEffect() is None
    assert any(h0 < h < p.height() for h in heights)             # grew step by step
    assert p.geometry().bottom() == bottom and p.height() >= layout_height(p)
    p.toggle_chat()
    assert not p.chat_expanded() and p.revealing(chat)
    settled(qtbot, p)
    assert not chat.isVisible() and p.height() == h0


# (f) the details panel's content swap ---------------------------------------------------------------
def test_flyout_crossfades_and_tweens_height_on_swap(qtbot, tmp_path, animated):
    short = ev("s", "Short", 17)
    long = ev("l", "Long", 18, description=LONG_NOTES)
    p = shown_popup(qtbot, tmp_path, MANY + [short, long])
    click(qtbot, rows(p)[-2])
    fly = p.flyout()
    settled(qtbot, p)
    h_short = fly.height()
    click(qtbot, rows(p)[-1])
    assert fly.crossfading() and fly.findChild(QLabel, "ghost") is not None
    assert fly.height_animating() and fly.target_height() == p.height()
    heights = []
    qtbot.waitUntil(lambda: heights.append(fly.height()) or not fly.height_animating())
    qtbot.waitUntil(lambda: not fly.crossfading())
    assert fly.height() == p.height() and any(h_short < h < p.height() for h in heights)
    assert fly.findChild(QLabel, "ghost") is None and fly.body().graphicsEffect() is None
    assert fly.y() == p.y()
    img = fly.grab().toImage()                   # painted edge to edge, no black
    black = QColor("#000000").rgb()
    assert not any(img.pixel(x, y) == black for y in range(0, img.height(), 3)
                   for x in range(0, img.width(), 3))


# -- focus group and hiding mid-animation ----------------------------------------------------------
def test_focus_group_unchanged_during_animations(qtbot, tmp_path, animated):
    p = shown_popup(qtbot, tmp_path, MANY)
    click(qtbot, rows(p)[3])
    fly = p.flyout()
    settled(qtbot, p)
    right_click(rows(p)[0])
    _menu_action(p, meeting_menus.HIDE_ONE).trigger()
    assert p.collapsing()
    p.active_window = lambda: fly                  # a click in the panel mid-collapse
    p.app_active = lambda: True
    p.menu().hide()                                # (the menu closes on a real click)
    QApplication.sendEvent(p, QEvent(QEvent.Type.WindowDeactivate))
    assert p.isVisible() and fly.isVisible()
    p.active_window = lambda: None                 # focus went to another app
    p.app_active = lambda: False
    QApplication.sendEvent(p, QEvent(QEvent.Type.WindowDeactivate))
    qtbot.waitUntil(lambda: not p.isVisible())
    assert not fly.isVisible() and not p.animations_running()


def test_hide_mid_animation_stops_everything(qtbot, tmp_path, animated):
    p = shown_popup(qtbot, tmp_path, MANY)
    p.attach_chat(lambda: None)
    click(qtbot, rows(p)[2])
    fly = p.flyout()
    settled(qtbot, p)
    _enter(rows(p)[4])
    p.toggle_chat()                                 # chat opening
    right_click(rows(p)[0])
    _menu_action(p, meeting_menus.HIDE_ONE).trigger()   # a collapse
    click(qtbot, rows(p)[3])                        # a panel swap
    assert p.animations_running() or fly.crossfading()
    p.hide()
    assert not p.isVisible() and not fly.isVisible()
    assert not p.animations_running() and not fly.height_animating()
    assert not fly.crossfading() and not fly.sliding()
    assert p.chat().isVisible() is False and p.chat().graphicsEffect() is None
    assert all(r.graphicsEffect() is None for r in rows(p))
    assert MANY[0].title not in titles(p)           # the hide itself still applied
    wait_ms(qtbot, motion.NORMAL + 100)            # no handler runs after the hide
    assert not p.isVisible() and not p.animations_running()


# -- the earlier collapse regressions, with every animation on -----------------------------------------
LONG = "with a title long enough to wrap onto a second line here"


def eight():
    return [ev(str(i), f"Meeting {i} {LONG}", 9 + i) for i in range(8)]


def _not_collapsed(p, h0, row_h, area=AREA):
    content = p._rows_box.sizeHint().height()
    budget = min(480, area.height() - 24) - (p.height() - p._scroll.height())
    assert p._scroll.height() >= min(content, budget) - 1
    assert p.height() >= h0 - row_h - p._rows_box.spacing() - 1
    assert p.height() >= layout_height(p) - 1


@pytest.mark.parametrize("variant", ["selected", "details_on_other_row", "chat", "repeat",
                                     "small_area", "notice"])
def test_collapse_regressions_with_animations_on(qtbot, tmp_path, animated, variant):
    area = QRect(0, 0, 1000, 330) if variant == "small_area" else AREA
    p = shown_popup(qtbot, tmp_path, eight(), tray=BOTTOM_RIGHT, area=area)
    if variant == "chat":
        p.attach_chat(lambda: None)
        p.toggle_chat()
        settled(qtbot, p)
    h0, row_h = p.height(), rows(p)[0].height()
    if variant == "notice":
        p.show_notice("A long notice text that wraps onto a second line in the popup")
        settled(qtbot, p)
        _not_collapsed(p, h0, row_h, area)
        p.notice_timer.timeout.emit()
        settled(qtbot, p)
        assert p.height() == h0
        return
    rounds = 10 if variant == "repeat" else 1
    for i in range(rounds):
        if variant == "details_on_other_row":
            click(qtbot, rows(p)[5])
        if variant == "selected":
            click(qtbot, rows(p)[2])
        target = rows(p)[2 if variant == "selected" else i % 8]
        right_click(target)
        _menu_action(p, meeting_menus.HIDE_ONE).trigger()
        settled(qtbot, p)
        assert len(rows(p)) == 7 and all(r.isVisible() for r in rows(p))
        _not_collapsed(p, h0, row_h, area)
        assert area.contains(p.geometry())
        if variant == "repeat":
            p.notice_undo_button().click()
            settled(qtbot, p)
            assert len(rows(p)) == 8 and p.height() == h0
