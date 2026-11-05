"""v0.8.3: SmartCal's own Animations setting ("on" default / "auto" follow Windows /
"off"), overriding Windows "Animation effects" (which were off on the owner's PC, so
nothing animated). Offscreen Qt. The suite forces animations off through
SMARTCAL_NO_ANIMATIONS (conftest); these tests remove it on purpose."""
import json
import logging
from pathlib import Path

import pytest
from PyQt6.QtCore import QRect

from smartcal.settings import Settings
from smartcal.ui import meeting_menus, motion
from smartcal.ui.settings_dialog import SettingsDialog
from smartcal.ui.tray import TrayController
from test_meeting_actions_ui import _menu_action, click, ev, right_click, rows, store
from test_polish_ui import MANY, _enter
from test_popup_flyout import BOTTOM_RIGHT, popup_on


class FakeWorker:
    def sync_now(self):
        pass

    def stop(self):
        pass


@pytest.fixture
def windows_effects_off(monkeypatch):
    """The owner's PC: Windows Animation effects off, no env override."""
    monkeypatch.delenv(motion.ENV_OFF, raising=False)
    monkeypatch.delenv(motion.ENV_LEVEL, raising=False)
    monkeypatch.setattr(motion, "system_animations_enabled", lambda: False)


# -- the setting ---------------------------------------------------------------------------
def test_animations_setting_persists_and_ignores_invalid_values(tmp_path):
    path = tmp_path / "s.json"
    s = Settings(path)
    assert s.animations == "on"                      # default: the owner's preference
    for mode in ("off", "auto", "on"):
        s.set_animations(mode)
        assert Settings(path).animations == mode
    with pytest.raises(ValueError):
        s.set_animations("sometimes")
    data = json.loads(path.read_text(encoding="utf-8"))
    for bad in ("sometimes", 1, None, ["on"], {"x": 1}, "ON", True):
        data["animations"] = bad
        path.write_text(json.dumps(data), encoding="utf-8")
        assert Settings(path).animations == "on"
    del data["animations"]                           # an older settings file
    path.write_text(json.dumps(data), encoding="utf-8")
    assert Settings(path).animations == "on"
    assert not list(tmp_path.glob("*.tmp"))


def test_set_animations_rolls_back_on_failed_save(tmp_path, monkeypatch):
    s = Settings(tmp_path / "s.json")
    monkeypatch.setattr(s, "save", lambda: (_ for _ in ()).throw(OSError("ro")))
    with pytest.raises(OSError):
        s.set_animations("off")
    assert s.animations == "on"


@pytest.mark.parametrize("env", [False, True])
@pytest.mark.parametrize("mode", ["on", "auto", "off", "bogus"])
@pytest.mark.parametrize("system", [False, True])
def test_effective_mode_truth_table(monkeypatch, env, mode, system):
    monkeypatch.delenv(motion.ENV_LEVEL, raising=False)
    if env:
        monkeypatch.setenv(motion.ENV_OFF, "1")
    else:
        monkeypatch.delenv(motion.ENV_OFF, raising=False)
    monkeypatch.setattr(motion, "system_animations_enabled", lambda: system)
    motion.set_user_preference(mode)
    expected = (not env) and {"on": True, "auto": system, "off": False, "bogus": True}[mode]
    assert motion.animations_enabled() is expected
    assert motion.micro_enabled() is expected
    assert motion.duration(motion.POPUP_MS) == (motion.POPUP_MS if expected else 0)
    assert motion.user_preference() == (mode if mode != "bogus" else "on")


def test_mode_description_for_the_log(monkeypatch):
    monkeypatch.delenv(motion.ENV_OFF, raising=False)
    monkeypatch.delenv(motion.ENV_LEVEL, raising=False)
    monkeypatch.setattr(motion, "system_animations_enabled", lambda: False)
    motion.set_user_preference("on")
    assert motion.describe_mode() == \
        "animations: on (user setting; Windows animation effects are off)"
    motion.set_user_preference("auto")
    assert motion.describe_mode() == \
        "animations: off (user setting: follow Windows animation effects)"
    motion.set_user_preference("off")
    assert motion.describe_mode().startswith("animations: off (user setting")
    monkeypatch.setenv(motion.ENV_LEVEL, "basic")
    motion.set_user_preference("on")
    assert motion.describe_mode().endswith("; micro-animations off (SMARTCAL_ANIMATIONS=basic)")
    monkeypatch.setenv(motion.ENV_OFF, "1")
    assert motion.describe_mode() == "animations: off (SMARTCAL_NO_ANIMATIONS=1 is set)"


def test_startup_applies_the_setting_and_logs_one_line():
    src = (Path(__file__).resolve().parent.parent / "run.py").read_text(encoding="utf-8")
    start = src.index("    settings = Settings()\n")
    head = src[start:start + 300]
    assert "motion.set_user_preference(settings.animations)" in head
    assert "info(motion.describe_mode())" in head
    assert src.count("motion.describe_mode()") == 1      # ONE line at startup


