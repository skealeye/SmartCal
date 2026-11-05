import logging
import threading
import time

from datetime import datetime, timedelta

from PyQt6.QtCore import QEvent, QRect, Qt
from PyQt6.QtWidgets import QApplication, QLabel, QPushButton

from smartcal.assistant import (PENDING_HEADER, PENDING_PREFIX, Assistant, AssistantError,
                                AssistantResult, make_client)
from smartcal.db import Cache
from smartcal.google_auth import ReauthRequired
from smartcal.models import CalendarInfo, Event
from smartcal.settings import Settings
from smartcal.ui import chat as chat_mod
from smartcal.ui.chat import ChatWidget
from smartcal.ui.popup import AgendaPopup
from smartcal.ui.tray import TrayController


class FakeAssistant:
    def __init__(self, script=None):
        self.script = list(script or [])
        self.calls = []
        self.undos = 0
        self.resets = 0
        self.reset_ok = True
        self.undo_line = "Undone: removed Lunch"

    def reply(self, text):
        self.calls.append(text)
        item = self.script.pop(0) if self.script else AssistantResult("ok", False)
        if callable(item):
            item = item()
        if isinstance(item, BaseException):
            raise item
        return item

    def undo_last(self):
        self.undos += 1
        return self.undo_line

    def reset(self):
        self.resets += 1
        return self.reset_ok


def make(qtbot, assistant=None, factory=None):
    a = assistant or FakeAssistant()
    w = ChatWidget(factory or (lambda: a))
    qtbot.addWidget(w)
    w.show()
    return w, a


def say(qtbot, w, text="hi"):
    w.send(text)
    qtbot.waitUntil(lambda: not w.is_busy(), timeout=3000)


def all_text(w):
    return "\n".join(w.texts())


def buttons(w, label):
    return [b for b in w.findChildren(QPushButton) if b.text() == label]


