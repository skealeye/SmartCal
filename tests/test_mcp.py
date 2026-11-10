import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from smartcal import assistant, inbox, mcp_server
from smartcal.reminders import Reminders

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTCAL_DATA_DIR", str(tmp_path))
    return tmp_path


def call(name, **args):
    reply = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                               "params": {"name": name, "arguments": args}})
    return reply["result"]


def tomorrow_10():
    return (datetime.now().astimezone() + timedelta(days=1)).replace(
        hour=10, minute=0, second=0, microsecond=0)


def test_initialize_list_and_unknown(data):
    r = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                           "params": {"protocolVersion": "2025-06-18"}})
    assert r["result"]["protocolVersion"] == "2025-06-18"
    assert r["result"]["serverInfo"]["name"] == "smartcal"
    assert mcp_server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    names = [t["name"] for t in mcp_server.handle(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]]
    assert "smartcal_add_reminder" in names and "smartcal_agenda" in names
    assert mcp_server.handle({"jsonrpc": "2.0", "id": 3, "method": "nope"})["error"]["code"] == -32601


def test_add_reminder_goes_through_the_inbox_and_the_app_applies_it(data):
    when = tomorrow_10()
    res = call("smartcal_add_reminder", text="check in on the Acme deal", when=when.isoformat())
    assert not res.get("isError") and "check in on the Acme deal" in res["content"][0]["text"]
    assert len(list(inbox.inbox_dir().glob("*.json"))) == 1
    reminders = Reminders(data / "reminders.json")
    assert inbox.process(reminders) == ["reminder #%d added" % reminders.list()[0].id]
    (r,) = reminders.list()
    assert r.text == "check in on the Acme deal" and r.fire_at == when
    assert list(inbox.inbox_dir().glob("*.json")) == []           # consumed
    assert "Acme" in call("smartcal_list_reminders")["content"][0]["text"]


def test_naive_time_means_local_and_past_or_garbage_is_refused(data):
    naive = tomorrow_10().replace(tzinfo=None).isoformat()
    assert not call("smartcal_add_reminder", text="x", when=naive).get("isError")
    past = (datetime.now().astimezone() - timedelta(hours=2)).isoformat()
    assert call("smartcal_add_reminder", text="x", when=past)["isError"]
    assert call("smartcal_add_reminder", text="x", when="tomorrow 10am")["isError"]
    assert call("smartcal_add_reminder", text="  ", when=tomorrow_10().isoformat())["isError"]


def test_cancel_and_bad_inbox_files(data):
    reminders = Reminders(data / "reminders.json")
    rid = reminders.add("old", tomorrow_10())
    call("smartcal_cancel_reminder", id=rid)
    (inbox.inbox_dir() / "junk.json").write_text("not json", encoding="utf-8")
    (inbox.inbox_dir() / "odd.json").write_text(json.dumps({"type": "format_c"}), encoding="utf-8")
    inbox.process(reminders)
    assert reminders.list() == []
    assert sorted(p.name for p in inbox.inbox_dir().iterdir()) == ["junk.bad", "odd.bad"]


def test_agenda_with_no_data_is_empty(data):
    assert call("smartcal_agenda")["content"][0]["text"] == "No meetings."
    assert call("smartcal_agenda", start_date="garbage")["isError"]


def test_real_stdio_round_trip(data):
    env = dict(os.environ, SMARTCAL_DATA_DIR=str(data))
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "smartcal_now", "arguments": {}}}]
    out = subprocess.run([sys.executable, "-I", str(ROOT / "smartcal_mcp.py")], cwd=ROOT, env=env,
                         input="\n".join(json.dumps(m) for m in msgs) + "\n",
                         capture_output=True, text=True, timeout=30)
    lines = [json.loads(x) for x in out.stdout.splitlines()]
    assert [m["id"] for m in lines] == [1, 2]            # nothing but protocol on stdout
    assert str(datetime.now().year) in lines[1]["result"]["content"][0]["text"]


def test_api_key_save_keeps_other_lines_and_resets_clients(data, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    (data / ".env").write_text("OTHER=1\nANTHROPIC_API_KEY=old-old-old-old-old-old\n", encoding="utf-8")
    client = assistant.make_client()
    client._client = object()
    assert assistant.api_key_source() == "saved"
    assistant.save_api_key("sk-ant-0123456789abcdef0123")
    text = (data / ".env").read_text(encoding="utf-8")
    assert "OTHER=1" in text and text.count("ANTHROPIC_API_KEY") == 1
    assert assistant.load_api_key() == "sk-ant-0123456789abcdef0123"
    assert client._client is None                          # the next chat uses the new key
    with pytest.raises(ValueError):
        assistant.save_api_key("short")
    assistant.save_api_key("")
    assert assistant.api_key_source() == "none"
    assert "OTHER=1" in (data / ".env").read_text(encoding="utf-8")


def test_settings_dialog_key_and_command_rows(qtbot, tmp_path, monkeypatch):
    from tests.test_settings_dialog import build
    monkeypatch.setenv("SMARTCAL_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    dlg, *_ = build(qtbot, tmp_path)
    assert "No key yet" in dlg.key_status.text()
    dlg.key_box.setText("sk-ant-0123456789abcdef0123")
    dlg.key_save.click()
    assert "saved" in dlg.key_status.text() and dlg.key_box.text() == ""
    cmd = dlg.mcp_command()
    assert cmd.startswith("claude mcp add smartcal -- ") and "smartcal_mcp.py" in cmd
