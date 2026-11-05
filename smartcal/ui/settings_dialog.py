"""Settings dialog: calendars (show/mute/lead time), general, accounts.

Calendar and general edits apply on OK (one atomic save). Adding/removing an
account takes effect immediately, since it touches tokens and the cache.
"""
from __future__ import annotations

import copy
import logging

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QFrame,
                             QHBoxLayout, QLabel, QLineEdit, QListWidget, QMessageBox,
                             QPushButton, QScrollArea, QSpinBox, QTabWidget, QVBoxLayout,
                             QWidget)

from .. import agenda
from . import theme

log = logging.getLogger(__name__)


def _spin(value: int) -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(0, 1440)
    spin.setSuffix(" min")
    spin.setValue(value)
    return spin


class SettingsDialog(QDialog):
    applied = pyqtSignal()

    def __init__(self, settings, cache, accounts_provider, on_add_account, on_remove_account,
                 parent=None, signin_pending=None, on_cancel_signin=None):
        super().__init__(parent)
        self.settings, self.cache = settings, cache
        self._accounts_provider = accounts_provider
        self._on_add, self._on_remove = on_add_account, on_remove_account
        self._signin_pending = signin_pending or (lambda: False)
        self._on_cancel_signin = on_cancel_signin
        self.confirm = self._ask_confirm  # replaceable in tests
        self.setWindowTitle("SmartCal settings")
        self.resize(520, 560)
        # calendar id -> (show checkbox, mute checkbox, lead spinbox, lead when opened, primary)
        self._rows: dict[str, tuple[QCheckBox, QCheckBox, QSpinBox, int, bool]] = {}

        tabs = QTabWidget()
        self._cal_tab = QWidget()
        self._cal_layout = QVBoxLayout(self._cal_tab)
        tabs.addTab(self._cal_tab, "Calendars")
        tabs.addTab(self._build_general(), "General")
        tabs.addTab(self._build_meetings(), "Meetings")
        tabs.addTab(self._build_accounts(), "Accounts")
        self._build_calendars()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer = QVBoxLayout(self)
        outer.addWidget(tabs)
        outer.addWidget(buttons)

    # -- calendars tab -----------------------------------------------------
    def _build_calendars(self) -> None:
        """(Re)build the calendar list, keeping edits already made in the dialog."""
        pending = {cid: (r[0].isChecked(), r[1].isChecked(), r[2].value())
                   for cid, r in self._rows.items()}
        default = (self.default_lead.value() if hasattr(self, "default_lead")
                   else self.settings.default_lead_minutes)
        while self._cal_layout.count():
            item = self._cal_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._rows = {}

        top = QFormLayout()
        self.default_lead = _spin(default)
        top.addRow("Default reminder lead time", self.default_lead)
        top_w = QWidget()
        top_w.setLayout(top)
        self._cal_layout.addWidget(top_w)

        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        by_account = self.cache.calendars_by_account()
        owners = agenda.calendar_owners(self.cache)
        if not by_account:
            inner_layout.addWidget(QLabel("No calendars yet. Add an account and sync."))
        for account, calendars in by_account.items():
            head = QLabel(account)
            head.setTextFormat(Qt.TextFormat.PlainText)
            head.setStyleSheet("font-weight:bold;margin-top:8px;")
            inner_layout.addWidget(head)
            for cal in calendars:
                owner = owners.get(cal.id, account)
                if owner != account:
                    # Shared calendar: one calendar, set up under the account that owns it.
                    same = QLabel(f"{cal.name} (same calendar as in {owner})")
                    same.setTextFormat(Qt.TextFormat.PlainText)
                    same.setEnabled(False)
                    inner_layout.addWidget(same)
                    continue
                row = QHBoxLayout()
                show = QCheckBox(cal.name.replace("&", "&&"))
                show.setToolTip("Show this calendar")
                show.setChecked(not self.settings.is_hidden(cal.id, cal.primary))
                mute = QCheckBox("Mute")
                mute.setToolTip("No reminders for this calendar")
                mute.setChecked(self.settings.is_muted(cal.id))
                lead_now = self.settings.lead_for(cal.id)
                spin = _spin(lead_now)
                spin.setToolTip("Remind this long before the event starts")
                if cal.id in pending:
                    show.setChecked(pending[cal.id][0])
                    mute.setChecked(pending[cal.id][1])
                    spin.setValue(pending[cal.id][2])
                row.addWidget(show, 1)
                row.addWidget(mute)
                row.addWidget(spin)
                inner_layout.addLayout(row)
                self._rows[cal.id] = (show, mute, spin, lead_now, cal.primary)
        inner_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(inner)
        self._cal_layout.addWidget(scroll, 1)

    # -- general tab -------------------------------------------------------
    def _build_general(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        self.autostart_box = QCheckBox("Start with Windows")
        self.autostart_box.setChecked(self.settings.autostart)
        self.model_edit = QLineEdit(self.settings.claude_model)
        form.addRow(self.autostart_box)
        form.addRow("Claude model", self.model_edit)
        return w

    # -- meetings tab (hidden meetings, meeting colors; local to SmartCal) ---
    def _build_meetings(self) -> QWidget:
        """Lists what was hidden or colored. Unhide / Reset apply on OK."""
        self._unhide: set[str] = set()
        self._reset_colors: set[str] = set()
        # key -> (title label, Unhide/Reset button)
        self.hidden_rows: dict[str, tuple[QLabel, QPushButton]] = {}
        self.color_rows: dict[str, tuple[QLabel, QPushButton]] = {}
        w = QWidget()
        outer = QVBoxLayout(w)
        note = QLabel("Hidden and colored meetings are kept in SmartCal only; "
                      "nothing changes in Google Calendar.")
        note.setWordWrap(True)
        outer.addWidget(note)
        inner = QWidget()
        lay = QVBoxLayout(inner)

        def heading(text: str) -> None:
            head = QLabel(text)
            head.setStyleSheet("font-weight:bold;margin-top:8px;")
            lay.addWidget(head)

        def add_row(text: str, detail: str, button_text: str, on_click) -> tuple:
            row = QHBoxLayout()
            label = QLabel(text)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            sub = QLabel(detail)
            sub.setTextFormat(Qt.TextFormat.PlainText)
            sub.setEnabled(False)
            button = QPushButton(button_text)
            button.clicked.connect(lambda _c=False: on_click())
            row.addWidget(label, 1)
            row.addWidget(sub)
            row.addWidget(button)
            lay.addLayout(row)
            return label, button

        heading("Hidden meetings")
        if not self.settings.hidden_events:
            lay.addWidget(QLabel("No hidden meetings. Use Hide… on a meeting to hide it."))
        for key, info in reversed(list(self.settings.hidden_events.items())):
            scope = "whole series" if info.get("scope") == "series" else "this meeting"
            label, button = add_row(info.get("title") or "(no title)",
                                    f"{scope} · {info.get('when', '')}", "Unhide",
                                    lambda k=key: self._mark(k, self._unhide, self.hidden_rows))
            self.hidden_rows[key] = (label, button)

        heading("Colored meetings")
        if not self.settings.event_colors:
            lay.addWidget(QLabel("No colored meetings. Use Color… on a meeting to color it."))
        for key, color in reversed(list(self.settings.event_colors.items())):
            title = self.settings.event_color_titles.get(key) or "(no title)"
            scope = "whole series" if key.startswith("s:") else "this meeting"
            label, button = add_row(title, scope, "Reset",
                                    lambda k=key: self._mark(k, self._reset_colors,
                                                             self.color_rows))
            swatch = QLabel()
            swatch.setFixedSize(14, 14)
            swatch.setStyleSheet(f"background:{theme.stripe_color(color)};border-radius:3px;")
            swatch.setAccessibleName(f"color {theme.stripe_color(color)}")
            lay.itemAt(lay.count() - 1).layout().insertWidget(0, swatch)
            self.color_rows[key] = (label, button)
        lay.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)
        return w

    def _mark(self, key: str, pending: set, rows: dict) -> None:
        """Toggle a pending Unhide/Reset; nothing is saved until OK."""
        label, button = rows[key]
        verb = "Unhide" if rows is self.hidden_rows else "Reset"
        if key in pending:
            pending.discard(key)
            button.setText(verb)
        else:
            pending.add(key)
            button.setText(f"Undo {verb.lower()}")
        font = label.font()
        font.setStrikeOut(key in pending)
        label.setFont(font)

    # -- accounts tab ------------------------------------------------------
    def _build_accounts(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        self.account_list = QListWidget()
        layout.addWidget(self.account_list)
        row = QHBoxLayout()
        self.add_button = QPushButton("Add…")
        self.remove_button = QPushButton("Remove…")
        self.add_button.clicked.connect(lambda _c=False: self._add())
        self.signin_label = QLabel("Signing in…")
        self.cancel_signin_button = QPushButton("Cancel")
        self.cancel_signin_button.clicked.connect(lambda _c=False: self._cancel_signin())
        self.remove_button.clicked.connect(lambda _c=False: self.remove_selected())
        row.addWidget(self.add_button)
        row.addWidget(self.signin_label)
        row.addWidget(self.cancel_signin_button)
        row.addWidget(self.remove_button)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addWidget(QLabel("Adding or removing an account takes effect immediately."))
        self.refresh_accounts()
        self.refresh_signin()
        return w

    def refresh_signin(self, *_args) -> None:
        """While a Google sign-in is pending: 'Signing in… [Cancel]' instead of Add."""
        pending = bool(self._signin_pending())
        self.add_button.setVisible(not pending)
        self.signin_label.setVisible(pending)
        self.cancel_signin_button.setVisible(pending)

    def _add(self) -> None:
        self._on_add()
        self.refresh_signin()

    def _cancel_signin(self) -> None:
        if self._on_cancel_signin:
            self._on_cancel_signin()
        self.refresh_signin()

    def refresh_accounts(self) -> None:
        self.account_list.clear()
        self.account_list.addItems(list(self._accounts_provider()))

    def _ask_confirm(self, account: str) -> bool:
        box = QMessageBox(QMessageBox.Icon.Question, "Remove account",
                          f"Remove {account}? Its sign-in and cached events will be "
                          "deleted from this PC.",
                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, self)
        box.setTextFormat(Qt.TextFormat.PlainText)
        return box.exec() == QMessageBox.StandardButton.Yes

    def _warn(self, title: str, text: str) -> None:
        box = QMessageBox(QMessageBox.Icon.Warning, title, text,
                          QMessageBox.StandardButton.Ok, self)
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.exec()

    def remove_selected(self) -> None:
        item = self.account_list.currentItem()
        if item is None:
            return
        account = item.text()
        if not self.confirm(account):
            return
        try:
            self._on_remove(account)
        except Exception:  # noqa: BLE001
            log.exception("remove account failed")
            self._warn("Remove account", f"Could not remove {account}.")
            return
        self.refresh_accounts()
        self._build_calendars()

    # -- apply -------------------------------------------------------------
    _FIELDS = ("hidden_calendars", "shown_calendars", "lead_minutes", "default_lead_minutes",
               "muted", "autostart", "claude_model", "hidden_events", "event_colors",
               "event_color_titles")

    def accept(self) -> None:
        s = self.settings
        backup = {f: copy.deepcopy(getattr(s, f)) for f in self._FIELDS}
        s.default_lead_minutes = self.default_lead.value()
        for cid, (show, mute, spin, orig, primary) in self._rows.items():
            if show.isChecked() == s.is_hidden(cid, primary):  # visibility changed
                s.set_hidden_nosave(cid, not show.isChecked())
            (s.muted.add if mute.isChecked() else s.muted.discard)(cid)
            if spin.value() != orig:
                s.lead_minutes[cid] = spin.value()
        s.autostart = self.autostart_box.isChecked()
        s.claude_model = self.model_edit.text().strip() or s.claude_model
        for key in self._unhide:
            s.hidden_events.pop(key, None)
        for key in self._reset_colors:
            s.event_colors.pop(key, None)
            s.event_color_titles.pop(key, None)
        try:
            s.save()
        except OSError as exc:
            for f, v in backup.items():  # nothing half-applied
                setattr(s, f, v)
            log.exception("could not save settings")
            self._warn("Settings", f"Could not save settings: {exc}")
            return
        self.applied.emit()
        super().accept()
