"""v0.8.1 review fixes for the v0.8.0 motion and layout: one native geometry change per
frame with the tray-side edge docked (all four taskbar edges), no fades on a refresh that
changed nothing, no scrollbar flashes or stretched rows during tweens, the cross-fade
snapshot placed right at 1.5x, quick successive hides, the details panel's spare height
kept below its content, and the minor polish (contrast-safe hover/press, radio "Color
style", Settings follows the right-click toggle, chat reversal, DPR-aware cache, painted
button backgrounds, no parentless row widgets). Offscreen Qt; animations driven
deterministically through each animation's clock."""
from datetime import timedelta

import pytest
from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication, QLabel

from smartcal.settings import Settings
from smartcal.ui import meeting_menus, motion, placement, theme
from smartcal.ui.popup import WIDTH, AgendaRow
from smartcal.ui.settings_dialog import SettingsDialog
from test_meeting_actions_ui import (NOW, _menu_action, click, details, ev, right_click,
                                     row_menu, rows, store, titles)
from test_polish_ui import (MANY, _enter, animated, frames, layout_height,  # noqa: F401
                            settled, shown_popup)
from test_popup_flyout import AREA, BOTTOM_RIGHT, TOP_TRAY

EDGES = {
    "bottom": BOTTOM_RIGHT,
    "top": TOP_TRAY,
    "left": QRect(-30, 600, 20, 20),       # a vertical taskbar left of the work area
    "right": QRect(1010, 600, 20, 20),     # ...and right of it
}


def spy(p, names=("setGeometry", "resize", "move", "repaint")):
    calls = {n: 0 for n in names}
    for n in names:
        real = getattr(p, n)

        def wrapper(*a, _n=n, _real=real, **k):
            calls[_n] += 1
            return _real(*a, **k)
        setattr(p, n, wrapper)
    return calls


def docked_edge(p, edge) -> int:
    g = p.geometry()
    return {"bottom": g.bottom(), "top": g.top(), "left": g.left(),
            "right": g.right()}[edge]


def _drive_and_check(qtbot, p, edge, grow: bool):
    anim = p.height_animation()
    assert anim is not None
    calls = spy(p)
    anchor = docked_edge(p, edge)
    last_h = p.height()
    seen = []
    for _ in frames(anim, 8):
        g = p.geometry()
        seen.append(calls["setGeometry"])
        assert calls["setGeometry"] <= 1 and calls["resize"] == 0 and calls["move"] == 0
        if calls["setGeometry"] and grow and g.height() > last_h:
            assert calls["repaint"] >= 1         # exposed pixels painted this frame
        assert g == QRect(placement.compute_position(g.size(), p._tray_geom, AREA),
                          g.size())               # docked for its size, every frame
        assert docked_edge(p, edge) == anchor     # the tray-side edge never moves
        assert AREA.contains(g)
        last_h = g.height()
        for k in calls:
            calls[k] = 0
    assert sum(seen) >= len(seen) // 2              # it really moved (eased: the first
                                                    # frames of a collapse may round to 0)
    anim.resume()
    qtbot.waitUntil(lambda: not p.height_animating())
    assert p.height() == p.final_height() == layout_height(p)
    assert docked_edge(p, edge) == anchor


@pytest.mark.parametrize("edge", list(EDGES))
def test_one_geometry_change_per_frame_with_tray_edge_docked(qtbot, tmp_path, animated, edge):
    p = shown_popup(qtbot, tmp_path, MANY[:2], tray=EDGES[edge])
    for i in range(2, 5):                       # a sync brings more meetings: it grows
        p.cache.upsert_events("a", [MANY[i]])
    p.refresh()
    _drive_and_check(qtbot, p, edge, grow=True)
    for i in range(2, 5):                       # ...and they go again: it shrinks
        p.cache.delete_event("a", "primary", MANY[i].id)
    p.refresh()
    _drive_and_check(qtbot, p, edge, grow=False)


