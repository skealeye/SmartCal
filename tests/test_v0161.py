import io
import json
from datetime import datetime, timedelta

from smartcal import mcp_server
from smartcal.models import Event
from smartcal.ui import window


def test_mcp_ignores_a_leading_bom(monkeypatch, capsys):
    msg = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"})
    monkeypatch.setattr("sys.stdin", io.StringIO(chr(0xFEFF) + msg + chr(10)))
    mcp_server.main()
    assert json.loads(capsys.readouterr().out)["id"] == 1


def _chip(qtbot, start, end, all_day=False):
    ev = Event(id="1", account="a", calendar_id="c", calendar_name="C", title="T",
               start=start, end=end, all_day=all_day,
               color="#4285f4")
    chip = window.EventChip(ev, "T")
    qtbot.addWidget(chip)
    return chip


def test_window_chip_knows_when_its_meeting_has_ended(qtbot):
    now = datetime.now().astimezone()
    assert _chip(qtbot, now - timedelta(hours=2), now - timedelta(hours=1)).is_past()
    assert not _chip(qtbot, now - timedelta(hours=1), now + timedelta(hours=1)).is_past()
    assert not _chip(qtbot, now - timedelta(days=1), now - timedelta(hours=1), all_day=True).is_past()
