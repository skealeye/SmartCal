from datetime import timedelta

from PyQt6.QtWidgets import QLabel

from smartcal.ui import theme
from smartcal.ui.popup import AgendaPopup, AgendaRow
from test_popup import NOW, ev, make


def _rows(popup):
    return [r for r in popup.row_widgets() if isinstance(r, AgendaRow)]


def test_finished_meetings_are_greyed_and_upcoming_ones_are_not(qtbot, tmp_path):
    cache, settings = make(tmp_path, [ev("1", "Done", 8), ev("2", "Later", 11)])  # 8:00-8:30, 11:00
    popup = AgendaPopup(cache, settings)
    qtbot.addWidget(popup)
    popup.refresh(NOW)                          # 9:00
    done, later = _rows(popup)
    assert done.past and not later.past
    assert theme.MUTED in done.findChild(QLabel, "title").styleSheet()
    assert later.fg == theme.TEXT


def test_a_meeting_turns_grey_when_it_ends(qtbot, tmp_path):
    cache, settings = make(tmp_path, [ev("1", "Standup", 9)])        # 9:00-9:30
    popup = AgendaPopup(cache, settings)
    qtbot.addWidget(popup)
    popup.refresh(NOW + timedelta(minutes=10))                       # in progress
    assert not _rows(popup)[0].past
    popup.refresh(NOW + timedelta(minutes=31))                       # over
    assert _rows(popup)[0].past


def test_fill_style_past_row_is_dimmed_with_grey_text(qtbot):
    e = ev("1", "Old", 8)
    live = AgendaRow(e, "8:00 AM", False, "#3fa364", style="fill")
    past = AgendaRow(e, "8:00 AM", False, "#3fa364", style="fill", past=True)
    qtbot.addWidget(live)
    qtbot.addWidget(past)
    assert past.color != live.color and past.fg != live.fg