@pytest.mark.parametrize("kind", ["notice", "collapse", "chat"])
def test_reveal_and_collapse_also_one_geometry_change_per_frame(qtbot, tmp_path, animated,
                                                                kind):
    p = shown_popup(qtbot, tmp_path, MANY[:4], tray=BOTTOM_RIGHT)
    if kind == "notice":
        p.show_notice("A notice for the test")
    elif kind == "collapse":
        right_click(rows(p)[1])
        _menu_action(p, meeting_menus.HIDE_ONE).trigger()
    else:
        p.attach_chat(lambda: None)
        p.toggle_chat()
    _drive_and_check(qtbot, p, "bottom", grow=kind != "collapse")


# -- I2: a refresh that changed nothing animates nothing -------------------------------------------
def test_no_change_refresh_with_details_open_and_hover_starts_nothing(qtbot, tmp_path,
                                                                      animated):
    p = shown_popup(qtbot, tmp_path, MANY)
    click(qtbot, rows(p)[2])
    fly = p.flyout()
    over = rows(p)[4]
    p.cursor_pos = lambda: over.mapToGlobal(over.rect().center())   # the mouse rests here
    _enter(over)
    settled(qtbot, p)
    qtbot.waitUntil(lambda: not p.row_fades_running())
    p.refresh()                                   # the 30 s tick
    new_hovered = rows(p)[4]
    _enter(new_hovered)                           # Qt's synthetic enter on the new row
    sel = rows(p)[2]
    assert sel.is_selected() and sel.background().name() == sel.target_background()
    assert new_hovered.is_hovered()
    assert new_hovered.background().name() == new_hovered.target_background()
    assert not p.animations_running() and not p.row_fades_running()
    assert not fly.height_animating() and not fly.crossfading() and not fly.sliding()


# -- I3: no scrollbar flash, rows keep their height ---------------------------------------------------
def test_no_list_scrollbar_and_constant_rows_during_tweens(qtbot, tmp_path, animated):
    p = shown_popup(qtbot, tmp_path, MANY[:4], tray=BOTTOM_RIGHT)
    bar = p._scroll.verticalScrollBar()
    for change in ("shrink", "grow"):
        if change == "shrink":
            p.cache.delete_event("a", "primary", MANY[3].id)
        else:
            p.cache.upsert_events("a", [MANY[3]])
        p.refresh()
        heights = {r.event.id: r.height() for r in rows(p)}
        anim = p.height_animation()
        assert anim is not None
        for _ in frames(anim, 8):
            assert not bar.isVisible()
            assert p._scroll.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            assert {r.event.id: r.height() for r in rows(p)} == heights
        anim.resume()
        settled(qtbot, p)
        assert p._scroll.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
        assert not bar.isVisible()