def test_missing_api_key_shows_message_and_tray_keeps_working(qtbot, tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("smartcal.assistant.data_dir", lambda: tmp_path)
    cache = Cache(tmp_path / "c.db")
    settings = Settings(tmp_path / "s.json")
    real = Assistant(make_client(), actions=None, reminders=None, settings=settings, cache=cache)
    real.system_prompt = lambda now: "x"
    w, _ = make(qtbot, factory=lambda: real)
    say(qtbot, w, "hello")
    text = all_text(w)
    assert "No Claude API key found" in text and ".env" in text and "Claude:" in text
    assert w.input_enabled()
    popup = AgendaPopup(cache, settings)
    qtbot.addWidget(popup)

    class W:
        def sync_now(self):
            pass

        def stop(self):
            pass
    tray = TrayController(popup, W(), cache, settings)
    popup.refresh()
    popup.show_near_tray(QRect(700, 570, 20, 20))
    assert popup.isVisible()
    tray.toggle_popup()
    assert not popup.isVisible()


def test_api_error_shows_message_not_crash(qtbot):
    w, a = make(qtbot, FakeAssistant([AssistantError("Claude is busy right now. Try again in a minute.")]))
    say(qtbot, w)
    assert "Claude: Claude is busy right now." in all_text(w)
    assert w.items()[-1].kind == "error"
    assert w.input_enabled()


def test_api_timeout_shows_message(qtbot):
    w, a = make(qtbot, FakeAssistant([AssistantError("Claude took too long to answer. Try again.")]))
    say(qtbot, w)
    assert "took too long" in all_text(w)
    assert w.input_enabled()


def test_undo_button_calls_undo_last(qtbot):
    r = AssistantResult("Added.", True, changes=["Done: added Lunch on Thu Nov 5 12:00 PM"])
    w, a = make(qtbot, FakeAssistant([r]))
    say(qtbot, w)
    assert "Done: added Lunch" in all_text(w)
    (undo,) = buttons(w, "Undo")
    assert undo.isEnabled()
    undo.click()
    qtbot.waitUntil(lambda: not w.is_busy(), timeout=3000)
    assert a.undos == 1
    assert "Undone: removed Lunch" in all_text(w)
    assert not undo.isEnabled()


def test_chat_does_not_block_ui_thread(qtbot):
    gate = threading.Event()
    w, a = make(qtbot, FakeAssistant([lambda: (gate.wait(5), AssistantResult("late", False))[1]]))
    t0 = time.perf_counter()
    w.send("slow")
    assert time.perf_counter() - t0 < 0.5
    assert w.is_busy() and not w.input_enabled()
    assert chat_mod.THINKING in all_text(w)
    QApplication.processEvents()
    gate.set()
    qtbot.waitUntil(lambda: not w.is_busy(), timeout=3000)
    assert "late" in all_text(w) and chat_mod.THINKING not in all_text(w)


def test_pending_block_has_approve_deny_buttons_that_send_yes_no(qtbot):
    line = PENDING_PREFIX + "delete the event titled “Dentist” (Thu Nov 5 6:00 PM)"
    r1 = AssistantResult("Delete it?\n\n" + PENDING_HEADER + "\n" + line, False, question=True,
                         pending=[line])
    w, a = make(qtbot, FakeAssistant([r1, AssistantResult("Deleted.", True,
                                                         changes=["Done: deleted Dentist"]),
                                      r1, AssistantResult("Left it.", False)]))
    say(qtbot, w, "delete dentist")
    (ok,), (no,) = buttons(w, "Approve"), buttons(w, "Deny")
    assert ok.isEnabled() and no.isEnabled()
    ok.click()
    qtbot.waitUntil(lambda: not w.is_busy(), timeout=3000)
    assert a.calls == ["delete dentist", "yes"]
    assert not ok.isEnabled() and not no.isEnabled()
    say(qtbot, w, "again")
    no2 = buttons(w, "Deny")[-1]
    no2.click()
    qtbot.waitUntil(lambda: not w.is_busy(), timeout=3000)
    assert a.calls[-1] == "no"


def test_pending_buttons_disable_when_newer_message_exists(qtbot):
    line = PENDING_PREFIX + "delete x"
    r = AssistantResult("Sure?\n\n" + PENDING_HEADER + "\n" + line, False, pending=[line])
    w, a = make(qtbot, FakeAssistant([r]))
    say(qtbot, w)
    (ok,) = buttons(w, "Approve")
    say(qtbot, w, "actually something else")
    assert not ok.isEnabled()


def test_pending_text_not_shown_twice(qtbot):
    line = PENDING_PREFIX + "delete the event titled “Dentist” (Thu Nov 5 6:00 PM)"
    r = AssistantResult("Delete it?\n\n" + PENDING_HEADER + "\n" + line, False, question=True,
                        pending=[line])
    w, _ = make(qtbot, FakeAssistant([r]))
    say(qtbot, w)
    text = all_text(w)
    assert text.count("Waiting for your OK") == 1
    assert PENDING_HEADER not in text
    assert "Delete it?" in text


def test_html_in_reply_renders_as_plain_text(qtbot):
    evil = '<b>bold</b> <a href="http://x.test">link</a> <img src="x">'
    w, _ = make(qtbot, FakeAssistant([AssistantResult(evil, False)]))
    say(qtbot, w, "<i>me</i>")
    labels = w.findChildren(QLabel)
    assert labels
    for lab in labels:
        assert lab.textFormat() == Qt.TextFormat.PlainText
    assert evil in all_text(w) and "<i>me</i>" in all_text(w)


def test_only_latest_undoable_reply_has_enabled_undo(qtbot):
    def mk(n):
        return AssistantResult("ok", True, changes=[f"Done: added {n}"])
    w, _ = make(qtbot, FakeAssistant([mk("A"), mk("B")]))
    say(qtbot, w, "a")
    say(qtbot, w, "b")
    first, second = buttons(w, "Undo")
    assert not first.isEnabled() and second.isEnabled()


def test_input_disabled_while_pending_and_reenabled_after_error(qtbot):
    gate = threading.Event()

    def boom():
        gate.wait(5)
        raise AssistantError("nope")
    w, _ = make(qtbot, FakeAssistant([boom]))
    w.send("x")
    assert not w.input_enabled()
    gate.set()
    qtbot.waitUntil(lambda: not w.is_busy(), timeout=3000)
    assert w.input_enabled()
    assert "Claude: nope" in all_text(w)


def test_unexpected_worker_exception_reenables_input(qtbot, caplog):
    secret = "SECRET-TITLE-123"
    w, _ = make(qtbot, FakeAssistant([RuntimeError(secret)]))
    with caplog.at_level(logging.DEBUG):
        say(qtbot, w, "my private text")
    assert chat_mod.GENERIC_ERROR in all_text(w)
    assert w.input_enabled()
    assert secret not in all_text(w) and secret not in caplog.text
    assert "my private text" not in caplog.text
    assert "RuntimeError" in caplog.text


def test_reauth_required_message(qtbot):
    w, _ = make(qtbot, FakeAssistant([ReauthRequired("me@example.com"),
                                      ReauthRequired("bad token!")]))
    say(qtbot, w)
    assert ("Sign in again for me@example.com: right-click the tray icon > "
            "Add Google account…") in all_text(w)
    say(qtbot, w)
    assert "Sign in again: right-click" in all_text(w)


def test_new_chat_resets_assistant(qtbot):
    w, a = make(qtbot)
    say(qtbot, w)
    assert w.items()
    w._new_btn.click()
    assert a.resets == 1 and w.items() == []
    say(qtbot, w)
    a.reset_ok = False
    w._new_btn.click()
    assert chat_mod.STILL_WORKING in all_text(w)


def test_changed_signal_emitted_after_write_and_undo(qtbot):
    r = AssistantResult("ok", True, changes=["Done: added X"])
    w, a = make(qtbot, FakeAssistant([r, AssistantResult("just talk", False)]))
    got = []
    w.changed.connect(lambda: got.append(1))
    say(qtbot, w, "add x")
    assert got == [1]
    say(qtbot, w, "chat")
    assert got == [1]
    buttons(w, "Undo")[0].click()
    qtbot.waitUntil(lambda: not w.is_busy(), timeout=3000)
    assert got == [1, 1]


def test_assistant_factory_error_shown_not_raised(qtbot):
    def bad():
        raise AssistantError("Bad config.")
    w, _ = make(qtbot, factory=bad)
    say(qtbot, w)
    assert "Claude: Bad config." in all_text(w) and w.input_enabled()

    def worse():
        raise ValueError("x")
    w2, _ = make(qtbot, factory=worse)
    say(qtbot, w2)
    assert chat_mod.GENERIC_ERROR in all_text(w2) and w2.input_enabled()


def test_history_limited_to_50_items(qtbot):
    w, _ = make(qtbot)
    for i in range(30):
        say(qtbot, w, f"m{i}")
    assert len(w.items()) == chat_mod.MAX_ITEMS


# -- popup hosting ---------------------------------------------------------------
def popup_with_chat(qtbot, tmp_path, assistant=None):
    cache = Cache(tmp_path / "c.db")
    settings = Settings(tmp_path / "s.json")
    popup = AgendaPopup(cache, settings)
    qtbot.addWidget(popup)
    a = assistant or FakeAssistant()
    popup.attach_chat(lambda: a)
    popup.refresh()
    return popup, a


def test_chat_collapsed_by_default_and_popup_still_hides_on_focus_loss(qtbot, tmp_path):
    popup, _ = popup_with_chat(qtbot, tmp_path)
    assert popup.chat() is None and not popup.chat_expanded()
    popup.show_near_tray(QRect(700, 570, 20, 20))
    qtbot.waitExposed(popup)
    popup._ask.click()
    assert popup.chat_expanded()
    assert popup.height() <= 480
    QApplication.sendEvent(popup, QEvent(QEvent.Type.WindowDeactivate))
    assert not popup.isVisible()
    popup.show_near_tray(QRect(700, 570, 20, 20))
    popup._ask.click()
    assert not popup.chat_expanded()


def test_popup_stays_visible_while_typing_and_pending(qtbot, tmp_path):
    gate = threading.Event()
    popup, a = popup_with_chat(
        qtbot, tmp_path,
        FakeAssistant([lambda: (gate.wait(5), AssistantResult("r", False))[1]]))
    popup.show_near_tray(QRect(700, 570, 20, 20))
    qtbot.waitExposed(popup)
    popup._ask.click()
    chat = popup.chat()
    qtbot.keyClicks(chat._input, "hello")
    QApplication.processEvents()
    assert popup.isVisible()
    qtbot.keyClick(chat._input, Qt.Key.Key_Return)
    assert chat.is_busy()
    QApplication.processEvents()
    assert popup.isVisible()
    gate.set()
    qtbot.waitUntil(lambda: not chat.is_busy(), timeout=3000)
    assert popup.isVisible() and a.calls == ["hello"]


# -- fix round 1 -------------------------------------------------------------------
def two_changes():
    return AssistantResult("ok", True, changes=["Done: added A", "Done: added B"])


def test_undo_button_undoes_every_write_of_the_reply(qtbot):
    w, a = make(qtbot, FakeAssistant([two_changes()]))
    say(qtbot, w)
    (undo,) = buttons(w, "Undo (2)")
    undo.click()
    qtbot.waitUntil(lambda: not w.is_busy(), timeout=3000)
    assert a.undos == 2
    assert not undo.isEnabled()


def test_undo_stops_at_first_warning_and_stays_enabled(qtbot):
    w, a = make(qtbot, FakeAssistant([two_changes()]))
    lines = iter(["Can't undo: the event no longer exists.", "Undone: x"])
    a.undo_last = lambda: (setattr(a, "undos", a.undos + 1), next(lines))[1]
    say(qtbot, w)
    (undo,) = buttons(w, "Undo (2)")
    undo.click()
    qtbot.waitUntil(lambda: not w.is_busy(), timeout=3000)
    assert a.undos == 1
    assert "Can't undo" in all_text(w)
    assert undo.isEnabled() and undo.text() == "Undo (2)"


def test_undo_partial_then_retry_shows_remaining(qtbot):
    w, a = make(qtbot, FakeAssistant([two_changes()]))
    lines = iter(["Undone: removed B", "Can't undo: nope", "Undone: removed A"])
    a.undo_last = lambda: next(lines)
    say(qtbot, w)
    (undo,) = buttons(w, "Undo (2)")
    undo.click()
    qtbot.waitUntil(lambda: not w.is_busy(), timeout=3000)
    assert undo.isEnabled() and undo.text() == "Undo"
    undo.click()
    qtbot.waitUntil(lambda: not w.is_busy(), timeout=3000)
    assert not undo.isEnabled()


def test_undo_retry_after_error_possible(qtbot):
    for exc in (ReauthRequired("me@example.com"), AssistantError("busy")):
        w, a = make(qtbot, FakeAssistant([AssistantResult("ok", True, changes=["Done: x"])]))

        def boom(exc=exc):
            raise exc
        a.undo_last = boom
        say(qtbot, w)
        (undo,) = buttons(w, "Undo")
        undo.click()
        qtbot.waitUntil(lambda: not w.is_busy(), timeout=3000)
        assert undo.isEnabled() and w.input_enabled()


def test_undo_freezes_approve_deny(qtbot):
    line = PENDING_PREFIX + "delete x"
    r1 = AssistantResult("ok", True, changes=["Done: added A"])
    r2 = AssistantResult("Sure?\n\n" + PENDING_HEADER + "\n" + line, False, pending=[line])
    w, a = make(qtbot, FakeAssistant([r1, r2]))
    say(qtbot, w, "a")
    say(qtbot, w, "b")
    (ok,) = buttons(w, "Approve")
    assert ok.isEnabled()
    buttons(w, "Undo")[0].click()
    qtbot.waitUntil(lambda: not w.is_busy(), timeout=3000)
    assert not ok.isEnabled()


def test_changed_not_emitted_when_nothing_undone(qtbot):
    w, a = make(qtbot, FakeAssistant([AssistantResult("ok", True, changes=["Done: x"])]))
    a.undo_line = "Nothing to undo."
    say(qtbot, w)
    got = []
    w.changed.connect(lambda: got.append(1))
    buttons(w, "Undo")[0].click()
    qtbot.waitUntil(lambda: not w.is_busy(), timeout=3000)
    assert got == []
    assert not buttons(w, "Undo")[0].isEnabled()


def test_input_max_length_and_empty_ignored(qtbot):
    w, a = make(qtbot)
    assert w._input.maxLength() == 2000
    w.send("   ")
    w._input.setText("  ")
    w._send_btn.click()
    assert a.calls == [] and not w.is_busy()


def test_handlers_run_on_main_thread(qtbot, monkeypatch):
    w, a = make(qtbot)
    seen = []
    orig = w._on_reply
    monkeypatch.setattr(w, "_on_reply",
                        lambda r: (seen.append(threading.current_thread()), orig(r)))
    say(qtbot, w)
    assert seen == [threading.main_thread()]


def _pending_result():
    line = PENDING_PREFIX + "delete x"
    return AssistantResult("Sure?\n\n" + PENDING_HEADER + "\n" + line, False, pending=[line])


def test_attention_emitted_for_pending_when_popup_hidden(qtbot):
    w, a = make(qtbot, FakeAssistant([_pending_result()]))
    w.hide()
    got = []
    w.attention.connect(got.append)
    say(qtbot, w)
    assert got == ["Claude is waiting for your OK"]


def test_no_attention_when_popup_visible(qtbot):
    w, a = make(qtbot, FakeAssistant([_pending_result(), AssistantResult("plain", False)]))
    got = []
    w.attention.connect(got.append)
    say(qtbot, w)
    w.hide()
    say(qtbot, w)       # hidden, but no pending block
    assert got == []


def test_tray_notify_attention_sets_tooltip_until_shown(qtbot, tmp_path):
    cache = Cache(tmp_path / "c.db")
    settings = Settings(tmp_path / "s.json")
    popup = AgendaPopup(cache, settings)
    qtbot.addWidget(popup)

    class W:
        def sync_now(self):
            pass
    tray = TrayController(popup, W(), cache, settings)
    tray.showMessage = lambda *a, **k: None
    tray.notify_attention("Claude is waiting for your OK")
    assert "waiting for your OK" in tray.toolTip()
    tray.toggle_popup()
    assert "waiting for your OK" not in tray.toolTip()


def test_escape_collapses_chat(qtbot, tmp_path):
    popup, _ = popup_with_chat(qtbot, tmp_path)
    popup.show_near_tray(QRect(700, 570, 20, 20))
    qtbot.waitExposed(popup)
    popup._ask.click()
    qtbot.keyClick(popup.chat()._input, Qt.Key.Key_Escape)
    assert not popup.chat_expanded() and popup.isVisible()
    qtbot.keyClick(popup, Qt.Key.Key_Escape)
    assert not popup.isVisible()


def test_popup_height_cap_and_input_visible_with_30_events(qtbot, tmp_path):
    cache = Cache(tmp_path / "c.db")
    cache.upsert_calendars("a", [CalendarInfo("primary", "Primary", "#4285f4", True)])
    today = datetime.now().astimezone().replace(hour=8, minute=0, second=0, microsecond=0)
    for i in range(30):
        st = today + timedelta(minutes=10 * i)
        cache.upsert_events("a", [Event(id=str(i), account="a", calendar_id="primary",
                                        calendar_name="Primary", color="#4285f4",
                                        title=f"Event {i}", start=st,
                                        end=st + timedelta(minutes=5))])
    popup = AgendaPopup(cache, Settings(tmp_path / "s.json"))
    qtbot.addWidget(popup)
    popup.attach_chat(lambda: FakeAssistant())
    popup.refresh(today)
    assert len(popup.rows()) == 30
    popup.show_near_tray(QRect(700, 570, 20, 20))
    qtbot.waitExposed(popup)
    popup._ask.click()
    QApplication.processEvents()
    assert popup.height() <= min(480, popup._area().height())
    inp = popup.chat()._input
    bottom = inp.mapTo(popup, inp.rect().bottomLeft()).y()
    assert 0 <= bottom < popup.height()
    assert bottom < popup._footer.geometry().top()
