"""In-memory stand-in for GoogleClient, shared by action/undo tests."""
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace

from googleapiclient.errors import HttpError

from smartcal.models import CalendarInfo, Event


class FakeClient:
    def __init__(self, account, events=()):
        self.account = account
        self.store = {(e.calendar_id, e.id): e for e in events}
        self.n = 0
        self.stamp = 0
        self.fail_next = None
        self.log = []

    def _bump(self, ev):
        self.stamp += 1
        return replace(ev, updated=f"u{self.stamp}")

    def _check(self):
        if self.fail_next:
            exc, self.fail_next = self.fail_next, None
            raise exc

    def get_event(self, event_id, *, calendar_id):
        try:
            return replace(self.store[(calendar_id, event_id)])
        except KeyError:
            raise HttpError(SimpleNamespace(status=404, reason="nf"), b"")

    def create_event(self, calendar_id, title, start, end, all_day=False, location="",
                     description=""):
        self._check()
        self.n += 1
        ev = self._bump(Event(f"new{self.n}", self.account, calendar_id, "Cal", "#000", title,
                              start, end, all_day, location, description))
        self.store[(calendar_id, ev.id)] = ev
        self.log.append(("create", ev.id))
        return replace(ev)

    def update_event(self, event_id, *, calendar_id, **f):
        self._check()
        if ("start" in f or "end" in f) and "all_day" not in f:
            raise ValueError("all_day is required")
        ev = self.store[(calendar_id, event_id)]
        ev = self._bump(replace(ev, **f))
        self.store[(calendar_id, event_id)] = ev
        self.log.append(("update", event_id, sorted(f)))
        return replace(ev)

    def delete_event(self, event_id, *, calendar_id):
        self._check()
        del self.store[(calendar_id, event_id)]
        self.log.append(("delete", event_id))

    def restore_event(self, before):
        ev = self._bump(before)
        self.store[(before.calendar_id, before.id)] = ev
        self.log.append(("restore", before.id))
        return replace(ev)

    def external_edit(self, calendar_id, event_id, **f):
        """Simulate a change made in Google's own UI."""
        self.store[(calendar_id, event_id)] = self._bump(
            replace(self.store[(calendar_id, event_id)], **f))


def ev(account="a@x.com", id="e1", title="Dentist", start=None, end=None, cal="a@x.com",
       all_day=False, **kw):
    start = start or datetime(2026, 11, 5, 18, 0).astimezone()
    end = end or datetime(2026, 11, 5, 19, 0).astimezone()
    return Event(id, account, cal, "Cal " + cal, "#123", title, start, end, all_day, **kw)


def cal(id, primary=True, name=None):
    return CalendarInfo(id, name or id, "#123", primary)


def http_err(status):
    return HttpError(SimpleNamespace(status=status, reason="x"), b"")
