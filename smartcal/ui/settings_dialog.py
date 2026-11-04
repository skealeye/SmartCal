"""Settings dialog: calendars (show/mute/lead time), general, accounts.

Calendar and general edits apply on OK (one atomic save). Adding/removing an
account takes effect immediately, since it touches tokens and the cache.
"""
from __future__ import annotations

import logging

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QFrame,
                             QHBoxLayout, QLabel, QLineEdit, QListWidget, QMessageBox,
                             QPushButton, QScrollArea, QSpinBox, QTabWidget, QVBoxLayout,
                             QWidget)

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
                 parent=None):
        super().__init__(parent)
        self.settings, self.cache = settings, cache
        self._accounts_provider = accounts_provider
        self._on_add, self._on_remove = on_add_account, on_remove_account
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
        if not by_account:
            inner_layout.addWidget(QLabel("No calendars yet. Add an account and sync."))
        for account, calendars in by_account.items():
            head = QLabel(account)
            head.setTextFormat(Qt.TextFormat.PlainText)
            head.setStyleSheet("font-weight:bold;margin-top:8px;")
            inner_layout.addWidget(head)
            for cal in calendars:
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

    # -- accounts tab ------------------------------------------------------
    def _build_accounts(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        self.account_list = QListWidget()
        layout.addWidget(self.account_list)
        row = QHBoxLayout()
        self.add_button = QPushButton("Add…")
        self.remove_button = QPushButton("Remove…")
        self.add_button.clicked.connect(lambda _c=False: self._on_add())
        self.remove_button.clicked.connect(lambda _c=False: self.remove_selected())
        row.addWidget(self.add_button)
        row.addWidget(self.remove_button)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addWidget(QLabel("Adding or removing an account takes effect immediately."))
        self.refresh_accounts()
        return w

    def refresh_accounts(self) -> None:
        self.account_list.clear()
        self.account_list.addItems(list(self._accounts_provider()))

    def _ask_confirm(self, account: str) -> bool:
        answer = QMessageBox.question(
            self, "Remove account",
            f"Remove {account}? Its sign-in and cached events will be deleted from this PC.")
        return answer == QMessageBox.StandardButton.Yes

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
            QMessageBox.warning(self, "Remove account", f"Could not remove {account}.")
            return
        self.refresh_accounts()
        self._build_calendars()

    # -- apply -------------------------------------------------------------
    def accept(self) -> None:
        s = self.settings
        s.default_lead_minutes = self.default_lead.value()
        for cid, (show, mute, spin, orig, primary) in self._rows.items():
            if show.isChecked() == s.is_hidden(cid, primary):  # visibility changed
                s.set_hidden_nosave(cid, not show.isChecked())
            (s.muted.add if mute.isChecked() else s.muted.discard)(cid)
            if spin.value() != orig:
                s.lead_minutes[cid] = spin.value()
        s.autostart = self.autostart_box.isChecked()
        s.claude_model = self.model_edit.text().strip() or s.claude_model
        s.save()
        self.applied.emit()
        super().accept()
