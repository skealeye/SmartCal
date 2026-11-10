from smartcal import toast
from smartcal.ui import alerts
from smartcal.ui.alerts import AlertManager


def test_card_shows_title_body_and_join(qtbot):
    mgr = AlertManager()
    mgr.show("Standup", "in 10 minutes", "https://meet.example.com/abc")
    qtbot.waitUntil(lambda: len(mgr.cards) == 1, timeout=2000)
    card = mgr.cards[0]
    assert card.isVisible()
    assert card.title_label.text() == "Standup" and card.join_button is not None
    card.dismiss()
    assert mgr.cards == []


def test_no_join_button_for_unsafe_or_missing_url(qtbot):
    mgr = AlertManager()
    mgr.show("A", "b", "javascript:alert(1)")
    mgr.show("C", "d", None)
    qtbot.waitUntil(lambda: len(mgr.cards) == 2, timeout=2000)
    assert all(c.join_button is None for c in mgr.cards)
    for c in list(mgr.cards):
        c.dismiss()


def test_cards_stack_and_are_capped(qtbot):
    mgr = AlertManager()
    for i in range(alerts.MAX_CARDS + 2):
        mgr.show(f"m{i}", "x", None)
    qtbot.waitUntil(lambda: mgr.cards and mgr.cards[-1].title_label.text() == f"m{alerts.MAX_CARDS + 1}",
                    timeout=2000)
    assert len(mgr.cards) == alerts.MAX_CARDS
    ys = [c.y() for c in mgr.cards]
    assert ys == sorted(ys)          # newest lowest on screen
    for c in list(mgr.cards):
        c.dismiss()


def test_title_is_plain_text_not_html(qtbot):
    mgr = AlertManager()
    mgr.show("<b>bold</b>", "<img src=x>", None)
    qtbot.waitUntil(lambda: len(mgr.cards) == 1, timeout=2000)
    assert mgr.cards[0].title_label.textFormat().name == "PlainText"
    mgr.cards[0].dismiss()


def test_toast_sender_uses_the_card_and_skips_windows_toast():
    shown, native = [], []

    class Cards:
        def show(self, *a): shown.append(a)

    class Note:
        def __init__(self, **kw): native.append(kw)
        def show(self): native.append("shown")
    toast.ToastSender(notification_cls=Note, alerts=Cards())("T", "B", "https://x.example/")
    assert shown == [("T", "B", "https://x.example/")] and native == []


def test_toast_sender_falls_back_when_the_card_fails():
    native = []

    class Cards:
        def show(self, *a): raise RuntimeError("boom")

    class Note:
        def __init__(self, **kw): pass
        def show(self): native.append("shown")
    toast.ToastSender(notification_cls=Note, alerts=Cards())("T", "B")
    assert native == ["shown"]
