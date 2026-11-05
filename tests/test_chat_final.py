"""I5: an undo blocked by an edit made elsewhere can be forced with 'Undo anyway'."""
from test_chat import FakeAssistant, all_text, buttons, make, say

from smartcal.assistant import AssistantResult
from smartcal.ui import chat as chat_mod

WARN = ("Not undone: Dentist was changed elsewhere since SmartCal edited it. "
        "Use 'Undo anyway' to overwrite it.")


class ForceAssistant(FakeAssistant):
    def __init__(self, script=None):
        super().__init__(script)
        self.forced = 0

    def undo_last(self, force=False):
        if force:
            self.forced += 1
            self.store.pop()
            return "Undone: moved Dentist back to Thu 6:00 PM"
        self.undos += 1
        return WARN


def _warned(qtbot):
    r = AssistantResult("Moved.", True, changes=["Done: moved Dentist to Fri Nov 6 6:00 PM"])
    w, a = make(qtbot, ForceAssistant([r]))
    say(qtbot, w)
    buttons(w, "Undo")[0].click()
    qtbot.waitUntil(lambda: not w.is_busy(), timeout=3000)
    return w, a


def test_changed_elsewhere_warning_offers_undo_anyway(qtbot):
    w, a = _warned(qtbot)
    assert WARN in all_text(w)
    (anyway,) = buttons(w, "Undo anyway")
    assert anyway.isEnabled()
    asked = []
    w.confirm_overwrite = lambda: asked.append(1) or True
    anyway.click()
    qtbot.waitUntil(lambda: not w.is_busy(), timeout=3000)
    assert asked == [1] and a.forced == 1
    assert "Undone: moved Dentist back" in all_text(w)
    assert not anyway.isEnabled()          # once only
    anyway.click()
    assert a.forced == 1


def test_undo_anyway_cancelled_in_confirm_does_nothing(qtbot):
    w, a = _warned(qtbot)
    (anyway,) = buttons(w, "Undo anyway")
    w.confirm_overwrite = lambda: False
    anyway.click()
    assert a.forced == 0 and anyway.isEnabled()


def test_undo_anyway_frozen_after_a_new_message(qtbot):
    w, a = _warned(qtbot)
    (anyway,) = buttons(w, "Undo anyway")
    say(qtbot, w, "something else")
    assert not anyway.isEnabled()


def test_other_undo_messages_offer_no_force():
    assert chat_mod.offers_force(WARN)
    assert not chat_mod.offers_force("Can't undo: the event no longer exists.")
    assert not chat_mod.offers_force("Undone: removed Lunch")


def test_force_confirm_text():
    assert chat_mod.FORCE_CONFIRM == "This will overwrite the changes made in Google."


def test_assistant_undo_last_passes_force(tmp_path):
    from test_assistant import FakeClaude, make as amake

    seen = []

    class Store:
        def undo_last(self, actions, force=False):
            seen.append(force)
            return "x"

        def __len__(self):
            return 0

    a, *_ = amake(tmp_path, FakeClaude())
    a.undo = Store()
    a.undo_last()
    a.undo_last(force=True)
    assert seen == [False, True]


def test_store_warning_points_at_undo_anyway(tmp_path):
    from fakes import FakeClient, ev
    from smartcal.actions import Actions
    from smartcal.db import Cache
    from smartcal.undo import UndoStore
    from datetime import timedelta
    e = ev()
    client = FakeClient("a@x.com", [e])
    cache = Cache(tmp_path / "c.db")
    cache.upsert_events("a@x.com", [e])
    store = UndoStore(tmp_path / "u.json")
    act = Actions(lambda a: client, cache, store)
    act.move("e1", e.start + timedelta(days=1), e.end + timedelta(days=1))
    client.external_edit("a@x.com", "e1", title="RSVP changed")
    line = store.undo_last(act)
    assert line.endswith("Use 'Undo anyway' to overwrite it.") and len(store) == 1
    assert store.undo_last(act, force=True).startswith("Undone: moved")
