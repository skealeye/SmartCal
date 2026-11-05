"""Final-review fixes in the assistant (synced range, shared calendars, declined, undo)."""
from test_assistant import (NOW, FakeClaude, at, ev, make, text_msg, tool_msg,  # noqa: F401
                            tool_results)

from smartcal import assistant as asst

OUTSIDE = "outside the synced range (2026-10-28..2027-01-03)"


# -- I2: synced range ----------------------------------------------------------------
def test_tools_refuse_dates_outside_the_synced_range(tmp_path):
    claude = FakeClaude(tool_msg(
        ("list_events", {"start": "2026-12-20", "end": "2027-01-10"}),
        ("list_events", {"start": "2026-10-20", "end": "2026-10-30"}),
        ("find_free_slots", {"date": "2027-01-15", "duration_minutes": 60}),
        ("find_free_slots", {"date": "2027-01-04", "duration_minutes": 60}),
        ("create_event", {"title": "Far", "start": "2027-01-10T10:00", "end": "2027-01-10T11:00"}),
        ("move_event", {"event_id": "e1", "new_start": "2027-02-01T10:00",
                        "new_end": "2027-02-01T11:00"}),
        ("update_event", {"event_id": "e1", "fields": {"start": "2027-02-01T10:00"}})),
        text_msg("ok"))
    a, clients, *_ = make(tmp_path, claude, [ev()])
    a.reply("far away")
    results = tool_results(claude.messages.calls[1])
    assert len(results) == 7
    assert all(r.get("is_error") and OUTSIDE in r["content"] for r in results)
    assert not clients["a@x.com"].log


def test_free_slots_inside_the_range_still_work(tmp_path):
    claude = FakeClaude(tool_msg(
        ("find_free_slots", {"date": "2027-01-03", "duration_minutes": 60}),
        ("find_free_slots", {"date": "2026-10-28", "duration_minutes": 60}),
        ("list_events", {"start": "2026-12-04", "end": "2027-01-04"})), text_msg("ok"))
    a, *_ = make(tmp_path, claude, [ev()])
    a.reply("free?")
    results = tool_results(claude.messages.calls[1])
    assert not any(r.get("is_error") for r in results)
    assert '"free":[[' in results[0]["content"]


def test_system_prompt_states_the_synced_range(tmp_path):
    a, *_ = make(tmp_path, FakeClaude(text_msg("hi")))
    prompt = a.system_prompt(NOW)
    assert "from 2026-10-28 to 2027-01-03" in prompt
    assert "never assume they are free" in prompt
