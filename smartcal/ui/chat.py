"""Chat box for the Claude assistant. Plain text only; calendar writes happen in the
assistant (which has its own approval gate), never here.

Threading: `assistant.reply` / `undo_last` run on a daemon thread; results come back
through a queued signal and are rendered on the UI thread.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                             QScrollArea, QVBoxLayout, QWidget)

from ..assistant import PENDING_HEADER, AssistantError
from ..google_auth import ReauthRequired
from . import theme

log = logging.getLogger(__name__)

MAX_ITEMS = 50
LIST_HEIGHT = 200
THINKING = "Thinking…"
GENERIC_ERROR = "Something went wrong. Please try again."
REAUTH_GENERIC = "Sign in again: right-click the tray icon > Add Google account…"
STILL_WORKING = "Still working…"


def reauth_text(exc: BaseException) -> str:
    who = str(exc.args[0]) if exc.args else ""
    if "@" in who and " " not in who and len(who) <= 320:
        return f"Sign in again for {who}: right-click the tray icon > Add Google account…"
    return REAUTH_GENERIC


def strip_pending(text: str, pending: list[str]) -> str:
    """Model text without the assistant's appended pending block (shown separately)."""
    if pending and PENDING_HEADER in text:
        text = text[:text.rfind(PENDING_HEADER)]
    return text.strip()


class ChatWorker(QObject):
    """Runs one callable off the UI thread; emits `done(result)` or `failed(exception)`."""

    done = pyqtSignal(object)
    failed = pyqtSignal(object)

    def __init__(self, job: Callable[[], object], parent=None):
        super().__init__(parent)
        self._job = job

    def start(self) -> None:
        threading.Thread(target=self._run, name="chat-worker", daemon=True).start()

    def _run(self) -> None:
        try:
            result = self._job()
        except BaseException as exc:  # noqa: BLE001 - reported to the UI, never raised here
            self._emit(self.failed, exc)
        else:
            self._emit(self.done, result)

    @staticmethod
    def _emit(signal, value) -> None:
        try:
            signal.emit(value)
        except RuntimeError:   # the widget was destroyed while the job ran
            pass


def _label(text: str, style: str = "") -> QLabel:
    lab = QLabel(text)
    lab.setTextFormat(Qt.TextFormat.PlainText)
    lab.setWordWrap(True)
    lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    lab.setStyleSheet(style)
    return lab


def _button(text: str) -> QPushButton:
    b = QPushButton(text)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


class _Item(QFrame):
    """One entry in the message list; keeps handles to its buttons."""

    def __init__(self, kind: str):
        super().__init__()
        self.kind = kind          # user | claude | error | note | thinking
        self.undo_btn: QPushButton | None = None
        self.approve_btn: QPushButton | None = None
        self.deny_btn: QPushButton | None = None
        self.undoable = False
        self.setObjectName("chatitem")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(6, 4, 6, 4)
        self.lay.setSpacing(3)

    def set_buttons_enabled(self, which: str, enabled: bool) -> None:
        if which == "undo" and self.undo_btn is not None:
            self.undo_btn.setEnabled(enabled)
        if which == "pending":
            for b in (self.approve_btn, self.deny_btn):
                if b is not None:
                    b.setEnabled(enabled)