# -- I4: the cross-fade snapshot at 1.5x -------------------------------------------------------------
def test_crossfade_snapshot_placed_exactly_at_dpr_1_5(qtbot, tmp_path, animated):
    p = shown_popup(qtbot, tmp_path, [ev("1", "A", 10, description="Notes\n" * 4),
                                      ev("2", "B", 11), ev("3", "C", 12)])
    click(qtbot, rows(p)[0])
    fly = p.flyout()
    settled(qtbot, p)
    grabbed = []

    def grab_15(w):
        grabbed.append(w.graphicsEffect())        # never a half-faded snapshot
        pm = w.grab()
        big = pm.scaled(int(pm.width() * 1.5), int(pm.height() * 1.5))
        big.setDevicePixelRatio(1.5)
        return big
    fly._grab = grab_15
    old = fly.body()
    old_rect = QRect(old.pos(), old.size())
    click(qtbot, rows(p)[1])
    ghost = fly._ghost
    assert ghost is not None and ghost.geometry() == old_rect
    assert ghost.alignment() == (Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
    click(qtbot, rows(p)[2])                      # a second swap mid-fade
    assert grabbed == [None, None]
    settled(qtbot, p)
    assert not fly.crossfading()


# -- I5: two quick hides ------------------------------------------------------------------------------
def test_two_quick_hides_both_collapse_and_undo_restores_the_latest(qtbot, tmp_path,
                                                                     animated):
    p = shown_popup(qtbot, tmp_path, MANY)
    right_click(rows(p)[0])
    _menu_action(p, meeting_menus.HIDE_ONE).trigger()
    first = p.height_animation()
    first.pause()
    first.setCurrentTime(50)                      # 50 ms into the first collapse
    assert p.collapsing()
    menu = row_menu(p, rows(p)[3])                # the owner right-clicks another one
    menu_hide = meeting_menus.find_action(menu, meeting_menus.HIDE_ONE)
    menu_hide.trigger()
    assert p.collapsing()                         # the second one collapses too
    assert MANY[0].title not in titles(p)         # the first finished first
    settled(qtbot, p)
    assert titles(p) == [e.title for e in MANY[1:3] + MANY[4:]]
    assert p.notice_text() == meeting_menus.HIDDEN_NOTICE
    p.notice_undo_button().click()                # Undo: the latest hide
    assert titles(p) == [e.title for e in MANY[1:]]
    assert p.settings.is_event_hidden(MANY[0])


# -- I6: the details panel keeps spare height below its content -----------------------------------------
def test_title_only_meeting_lines_do_not_drift_in_a_taller_panel(qtbot, tmp_path):
    p = shown_popup(qtbot, tmp_path, MANY + [ev("t", "Title only", 18)])
    click(qtbot, rows(p)[-1])
    fly = p.flyout()
    d = details(p)
    title, when = d.findChild(QLabel, "title"), d.findChild(QLabel, "when")

    def spacing():
        return (title.mapTo(fly, QPoint(0, 0)).y(), when.mapTo(fly, QPoint(0, 0)).y())
    before = spacing()
    assert fly.height() == max(96, fly.content_height())
    fly.setFixedSize(WIDTH, 300)                  # e.g. mid-way through a shrink
    QApplication.processEvents()
    assert spacing() == before


def test_no_panel_scrollbar_during_short_long_tweens(qtbot, tmp_path, animated):
    short = ev("s", "Short", 17)
    longer = ev("l", "Longer", 18, description="\n".join(f"line {i}" for i in range(6)),
                location="Room 4")
    p = shown_popup(qtbot, tmp_path, MANY + [short, longer])
    click(qtbot, rows(p)[-2])
    fly = p.flyout()
    settled(qtbot, p)
    bar = fly._scroll.verticalScrollBar()
    for target in (rows(p)[-1], rows(p)[-2]):     # short -> longer -> short
        click(qtbot, target)
        assert fly.content_height() <= p.height()  # the final content fits
        anim = fly._height_anim
        assert anim is not None
        for _ in frames(anim, 8):
            assert not bar.isVisible()
        anim.resume()
        settled(qtbot, p)
        assert not bar.isVisible() and fly.height() == fly.content_height()


# -- minor ----------------------------------------------------------------------------------------------
def test_fill_rest_hover_press_keep_contrast_for_every_palette_color(qtbot, tmp_path):
    events = [ev(str(i), f"M{i}", 10 + i % 8, meeting_url="https://zoom.us/j/1")
              for i in range(len(theme.PALETTE))]
    cache, settings = store(tmp_path, events)
    for e, (_, color) in zip(events, theme.PALETTE):
        settings.set_event_color(e, color)
    settings.set_tile_style("fill")
    from smartcal.ui.popup import AgendaPopup
    p = AgendaPopup(cache, settings)
    p.now = lambda: NOW
    qtbot.addWidget(p)
    p.refresh()
    assert len(rows(p)) == len(theme.PALETTE)
    for r in rows(p):
        for hovered, pressed in ((False, False), (True, False), (True, True)):
            r._hovered, r._pressed = hovered, pressed
            assert theme.contrast_ratio(r.fg, r.target_background()) >= 4.5, r.color
        j = r.join_button
        for bg in (j.background(), j.hover_target(), j.pressed_color()):
            assert theme.contrast_ratio(bg, r.color) >= 4.5, (r.color, bg)


def test_color_style_actions_are_exclusive_radio_marks(qtbot, tmp_path):
    p = shown_popup(qtbot, tmp_path, [ev("1", "A", 10)])
    styles = meeting_menus.submenu(row_menu(p, rows(p)[0]), meeting_menus.STYLE_SUBMENU)
    acts = styles.actions()
    assert acts[0].actionGroup() is not None and acts[0].actionGroup().isExclusive()
    assert acts[0].actionGroup() is acts[1].actionGroup()


def test_settings_combo_follows_right_click_style_change(qtbot, tmp_path):
    p = shown_popup(qtbot, tmp_path, [ev("1", "A", 10)])
    dlg = SettingsDialog(p.settings, p.cache, lambda: [], lambda: None, lambda a: None)
    qtbot.addWidget(dlg)
    p.set_tile_style("fill")
    dlg.sync_from_settings()                      # run.py: on meetings_changed
    assert dlg.tile_style_box.currentData() == "fill"
    dlg.accept()
    assert Settings(p.settings.path).tile_style == "fill"
    dlg2 = SettingsDialog(p.settings, p.cache, lambda: [], lambda: None, lambda a: None)
    qtbot.addWidget(dlg2)
    dlg2.tile_style_box.setCurrentIndex(dlg2.tile_style_box.findData("stripe"))   # touched
    p.settings.set_tile_style("fill")
    dlg2.sync_from_settings()
    assert dlg2.tile_style_box.currentData() == "stripe"   # the owner's choice stays


def test_chat_reversal_continues_from_current_height_and_opacity(qtbot, tmp_path, animated):
    p = shown_popup(qtbot, tmp_path, MANY[:2], tray=BOTTOM_RIGHT)
    p.attach_chat(lambda: None)
    p.toggle_chat()
    settled(qtbot, p)
    chat = p.chat()
    full = chat.height()
    p.toggle_chat()                               # collapsing...
    anim = p.height_animation()
    anim.pause()
    anim.setCurrentTime(anim.duration() // 2)
    mid_h, mid_op = chat.height(), chat.graphicsEffect().opacity()
    assert 1 < mid_h < full and 0 < mid_op < 1
    p.toggle_chat()                               # ...and open again, from there
    again = p.height_animation()
    again.pause()
    again.setCurrentTime(1)
    QApplication.processEvents()
    assert abs(chat.maximumHeight() - mid_h) <= 3
    assert abs(chat.graphicsEffect().opacity() - mid_op) < 0.05
    again.resume()
    settled(qtbot, p)
    assert chat.isVisible() and chat.height() == full and p.chat_expanded()


def test_panel_content_height_cache_includes_device_pixel_ratio(qtbot, tmp_path,
                                                                monkeypatch):
    p = shown_popup(qtbot, tmp_path, [ev("1", "A", 10, description="Notes")])
    click(qtbot, rows(p)[0])
    fly = p.flyout()
    measured = []
    real = fly._measure_content_height
    monkeypatch.setattr(fly, "_measure_content_height",
                        lambda: measured.append(1) or real())
    fly.content_height()
    fly.content_height()
    assert measured == []                          # cached
    monkeypatch.setattr(fly, "devicePixelRatioF", lambda: 2.0)   # moved to a 200% screen
    fly.content_height()
    assert measured == [1]


def test_hover_button_paints_its_background_without_restyling(qtbot, tmp_path, animated):
    p = shown_popup(qtbot, tmp_path, [ev("1", "A", 10, meeting_url="https://zoom.us/j/1")])
    join = rows(p)[0].join_button
    sheet = join.styleSheet()
    base = join.background()
    _enter(join)
    for _ in frames(join._anim, 4):
        assert join.styleSheet() == sheet          # a frame is a repaint, not a restyle
    join._anim.resume()
    qtbot.waitUntil(lambda: not join.fading())
    assert join.background() == join.hover_target() != base
    img = join.grab().toImage()
    assert img.pixelColor(2, join.height() // 2).name() == join.background()


def test_rows_never_open_parentless_windows(qtbot, tmp_path, caplog):
    before = {id(w) for w in QApplication.topLevelWidgets() if w.isVisible()}
    for style in ("stripe", "fill"):
        r = AgendaRow(ev("1", "A", 10), "10:00 AM", False, "#3fa364", style=style)
        qtbot.addWidget(r)
        assert all(c.parentWidget() is not None for c in r.findChildren(QLabel))
        stripe = r.findChild(type(r).__mro__[1], "stripe")
        assert stripe.parentWidget() is r
        with caplog.at_level("WARNING"):
            r.show()                              # the guard: never a window of its own
        QApplication.processEvents()
        assert not r.isVisible()
        assert "without a parent" in caplog.text
    after = {id(w) for w in QApplication.topLevelWidgets() if w.isVisible()}
    assert after <= before


# == v0.8.2 ===================================================================================
LONG_NOTES = "\n".join(f"line {i} of the notes" for i in range(120))


def test_capped_panel_follows_popup_tween_with_one_geometry_change(qtbot, tmp_path, animated):
    p = shown_popup(qtbot, tmp_path, MANY[:3] + [ev("l", "Long", 18, description=LONG_NOTES)],
                    tray=BOTTOM_RIGHT)
    click(qtbot, rows(p)[-1])
    fly = p.flyout()
    settled(qtbot, p)
    assert fly.height() == p.height()             # capped at the popup's height
    calls = spy(fly, ("setGeometry", "resize", "move", "setFixedSize"))
    for i in (3, 4):                              # a sync: the popup grows (upward)
        p.cache.upsert_events("a", [MANY[i]])
    p.refresh()
    anim = p.height_animation()
    assert anim is not None
    seen = 0
    for _ in frames(anim, 8):
        assert calls["resize"] == 0 and calls["move"] == 0 and calls["setFixedSize"] == 0
        assert calls["setGeometry"] <= 1
        seen += calls["setGeometry"]
        assert fly.geometry().bottom() <= p.geometry().bottom()   # never pokes below
        assert fly.y() == p.y() and fly.height() == p.height()
        for k in calls:
            calls[k] = 0
    assert seen >= 4
    anim.resume()
    settled(qtbot, p)
    assert fly.geometry().bottom() == p.geometry().bottom() and fly.y() == p.y()


def test_notice_slides_in_after_the_collapse(qtbot, tmp_path, animated):
    p = shown_popup(qtbot, tmp_path, MANY)
    right_click(rows(p)[1])
    _menu_action(p, meeting_menus.HIDE_ONE).trigger()
    collapse = p.height_animation()
    collapse.pause()
    collapse.setCurrentTime(collapse.duration())  # the collapse completes
    QApplication.processEvents()
    assert not p.collapsing()
    assert p.revealing(p._notice_box)             # the notice is sliding in, not snapped
    reveal = p.height_animation()
    assert 0 <= reveal.currentTime() < reveal.duration()
    assert p._notice_box.height() < p._natural_height(p._notice_box)
    settled(qtbot, p)
    assert p.notice_text() == meeting_menus.HIDDEN_NOTICE
    assert p.height() == layout_height(p)


def test_collapse_keeps_focus_and_scroll_position(qtbot, tmp_path, animated):
    long = "with a title long enough to wrap onto a second line here"
    events = [ev(str(i), f"Meeting {i} {long}", 9 + i) for i in range(8)]
    p = shown_popup(qtbot, tmp_path, events, area=QRect(0, 0, 1000, 330), tray=BOTTOM_RIGHT)
    bar = p._scroll.verticalScrollBar()
    assert bar.maximum() > 0
    bar.setValue(bar.maximum())                    # scrolled to the bottom
    target = rows(p)[6]
    target.setFocus()
    qtbot.waitUntil(lambda: QApplication.focusWidget() is target)
    value = bar.value()
    right_click(target)
    _menu_action(p, meeting_menus.HIDE_ONE).trigger()
    assert p.collapsing()
    assert bar.value() == value and QApplication.focusWidget() is target
    settled(qtbot, p)
    assert target.event.title not in titles(p)


def test_only_the_row_under_the_cursor_looks_hovered_after_a_refresh(qtbot, tmp_path,
                                                                     animated):
    p = shown_popup(qtbot, tmp_path, MANY)
    first = rows(p)[1]
    point = first.mapToGlobal(first.rect().center())
    p.cursor_pos = lambda: point                  # the mouse rests here
    _enter(first)
    qtbot.waitUntil(lambda: not p.row_fades_running())
    p.cache.delete_event("a", "primary", MANY[0].id)   # rows shift up under the cursor
    p.refresh()
    settled(qtbot, p)
    hovered = [r for r in rows(p) if r.is_hovered()]
    under = [r for r in rows(p) if r.rect().contains(r.mapFromGlobal(point))]
    assert hovered == under and len(hovered) == 1      # one row, the right one
    assert all(r.background().name() == QColor(r.target_background()).name()
               or r.target_background() == "transparent" for r in rows(p))
    assert not p.row_fades_running()
