"""Settings dialog: calendars (show/mute/lead time), general, accounts.

Calendar and general edits apply on OK (one atomic save). Adding/removing an
account takes effect immediately, since it touches tokens and the cache.
"""
from __future__ import annotations

import copy
import logging

from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                             QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                             QMessageBox, QPushButton, QScrollArea, QSizePolicy, QSpinBox,
                             QTabWidget, QVBoxLayout, QWidget)

from .. import agenda
from ..textutil import plain_tip
from .meeting_menus import STYLE_NAMES
from . import theme

log = logging.getLogger(__name__)


ACCOUNT_ROLE = Qt.ItemDataRole.UserRole


class ElidedLabel(QLabel):
    """Plain text elided in the middle ("first.last…@example.com") to its width; the
    full text is in the tooltip."""

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self._full = text
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(40)
        self.setToolTip(plain_tip(text))
        self._elide()

    def full_text(self) -> str:
        return self._full

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()

    def _elide(self) -> None:
        self.setText(self.fontMetrics().elidedText(
            self._full, Qt.TextElideMode.ElideMiddle, max(0, self.width())))


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
        # Also one click away: right-click a meeting > Color style.
        self.tile_style_box = QComboBox()
        for key, name in STYLE_NAMES:
            self.tile_style_box.addItem(name, key)
        self._tile_style_orig = getattr(self.settings, "tile_style", "stripe")
        self.tile_style_box.setCurrentIndex(
            max(0, self.tile_style_box.findData(self._tile_style_orig)))
        form.addRow("Meeting color", self.tile_style_box)
        return w

    # -- meetings tab (hidden meetings, meeting colors; local to SmartCal) ---
    def _build_meetings(self) -> QWidget:
        """Lists what was hidden or colored. Unhide / Reset apply on OK."""
        self._unhide: set[str] = set()
        self._reset_colors: set[str] = set()
        # key -> (title label, Unhide/Reset button)
        self.hidden_rows: dict[str, tuple[QLabel, QPushButton]] = {}
        self.color_rows: dict[str, tuple[QLabel, QPushButton]] = {}
        self._meetings_seen = None
        w = QWidget()
        outer = QVBoxLayout(w)
        note = QLabel("Hidden and colored meetings are kept in SmartCal only; "
                      "nothing changes in Google Calendar.")
        note.setWordWrap(True)
        outer.addWidget(note)
        self._meetings_scroll = QScrollArea()
        self._meetings_scroll.setWidgetResizable(True)
        self._meetings_scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(self._meetings_scroll, 1)
        self._fill_meetings()
        return w

    def _meetings_snapshot(self):
        s = self.settings
        return (list(s.hidden_events.items()), list(s.event_colors.items()),
                dict(s.event_color_titles))

    def refresh_meetings(self) -> None:
        """Re-list hidden/colored meetings if they changed while the dialog was open
        (e.g. a hide from the popup). Pending Unhide/Reset marks are kept."""
        if self._meetings_snapshot() != self._meetings_seen:
            self._fill_meetings()

    def event(self, e):
        if e.type() == QEvent.Type.WindowActivate:
            self.refresh_meetings()
            self.sync_account_boxes()
        return super().event(e)

    def _fill_meetings(self) -> None:
        self._meetings_seen = self._meetings_snapshot()
        self.hidden_rows, self.color_rows = {}, {}
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
            lay.addWidget(QLabel("No hidden meetings. Right-click a meeting > Hide this meeting to hide it."))
        for key, info in reversed(list(self.settings.hidden_events.items())):
            scope = "whole series" if info.get("scope") == "series" else "this meeting"
            label, button = add_row(info.get("title") or "(no title)",
                                    f"{scope} · {info.get('when', '')}", "Unhide",
                                    lambda k=key: self._mark(k, self._unhide, self.hidden_rows))
            self.hidden_rows[key] = (label, button)

        heading("Colored meetings")
        if not self.settings.event_colors:
            lay.addWidget(QLabel("No colored meetings. Right-click a meeting > Color to color it."))
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
        for key in self._unhide & set(self.hidden_rows):
            self._show_mark(key, self._unhide, self.hidden_rows)
        for key in self._reset_colors & set(self.color_rows):
            self._show_mark(key, self._reset_colors, self.color_rows)
        self._meetings_scroll.setWidget(inner)      # the old list widget is deleted

    def _mark(self, key: str, pending: set, rows: dict) -> None:
        """Toggle a pending Unhide/Reset; nothing is saved until OK."""
        if key in pending:
            pending.discard(key)
        else:
            pending.add(key)
        self._show_mark(key, pending, rows)

    def _show_mark(self, key: str, pending: set, rows: dict) -> None:
        label, button = rows[key]
        verb = "Unhide" if rows is self.hidden_rows else "Reset"
        button.setText(f"Undo {verb.lower()}" if key in pending else verb)
        font = label.font()
        font.setStrikeOut(key in pending)
        label.setFont(font)

    # -- accounts tab ------------------------------------------------------
    def _build_accounts(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        # account -> its "Show in SmartCal" box, and the state that box started from:
        # only boxes the owner changed here are applied on OK (a change made meanwhile
        # from the tray's Accounts menu is never overwritten).
        self.account_shown: dict[str, QCheckBox] = {}
        self._account_orig: dict[str, bool] = {}
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
        hint = QLabel("Show in SmartCal: a hidden account keeps syncing, and its meetings "
                      "still count as busy for Claude; they are just not shown. Applies on "
                      "OK.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
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

    def _account_changed(self, account: str) -> bool:
        box = self.account_shown.get(account)
        return box is not None and box.isChecked() != self._account_orig.get(account)

    def refresh_accounts(self) -> None:
        """(Re)list the accounts, each with its "Show in SmartCal" box; boxes already
        changed in the dialog keep their pending state."""
        pending = {a: b.isChecked() for a, b in self.account_shown.items()
                   if self._account_changed(a)}
        self.account_list.clear()
        self.account_shown, self._account_orig = {}, {}
        for account in self._accounts_provider():
            # The email is drawn by the row's own label (elided in the middle when
            # long, never under the checkbox); the item keeps the account as data.
            item = QListWidgetItem()
            item.setData(ACCOUNT_ROLE, account)
            self.account_list.addItem(item)
            row = QWidget()
            row_lay = QHBoxLayout(row)
            row_lay.setContentsMargins(4, 0, 4, 0)
            label = ElidedLabel(account)
            label.setObjectName("account")
            row_lay.addWidget(label, 1)
            box = QCheckBox("Show in SmartCal")
            box.setToolTip("Show this account's meetings in SmartCal")
            shown = not self.settings.is_account_hidden(account)
            self._account_orig[account] = shown
            box.setChecked(pending.get(account, shown))
            row_lay.addWidget(box)
            item.setSizeHint(row.sizeHint())
            self.account_list.setItemWidget(item, row)
            self.account_shown[account] = box

    def sync_account_boxes(self) -> None:
        """Boxes the owner did not touch follow changes made elsewhere (the tray)."""
        for account, box in self.account_shown.items():
            if not self._account_changed(account):
                shown = not self.settings.is_account_hidden(account)
                self._account_orig[account] = shown
                box.setChecked(shown)

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
        account = item.data(ACCOUNT_ROLE)
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
               "event_color_titles", "hidden_accounts", "tile_style")

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
        style = self.tile_style_box.currentData()
        if style != self._tile_style_orig:   # only if changed here (the right-click
            s.tile_style = style             # menu may have changed it meanwhile)
        s.claude_model = self.model_edit.text().strip() or s.claude_model
        for account, box in self.account_shown.items():
            if self._account_changed(account):   # only what was changed here
                (s.hidden_accounts.discard if box.isChecked()
                 else s.hidden_accounts.add)(account)
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