class ChatWidget(QWidget):
    changed = pyqtSignal()   # a calendar write or undo happened

    def __init__(self, assistant_factory: Callable[[], object], parent=None):
        super().__init__(parent)
        self._factory = assistant_factory
        self._assistant = None
        self._pending_worker: ChatWorker | None = None
        self._thinking: _Item | None = None
        self._items: list[_Item] = []
        self.setStyleSheet(
            f"QLabel{{color:{theme.TEXT};font-size:12px;}}"
            f"QLineEdit{{background:{theme.BG_NEXT};color:{theme.TEXT};"
            f"border:1px solid {theme.BORDER};padding:2px;}}"
            f"QPushButton{{padding:2px 8px;}}")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        self._content = QWidget()
        self._content.setStyleSheet("background:transparent;")
        self._box = QVBoxLayout(self._content)
        self._box.setContentsMargins(0, 0, 0, 0)
        self._box.setSpacing(4)
        self._box.addStretch(1)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet("QScrollArea{background:transparent;}")
        self._scroll.setFixedHeight(LIST_HEIGHT)
        self._scroll.setWidget(self._content)
        outer.addWidget(self._scroll)

        row = QHBoxLayout()
        row.setSpacing(4)
        self._input = QLineEdit()
        self._input.setPlaceholderText("Ask Claude…")
        self._input.returnPressed.connect(self.send)
        self._send_btn = _button("Send")
        self._send_btn.clicked.connect(lambda _c=False: self.send())
        row.addWidget(self._input, 1)
        row.addWidget(self._send_btn)
        outer.addLayout(row)
        self._new_btn = _button("New chat")
        self._new_btn.clicked.connect(lambda _c=False: self.new_chat())
        outer.addWidget(self._new_btn, 0, Qt.AlignmentFlag.AlignLeft)

    # -- accessors (tests) ---------------------------------------------------
    def items(self) -> list[_Item]:
        return list(self._items)

    def texts(self) -> list[str]:
        out = []
        for it in self._items:
            out.append("\n".join(lab.text() for lab in it.findChildren(QLabel)))
        return out

    def is_busy(self) -> bool:
        return self._pending_worker is not None

    def input_enabled(self) -> bool:
        return self._input.isEnabled() and self._send_btn.isEnabled()

    def focus_input(self) -> None:
        self._input.setFocus()

    # -- sending --------------------------------------------------------------
    def send(self, text: str | None = None) -> None:
        if self.is_busy():
            return
        if text is None:
            text = self._input.text()
            self._input.clear()
        if not text.strip():
            return
        self._freeze_old_buttons()
        self._add_user(text)
        self._start(lambda a: a.reply(text), self._on_reply)

    def _start(self, call: Callable[[object], object], on_done) -> None:
        self._set_busy(True)
        worker = ChatWorker(lambda: call(self._get_assistant()), self)
        worker.done.connect(lambda r, w=worker: self._finish(w, on_done, r))
        worker.failed.connect(lambda e, w=worker: self._finish(w, self._on_error, e))
        self._pending_worker = worker
        worker.start()

    def _get_assistant(self):
        # Runs on the worker thread; only one job runs at a time.
        if self._assistant is None:
            self._assistant = self._factory()
        return self._assistant

    def _finish(self, worker: ChatWorker, handler, value) -> None:
        if worker is not self._pending_worker:
            return
        try:
            self._remove_thinking()
            handler(value)
        except Exception as exc:  # noqa: BLE001 - never leave the chat stuck
            log.error("chat render failed: %s", type(exc).__name__)
            self._add_error(GENERIC_ERROR)
        finally:
            self._pending_worker = None
            self._set_busy(False)
            self._scroll_bottom()

    def _set_busy(self, busy: bool) -> None:
        self._input.setEnabled(not busy)
        self._send_btn.setEnabled(not busy)
        if busy:
            self._thinking = self._add_item("thinking", [THINKING], "color:%s;" % theme.MUTED)
        else:
            self._input.setFocus()

    def _remove_thinking(self) -> None:
        if self._thinking is not None:
            self._drop(self._thinking)
            self._thinking = None

    # -- result handlers ---------------------------------------------------------
    def _on_reply(self, result) -> None:
        pending = [str(p) for p in (getattr(result, "pending", None) or [])]
        changes = [str(c) for c in (getattr(result, "changes", None) or [])]
        text = strip_pending(str(result.text), pending)
        item = _Item("claude")
        item.lay.addWidget(_label("Claude", f"font-weight:bold;color:{theme.LINK};"))
        if text:
            item.lay.addWidget(_label(text))
        if changes:
            block = _label("\n".join(changes), "color:#81c995;")
            item.lay.addWidget(block)
            if getattr(result, "undoable", False):
                item.undoable = True
                for it in self._items:
                    it.set_buttons_enabled("undo", False)
                item.undo_btn = _button("Undo")
                item.undo_btn.clicked.connect(lambda _c=False, i=item: self._undo(i))
                item.lay.addWidget(item.undo_btn, 0, Qt.AlignmentFlag.AlignLeft)
        if pending:
            box = QFrame()
            box.setObjectName("pending")
            box.setStyleSheet(f"QFrame#pending{{background:{theme.BG_NEXT};"
                              f"border:1px solid {theme.WARN};border-radius:4px;}}")
            bl = QVBoxLayout(box)
            bl.setContentsMargins(6, 4, 6, 4)
            bl.addWidget(_label("\n".join(pending), f"color:{theme.WARN};"))
            btns = QHBoxLayout()
            item.approve_btn, item.deny_btn = _button("Approve"), _button("Deny")
            item.approve_btn.clicked.connect(lambda _c=False, i=item: self._decide(i, "yes"))
            item.deny_btn.clicked.connect(lambda _c=False, i=item: self._decide(i, "no"))
            btns.addWidget(item.approve_btn)
            btns.addWidget(item.deny_btn)
            btns.addStretch(1)
            bl.addLayout(btns)
            item.lay.addWidget(box)
        self._append(item)
        if changes:
            self.changed.emit()

    def _on_error(self, exc) -> None:
        if isinstance(exc, AssistantError):
            self._add_error(f"Claude: {exc}")
        elif isinstance(exc, ReauthRequired):
            self._add_error(reauth_text(exc))
        else:
            log.error("chat worker failed: %s", type(exc).__name__)
            self._add_error(GENERIC_ERROR)

    def _decide(self, item: _Item, answer: str) -> None:
        if self.is_busy():
            return
        item.set_buttons_enabled("pending", False)
        self.send(answer)

    def _undo(self, item: _Item) -> None:
        if self.is_busy():
            return
        item.set_buttons_enabled("undo", False)
        self._start(lambda a: a.undo_last(), self._on_undone)

    def _on_undone(self, line) -> None:
        self._add_plain("claude", "Claude", str(line))
        self.changed.emit()

    def new_chat(self) -> None:
        if self.is_busy():
            return
        if self._assistant is not None and not self._assistant.reset():
            self._add_plain("note", None, STILL_WORKING)
            return
        for it in list(self._items):
            self._drop(it)

    # -- rendering ---------------------------------------------------------------
    def _freeze_old_buttons(self) -> None:
        for it in self._items:
            it.set_buttons_enabled("pending", False)

    def _add_user(self, text: str) -> None:
        item = _Item("user")
        item.lay.addWidget(_label("You", f"font-weight:bold;color:{theme.MUTED};"
                                         "qproperty-alignment:AlignRight;"))
        body = _label(text, "qproperty-alignment:AlignRight;")
        item.lay.addWidget(body)
        self._append(item)

    def _add_plain(self, kind: str, who: str | None, text: str) -> None:
        item = _Item(kind)
        if who:
            item.lay.addWidget(_label(who, f"font-weight:bold;color:{theme.LINK};"))
        item.lay.addWidget(_label(text))
        self._append(item)

    def _add_error(self, text: str) -> None:
        item = _Item("error")
        item.lay.addWidget(_label(text, f"color:{theme.WARN};"))
        item.setStyleSheet(f"QFrame#chatitem{{border-left:3px solid {theme.WARN};}}")
        self._append(item)

    def _add_item(self, kind: str, lines: list[str], style: str) -> _Item:
        item = _Item(kind)
        item.lay.addWidget(_label("\n".join(lines), style))
        self._append(item)
        return item

    def _append(self, item: _Item) -> None:
        self._box.insertWidget(self._box.count() - 1, item)
        self._items.append(item)
        while len(self._items) > MAX_ITEMS:
            old = next((i for i in self._items if i is not self._thinking), None)
            if old is None:
                break
            self._drop(old)
        self._scroll_bottom()

    def _drop(self, item: _Item) -> None:
        if item in self._items:
            self._items.remove(item)
        self._box.removeWidget(item)
        item.hide()
        item.deleteLater()

    def _scroll_bottom(self) -> None:
        QTimer.singleShot(0, self._do_scroll)

    def _do_scroll(self) -> None:
        try:
            bar = self._scroll.verticalScrollBar()
            bar.setValue(bar.maximum())
        except RuntimeError:   # widget already deleted
            pass
