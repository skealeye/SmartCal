import json
import logging
import threading
from datetime import datetime, timedelta, timezone

import pytest

from smartcal.reminders import ReminderFirer, Reminders
from smartcal.toast import ToastSender
from smartcal.ui.reminders_dialog import RemindersDialog

TZ = timezone(timedelta(hours=-5))
T0 = datetime(2026, 11, 4, 9, 0, tzinfo=TZ)


def make(tmp_path):
    return Reminders(tmp_path / "r.json", clock=lambda: T0)


def test_reminder_survives_restart(tmp_path):
    r = make(tmp_path)
    rid = r.add("Call Sam", T0 + timedelta(hours=1))
    r2 = make(tmp_path)
    got = r2.list()
    assert [(x.id, x.text, x.fire_at) for x in got] == [(rid, "Call Sam", T0 + timedelta(hours=1))]


def test_due_returns_only_past_unfired(tmp_path):
    r = make(tmp_path)
    a = r.add("past", T0 - timedelta(minutes=1))
    r.add("future", T0 + timedelta(minutes=1))
    b = r.add("done", T0 - timedelta(minutes=2))
    r.mark_fired(b)
    assert [x.id for x in r.due(T0)] == [a]


def test_missed_reminder_fires_once_after_wake(tmp_path):
    r = make(tmp_path)
    r.add("Call Sam", T0)
    sent = []
    f = ReminderFirer(r, lambda *a: sent.append(a))
    wake = T0 + timedelta(hours=8)
    f.check(wake)
    f.check(wake + timedelta(seconds=30))
    f2 = ReminderFirer(make(tmp_path), lambda *a: sent.append(a))  # restart
    f2.check(wake + timedelta(minutes=1))
    assert len(sent) == 1


def test_fire_marks_fired_and_does_not_repeat(tmp_path):
    r = make(tmp_path)
    r.add("x", T0)
    sent = []
    f = ReminderFirer(r, lambda *a: sent.append(a))
    f.check(T0)
    f.check(T0 + timedelta(seconds=30))
    assert sent == [("Reminder", "x", None)]
    assert r.list() == [] and r.due(T0) == []


def test_late_reminder_body_shows_original_due_time(tmp_path):
    r = make(tmp_path)
    r.add("Call Sam", T0)
    r.add("on time", T0)
    sent = []
    ReminderFirer(r, lambda *a: sent.append(a)).check(T0 + timedelta(minutes=6))
    bodies = sorted(s[1] for s in sent)
    assert any(b == f"(was due {T0.astimezone():%H:%M}) Call Sam" for b in bodies)
    sent.clear()
    r.add("fresh", T0 + timedelta(minutes=10))
    ReminderFirer(r, lambda *a: sent.append(a)).check(T0 + timedelta(minutes=14))
    assert sent == [("Reminder", "fresh", None)]


def test_corrupt_reminders_json_loads_empty(tmp_path, caplog):
    p = tmp_path / "r.json"
    for bad in ("{not json", "[]", '{"next_id": "x", "items": []}',
                '{"next_id": 3, "items": [{"id": "1"}]}', '{"next_id": 1, "items": 5}'):
        p.write_text(bad, encoding="utf-8")
        with caplog.at_level(logging.WARNING):
            assert Reminders(p).list() == []
    assert "starting empty" in caplog.text


def test_add_rejects_empty_text(tmp_path):
    r = make(tmp_path)
    for t in ("", "   ", None):
        with pytest.raises(ValueError):
            r.add(t, T0)


def test_text_capped_200_chars(tmp_path):
    r = make(tmp_path)
    r.add("a" * 500, T0)
    assert len(r.list()[0].text) == 200


def test_ids_not_reused_after_restart(tmp_path):
    r = make(tmp_path)
    a = r.add("a", T0)
    b = r.add("b", T0)
    r.cancel(b)
    c = make(tmp_path).add("c", T0)
    assert a < b < c


def test_cancel_removes_reminder(tmp_path):
    r = make(tmp_path)
    a = r.add("a", T0)
    r.cancel(a)
    r.cancel(a)  # unknown id: no error
    assert r.list() == [] and make(tmp_path).list() == []


def test_concurrent_add_and_due_is_safe(tmp_path):
    r = make(tmp_path)
    errors = []

    def adder():
        try:
            for i in range(40):
                r.add(f"t{i}", T0)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    def checker():
        try:
            for _ in range(40):
                for x in r.due(T0 + timedelta(hours=1)):
                    r.mark_fired(x.id)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    ts = [threading.Thread(target=adder), threading.Thread(target=checker)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errors
    data = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert len(data["items"]) == 40 and len({d["id"] for d in data["items"]}) == 40


def test_fired_reminders_pruned_after_two_days(tmp_path):
    r = make(tmp_path)
    a = r.add("old", T0)
    b = r.add("recent", T0 + timedelta(days=1))
    r.mark_fired(a)
    r.mark_fired(b)
    r.due(T0 + timedelta(days=2, minutes=1))
    ids = {d["id"] for d in json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))["items"]}
    assert ids == {b}


def test_send_toast_failure_does_not_refire(tmp_path):
    r = make(tmp_path)
    r.add("x", T0)
    calls = []

    def boom(*a):
        calls.append(a)
        raise OSError("no toast")

    f = ReminderFirer(r, boom)
    f.check(T0)
    f.check(T0 + timedelta(seconds=30))
    assert len(calls) == 1 and r.list() == []


class FakeNote:
    made = []

    def __init__(self, app_id, title, msg=""):
        self.title, self.msg = title, msg
        FakeNote.made.append(self)

    def add_actions(self, label, launch=""):
        pass

    def show(self):
        pass


def test_reminder_text_with_markup_goes_through_sanitizer(tmp_path):
    FakeNote.made = []
    r = make(tmp_path)
    r.add("<b>$(Get-Process)</b> 'x'\nline2", T0)
    ReminderFirer(r, ToastSender(notification_cls=FakeNote)).check(T0)
    msg = FakeNote.made[0].msg
    assert FakeNote.made[0].title == "Reminder"
    assert not any(c in msg for c in "<>$'\n")


def test_dialog_plain_text_and_cancel(qtbot, tmp_path):
    r = make(tmp_path)
    r.add("<b>bold</b> & <i>x</i>", T0 + timedelta(hours=1))
    keep = r.add("second", T0 + timedelta(hours=2))
    dlg = RemindersDialog(r)
    qtbot.addWidget(dlg)
    rows = dlg.rows()
    assert len(rows) == 2
    assert "<b>bold</b> & <i>x</i>" in rows[0].label.text()
    assert rows[0].label.textFormat().name == "PlainText"
    rows[0].button.click()
    assert [x.id for x in r.list()] == [keep]
    assert len(dlg.rows()) == 1