# -- Settings > General ----------------------------------------------------------------------
def test_settings_general_animations_applies_on_ok_keeping_tray_changes(qtbot, tmp_path,
                                                                        windows_effects_off):
    cache, settings = store(tmp_path, [])
    dlg = SettingsDialog(settings, cache, lambda: [], lambda: None, lambda a: None)
    qtbot.addWidget(dlg)
    box = dlg.animations_box
    assert [box.itemText(i) for i in range(box.count())] == ["On", "Follow Windows setting",
                                                             "Off"]
    assert box.currentData() == "on" and motion.animations_enabled()
    box.setCurrentIndex(box.findData("auto"))
    assert settings.animations == "on" and motion.animations_enabled()   # applies on OK
    dlg.accept()
    assert Settings(tmp_path / "s.json").animations == "auto"
    assert not motion.animations_enabled()          # Windows effects are off: no restart
    # A tray change made while the dialog is open survives its OK when untouched here,
    # and the open dialog follows it.
    dlg2 = SettingsDialog(settings, cache, lambda: [], lambda: None, lambda a: None)
    qtbot.addWidget(dlg2)
    settings.set_animations("on")
    motion.set_user_preference("on")
    dlg2.sync_from_settings()
    assert dlg2.animations_box.currentData() == "on"
    dlg2.accept()
    assert Settings(tmp_path / "s.json").animations == "on" and motion.animations_enabled()
    dlg3 = SettingsDialog(settings, cache, lambda: [], lambda: None, lambda a: None)
    qtbot.addWidget(dlg3)
    settings.set_animations("off")                  # tray, meanwhile; untouched here
    dlg3.accept()
    assert Settings(tmp_path / "s.json").animations == "off"
    assert not motion.animations_enabled()


# -- tray menu ------------------------------------------------------------------------------
def test_tray_animations_submenu_radio_and_immediate_effect(qtbot, tmp_path,
                                                            windows_effects_off, caplog):
    cache, settings = store(tmp_path, [ev("1", "A", 10)])
    from smartcal.ui.popup import AgendaPopup
    p = AgendaPopup(cache, settings)
    qtbot.addWidget(p)
    changed = []
    tray = TrayController(p, FakeWorker(), cache, settings, accounts_fn=lambda: [],
                          on_visibility_changed=lambda: changed.append(1))
    tray.showMessage = lambda *a, **k: None
    tray.contextMenu().aboutToShow.emit()
    menu = tray.animations_menu
    assert menu.menuAction() in tray.contextMenu().actions()
    acts = menu.actions()
    assert [a.text() for a in acts] == ["On", "Follow Windows", "Off"]
    assert acts[0].actionGroup() is not None and acts[0].actionGroup().isExclusive()
    assert [a.isChecked() for a in acts] == [True, False, False]
    with caplog.at_level(logging.INFO):
        acts[2].trigger()                           # Off
    assert settings.animations == "off" and Settings(tmp_path / "s.json").animations == "off"
    assert not motion.animations_enabled() and changed == [1]
    assert [a.isChecked() for a in acts] == [False, False, True]
    assert "animations: off (user setting" in caplog.text
    acts[1].trigger()                               # Follow Windows (off here)
    assert not motion.animations_enabled()
    acts[0].trigger()                               # On, despite Windows
    assert motion.animations_enabled() and motion.micro_enabled()
    tray.contextMenu().aboutToShow.emit()
    assert [a.isChecked() for a in menu.actions()] == [True, False, False]


# -- with "on" and Windows effects off, every animation really runs ----------------------------
def test_every_animation_runs_with_on_despite_windows_effects_off(qtbot, tmp_path,
                                                                 windows_effects_off):
    motion.set_user_preference("on")
    assert motion.duration(motion.POPUP_MS) > 0 and motion.micro_duration(motion.FAST) > 0
    started = {}
    short, long = ev("s", "Short", 17), ev("l", "Long", 18, description="x\n" * 40)
    p = popup_on(qtbot, tmp_path, MANY[:3] + [short, long], tray=BOTTOM_RIGHT)
    started["popup slide-up + fade"] = p.showing_animated() and \
        p._show_slide.duration() > 0 and p._show_fade.duration() > 0
    qtbot.waitUntil(lambda: not p.showing_animated())
    click(qtbot, rows(p)[3])
    fly = p.flyout()
    started["panel slide (masked)"] = fly.sliding() and fly._anim.duration() > 0 \
        and fly.mode() == "slide"
    qtbot.waitUntil(lambda: not fly.sliding())
    click(qtbot, rows(p)[4])
    started["flyout cross-fade"] = fly.crossfading()
    started["flyout height tween"] = fly.height_animating()
    qtbot.waitUntil(lambda: not fly.crossfading() and not fly.height_animating())
    row = rows(p)[1]
    _enter(row)
    started["row hover fade"] = row.fading()
    qtbot.waitUntil(lambda: not row.fading())
    p.cache.upsert_events("a", [MANY[3]])
    p.refresh()
    started["popup height tween"] = p.height_animating() and \
        p.height_animation().duration() > 0
    qtbot.waitUntil(lambda: not p.animations_running())
    right_click(rows(p)[0])
    _menu_action(p, meeting_menus.HIDE_ONE).trigger()
    started["row collapse"] = p.collapsing()
    qtbot.waitUntil(lambda: p.revealing(p._notice_box) or not p.animations_running())
    started["notice reveal"] = p.revealing(p._notice_box)
    qtbot.waitUntil(lambda: not p.animations_running())
    p.attach_chat(lambda: None)
    p.toggle_chat()
    started["chat expand"] = p.revealing(p.chat()) and p.animations_running()
    qtbot.waitUntil(lambda: not p.animations_running())
    assert started == {k: True for k in started}, started
    assert len(started) == 9
