"""Cancellable Google sign-in (stubbed flow: no browser, no port is opened)."""
import logging
import threading

from smartcal import google_auth
from smartcal.google_auth import SignIn, SignInCancelled
from test_tray import build


class StubFlow:
    """run_local_server blocks until the local server gets a request (like oauthlib)."""

    def __init__(self):
        self.request = threading.Event()
        self.kwargs = None
        self.uri = None

    def run_local_server(self, **kw):
        self.kwargs = kw
        self.request.wait(5)
        if self.uri and "error=access_denied" in self.uri:
            raise RuntimeError("access_denied")   # what fetch_token raises
        return "creds"


def make_signin():
    flow = StubFlow()

    def get(url):
        flow.uri = url
        flow.request.set()
    return SignIn(flow_factory=lambda: flow, store=lambda creds: "new@x.com",
                  http_get=get, port_fn=lambda: 54321), flow


def test_signin_uses_own_port_and_120_s_timeout():
    s, flow = make_signin()
    flow.request.set()
    assert s.run() == "new@x.com"
    assert flow.kwargs == {"port": 54321, "open_browser": True, "timeout_seconds": 120}
    assert s.port is None


def test_cancel_makes_a_request_that_ends_the_flow_as_cancelled():
    s, flow = make_signin()
    out = []

    def run():
        try:
            s.run()
        except SignInCancelled:
            out.append("cancelled")
    t = threading.Thread(target=run)
    t.start()
    for _ in range(200):
        if s.port:
            break
        threading.Event().wait(0.01)
    assert s.cancel() is True
    t.join(5)
    assert out == ["cancelled"]
    assert flow.uri == "http://localhost:54321/?error=access_denied"
    assert s.port is None and s.cancel() is False


def test_free_port_is_a_port():
    assert 0 < google_auth.free_port() < 65536


# -- tray --------------------------------------------------------------------------
class Pending:
    """add_account_fn that blocks until released, raising SignInCancelled on cancel."""

    def __init__(self):
        self.go = threading.Event()
        self.cancelled = False
        self.calls = 0

    def add(self, label):
        self.calls += 1
        self.go.wait(5)
        if self.cancelled:
            raise SignInCancelled()
        return "a@x.com"

    def cancel(self):
        self.cancelled = True
        self.go.set()
        return True


def test_cancel_signin_resets_menu_and_flag(qtbot, tmp_path):
    p = Pending()
    tray, _ = build(qtbot, tmp_path, add_account_fn=p.add, cancel_signin_fn=p.cancel)
    tray.showMessage = lambda *a, **k: None
    tray.add_account()
    assert tray.signin_pending()
    assert tray._add_action.text() == "Cancel Google sign-in" and tray._add_action.isEnabled()
    tray._add_action.trigger()                 # the menu item now cancels
    qtbot.waitUntil(lambda: not tray.signin_pending(), timeout=3000)
    assert p.cancelled and p.calls == 1
    assert tray._add_action.text() == "Add Google account…" and tray._add_action.isEnabled()


def test_cancelled_signin_is_not_reported_as_error(qtbot, tmp_path, caplog):
    p = Pending()
    messages = []
    tray, _ = build(qtbot, tmp_path, add_account_fn=p.add, cancel_signin_fn=p.cancel)
    tray.showMessage = lambda *a, **k: messages.append(a)
    with caplog.at_level(logging.ERROR):
        tray.add_account()
        tray.cancel_signin()
        qtbot.waitUntil(lambda: not tray.signin_pending(), timeout=3000)
    assert [m[1] for m in messages] == ["Sign-in cancelled"]
    assert messages[0][2] == tray.MessageIcon.Information
    assert "add account failed" not in caplog.text


def test_signin_pending_longer_than_timeout_can_be_restarted(qtbot, tmp_path):
    p = Pending()
    now = [1000.0]
    tray, _ = build(qtbot, tmp_path, add_account_fn=p.add, cancel_signin_fn=p.cancel,
                    clock=lambda: now[0])
    tray.showMessage = lambda *a, **k: None
    tray.add_account()
    tray.add_account()
    qtbot.waitUntil(lambda: p.calls == 1)
    now[0] += 126                              # older than the OAuth timeout: dead
    assert not tray.signin_pending()
    tray._update_add_action()
    assert tray._add_action.text() == "Add Google account…"
    tray._add_action.trigger()                 # starts a new sign-in, not a cancel
    qtbot.waitUntil(lambda: p.calls == 2)
    assert tray.signin_pending() and not p.cancelled
    p.go.set()
    qtbot.waitUntil(lambda: not tray.signin_pending(), timeout=3000)


def test_settings_accounts_add_shows_cancel_while_pending(qtbot, tmp_path):
    from smartcal.db import Cache
    from smartcal.settings import Settings
    from smartcal.ui.settings_dialog import SettingsDialog
    state, cancels = [False], []

    def add():
        state[0] = True

    def cancel():
        cancels.append(1)
        state[0] = False

    dlg = SettingsDialog(Settings(tmp_path / "s.json"), Cache(tmp_path / "c.db"), lambda: [],
                         add, lambda a: None, signin_pending=lambda: state[0],
                         on_cancel_signin=cancel)
    qtbot.addWidget(dlg)
    assert not dlg.add_button.isHidden() and dlg.cancel_signin_button.isHidden()
    dlg.add_button.click()
    assert dlg.add_button.isHidden()
    assert not dlg.signin_label.isHidden() and dlg.signin_label.text() == "Signing in…"
    assert not dlg.cancel_signin_button.isHidden()
    dlg.cancel_signin_button.click()
    assert cancels == [1] and not dlg.add_button.isHidden()
    state[0] = True
    dlg.refresh_signin(True)                   # the tray's signin_changed signal
    assert dlg.add_button.isHidden()


def test_oauth_flow_logger_not_info(tmp_path):
    from smartcal.logsetup import setup_logging
    handler = setup_logging(tmp_path / "smartcal.log")
    try:
        flow_log = logging.getLogger("google_auth_oauthlib.flow")
        assert not flow_log.isEnabledFor(logging.INFO)
        assert flow_log.isEnabledFor(logging.WARNING)
    finally:
        logging.getLogger().removeHandler(handler)
        handler.close()
