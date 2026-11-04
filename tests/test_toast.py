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
