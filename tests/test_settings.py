import json

from smartcal.settings import Settings


def test_settings_save_is_atomic_roundtrip(tmp_path):
    p = tmp_path / "s.json"
    s = Settings(p)
    s.set_hidden("a", True)
    s.set_hidden("b", False)
    assert [f.name for f in tmp_path.iterdir()] == ["s.json"]  # no temp left behind
    s2 = Settings(p)
    assert s2.hidden_calendars == {"a"} and s2.shown_calendars == {"b"}


def test_settings_wrong_types_ignored(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"hidden_calendars": "abc", "shown_calendars": ["x", 3, None]}))
    s = Settings(p)
    assert s.hidden_calendars == set() and s.shown_calendars == {"x"}
    p.write_text("[1,2]")
    assert Settings(p).hidden_calendars == set()
