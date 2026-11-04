import logging

from smartcal.toast import ToastSender, clean_text, join_url


class FakeNote:
    made = []
    fail = False

    def __init__(self, app_id, title, msg=""):
        self.app_id, self.title, self.msg, self.actions, self.shown = app_id, title, msg, [], False
        self.tag = title
        FakeNote.made.append(self)

    def add_actions(self, label, launch=""):
        self.actions.append((label, launch))

    def show(self):
        if FakeNote.fail:
            raise OSError("no powershell")
        self.shown = True


class FakeTray:
    def __init__(self):
        self.messages = []

    def showMessage(self, *a):
        self.messages.append(a)


def setup_function(_):
    FakeNote.made, FakeNote.fail = [], False


def test_toast_join_action_only_for_http_urls():
    send = ToastSender(notification_cls=FakeNote)
    for url in (None, "", "file:///C:/Windows/calc.exe", "javascript:alert(1)",
                "ms-calc:", "https://", "http://a b/c", "calc.exe"):
        send("T", "B", url)
    assert all(n.actions == [] and n.shown for n in FakeNote.made)
    send("T", "B", "https://meet.google.com/abc?x=1&y=2")
    send("T", "B", "HTTP://example.com/")
    assert FakeNote.made[-2].actions == [("Join", "https://meet.google.com/abc?x=1&amp;y=2")]
    assert FakeNote.made[-1].actions[0][0] == "Join"


def test_join_url_neutralizes_script_characters():
    out = join_url('https://x.com/a"$(calc)`b\'<c>')
    assert out and not any(ch in out for ch in '"$`\'<>')


def test_text_is_not_markup_or_script():
    assert "$" not in clean_text("$(calc)") and "`" not in clean_text("`x`")
    t = clean_text('a]]>b"\n@"\n<b>x</b> & y')
    assert "]]>" not in t and "\n" not in t and '"' not in t and "<" not in t and "&" not in t
    send = ToastSender(notification_cls=FakeNote)
    send("<b>$(evil)</b>", 'x\n"@\ny', None)
    n = FakeNote.made[0]
    assert "$" not in n.title and "\n" not in n.msg and n.tag.isalnum()


def test_falls_back_to_tray_and_logs_warning(caplog):
    FakeNote.fail = True
    tray = FakeTray()
    with caplog.at_level(logging.WARNING):
        ToastSender(tray=tray, notification_cls=FakeNote)("Title", "Body", None)
    assert tray.messages and tray.messages[0][:2] == ("Title", "Body")
    assert "native toast failed" in caplog.text


def test_missing_winotify_falls_back(monkeypatch):
    import builtins
    real = builtins.__import__

    def fake(name, *a, **k):
        if name == "winotify":
            raise ImportError(name)
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake)
    tray = FakeTray()
    ToastSender(tray=tray)("T", "B", None)
    assert len(tray.messages) == 1


HOSTILE = ["$(calc)", "${a}", "$env:X", "a`b", 'x\n"@\ny', "x\n'@\ny", "“”‘’‚„",
           "a;b", "%PATH%", "]]>", "line1\r\nline2\nline3", "bad￿char", "x" * 5000,
           "”@   “@", "lone\ud800surrogate", "«q» ＂ ＇"]


def _script(monkeypatch, title, body, url=None):
    import winotify
    out = []
    monkeypatch.setattr(winotify, "_run_ps", lambda command="", file="": out.append(command))
    ToastSender()(title, body, url)
    return out[0]


def _strip_payload(script):
    import re
    texts = re.findall(r"<text id=\"\d\"><!\[CDATA\[(.*?)\]\]></text>", script, flags=re.S)
    assert len(texts) == 2
    for t in texts:
        yield t
    return


def test_hostile_text_cannot_alter_the_real_winotify_script(monkeypatch):
    import re
    benign = _script(monkeypatch, "BENIGN", "BENIGN")
    norm = lambda s: re.sub(r'\$Toast\.Tag = "[0-9a-f]*"', "TAG", s)  # noqa: E731
    for h in HOSTILE:
        script = _script(monkeypatch, h, h)
        payloads = list(_strip_payload(script))
        for p in payloads:
            assert not any(c in p for c in '$`"\'<>&\n\r  '), p
            assert "]]>" not in p and "￿" not in p
            assert not any(0xD800 <= ord(c) <= 0xDFFF for c in p)
        assert len(payloads[0]) <= 120 and len(payloads[1]) <= 300
        rebuilt = script
        for p in payloads:
            rebuilt = rebuilt.replace(p, "BENIGN", 1)
        assert norm(rebuilt) == norm(benign), h
        assert re.fullmatch(r'[0-9a-f]+', re.search(r'\$Toast\.Tag = "(.*?)"', script).group(1))


def test_xml_invalid_characters_removed():
    t = clean_text("a\x00b\x0bc\x1fd￾e￿ f\ud800g\udfffh i j")
    for bad in ("\x00", "\x0b", "\x1f", "￾", "￿", "\ud800", "\udfff", " ", " "):
        assert bad not in t
    assert clean_text("café \U0001F600 ok") == "café \U0001F600 ok"
    assert clean_text("x" * 500, 120) == "x" * 120


def test_register_app_id_roundtrip_on_temp_key():
    import uuid
    import winreg
    base = r"Software\SmartCalTest"
    key = base + "\\" + uuid.uuid4().hex
    from smartcal.toast import register_app_id
    try:
        register_app_id(key)
        register_app_id(key)  # idempotent
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            assert winreg.QueryValueEx(k, "DisplayName")[0] == "SmartCal"
    finally:
        for path in (key, base):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
            except OSError:
                pass
