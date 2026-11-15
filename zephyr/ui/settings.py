"""zephyr/ui/settings.py  Settings window: bindings list and the new-gesture wizard. Owner: Swayam.

Wizard, about 20 seconds total, air pointer only:
  1 name (filled in as "Gesture 3", editable)
  2 record (ui/recorder.py)
  3 action type and value (big buttons, Paste from clipboard), scope, Save
Test publishes ActionRequest(spec, "settings_test"). Save and Delete publish
T.CUSTOM_RELOAD so the engine reloads the matcher.
"""
from __future__ import annotations

from typing import Any, Optional

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QButtonGroup, QFrame, QGridLayout, QHBoxLayout, QLineEdit,
                               QScrollArea, QStackedWidget, QVBoxLayout, QWidget)

from zephyr.bus import bus
from zephyr.contracts import ActionRequest, SystemMode, T
from zephyr.custom import store, sync
from zephyr.custom.matcher import Matcher
from zephyr.ui.recorder import RecordPage
from zephyr.ui.style import SMALL_BUTTON_H, STYLE, big_button, label, set_kind

SYNC_KINDS = {"synced": "good", "offline": "warn", "syncing": "hint", "off": "hint"}

# fallback until Rohan's actions.runner.describe_action_types() exists
DEFAULT_ACTION_TYPES = [
    {"type": "url", "label": "Website", "hint": "https://…"},
    {"type": "app", "label": "App", "hint": "Microsoft Word"},
    {"type": "hotkey", "label": "Shortcut", "hint": "mod+t"},
    {"type": "text", "label": "Type text", "hint": "Text to paste"},
    {"type": "open", "label": "File", "hint": "~/Desktop/pitch.pptx"},
]
FRIENDLY = {"url": "Website", "app": "App", "hotkey": "Shortcut", "text": "Type text", "open": "File",
            "media": "Media", "system": "System", "macro": "Macro", "http": "Web request",
            "shell": "Command", "mode": "Mode"}

# scope values are Rohan's profile ids
SCOPES = [("*", "All apps"), ("chrome", "Chrome"), ("spotify", "Spotify"), ("word", "Word"),
          ("powerpoint", "PowerPoint")]


# one-value types the wizard can fill with Paste alone. Media is left out because it
# needs a typed value, and the built-in fist tap and swipes already cover it.
WIZARD_TYPES = ("url", "app", "hotkey", "text", "open")


def action_types() -> list[dict[str, str]]:
    """Rohan's actions.runner.describe_action_types(), trimmed to what the wizard offers."""
    try:
        from zephyr.actions.runner import describe_action_types
        raw = describe_action_types()
    except Exception:
        return DEFAULT_ACTION_TYPES
    out = []
    for item in raw:
        if isinstance(item, str):
            item = {"type": item}
        if not isinstance(item, dict) or item.get("type") not in WIZARD_TYPES:
            continue
        kind = item["type"]
        out.append({"type": kind,
                    "label": item.get("label") or FRIENDLY.get(kind, kind.title()),
                    "hint": item.get("value_hint") or item.get("hint") or ""})
    return out or DEFAULT_ACTION_TYPES


def action_label(kind: str, value: str) -> str:
    """Short text for the overlay toast, like "Open github.com"."""
    v = value.strip()
    if kind == "url":
        short = v.split("://", 1)[-1].split("/", 1)[0] or v
        return f"Open {short}"
    if kind in ("app", "open"):
        name = v.rstrip("/").split("/")[-1]
        return f"Open {name}"
    if kind == "hotkey":
        return f"Press {v}"
    if kind == "text":
        return "Type text"
    if kind == "media":
        return {"playpause": "Play / Pause", "next": "Next track", "previous": "Previous track"}.get(v, v)
    return v


def summarize(b: dict[str, Any]) -> str:
    action = b.get("action", {})
    scope = b.get("scope") or ["*"]
    names = dict(SCOPES)
    where = "All apps" if "*" in scope else ", ".join(names.get(s, s) for s in scope)
    what = action.get("label") or action_label(action.get("type", ""), str(action.get("value", "")))
    return f"{what}  ·  {where}"


# ---------------------------------------------------------------- list page

class BindingRow(QFrame):
    def __init__(self, binding: dict[str, Any], on_delete) -> None:
        super().__init__()
        self.setObjectName("row")
        self.binding = binding
        self._on_delete = on_delete
        name = label(binding.get("name", binding.get("id", "?")))
        name.setStyleSheet("font-size: 22pt; font-weight: 700; background: transparent;")
        info = label(summarize(binding), "hint")
        info.setStyleSheet("background: transparent;")

        self.btn_test = big_button("Test", height=SMALL_BUTTON_H)
        self.btn_delete = big_button("Delete", "danger", height=SMALL_BUTTON_H)
        self.btn_test.setMaximumWidth(220)
        self.btn_delete.setMaximumWidth(300)
        self.btn_test.clicked.connect(self._test)
        self.btn_delete.clicked.connect(self._delete)
        self._armed = False

        text = QVBoxLayout()
        text.addWidget(name)
        text.addWidget(info)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(28, 18, 22, 18)
        lay.setSpacing(20)
        lay.addLayout(text, 1)
        lay.addWidget(self.btn_test)
        lay.addWidget(self.btn_delete)

    def _test(self) -> None:
        spec = dict(self.binding.get("action", {}))
        bus.publish(T.ACTION_REQUEST, ActionRequest(spec=spec, source="settings_test"))
        # the action router only runs actions while armed, so say why nothing happened
        state = bus.latest(T.SYSTEM)
        if state is None or state.mode is not SystemMode.ARMED:
            self.btn_test.setText("Arm first")
            QTimer.singleShot(2000, self._reset_test)

    def _reset_test(self) -> None:
        try:
            self.btn_test.setText("Test")
        except RuntimeError:
            pass  # row was already removed

    def _delete(self) -> None:
        # two presses, so a stray pinch cannot delete a gesture
        if not self._armed:
            self._armed = True
            self.btn_delete.setText("Tap again")
            QTimer.singleShot(3000, self._disarm)
            return
        self._on_delete(self.binding.get("id", ""))

    def _disarm(self) -> None:
        self._armed = False
        try:
            self.btn_delete.setText("Delete")
        except RuntimeError:
            pass  # row was already removed


class ListPage(QWidget):
    def __init__(self, on_new, on_changed) -> None:
        super().__init__()
        self._on_changed = on_changed
        title = label("Your gestures", "title")
        hint = label("Built-in gestures always come first. Your own gestures fire when you hold "
                     "them for half a second.", "hint")
        self.btn_new = big_button("+  New gesture", "primary")
        self.btn_new.clicked.connect(on_new)

        self.rows_box = QVBoxLayout()
        self.rows_box.setSpacing(16)
        self.rows_box.addStretch(1)
        holder = QWidget()
        holder.setLayout(self.rows_box)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(holder)

        # cloud sync: a small status label and a big button (sync itself runs on its own thread)
        self.sync_label = label("Sync off", "hint")
        self.sync_label.setWordWrap(False)
        self.sync_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.btn_sync = big_button("Sync now", height=SMALL_BUTTON_H)
        self.btn_sync.setMaximumWidth(260)
        self.btn_sync.clicked.connect(sync.sync_now)
        self._sync_shown: Optional[tuple[str, str]] = None

        top = QHBoxLayout()
        top.setSpacing(18)
        top.addWidget(title, 1)
        top.addWidget(self.sync_label)
        top.addWidget(self.btn_sync)
        self.btn_new.setMaximumWidth(420)
        top.addWidget(self.btn_new)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(48, 36, 48, 36)
        lay.setSpacing(18)
        lay.addLayout(top)
        lay.addWidget(hint)
        lay.addWidget(scroll, 1)
        self.empty = label("No custom gestures yet. Press New gesture to make one.", "hint")
        self.refresh()

    def refresh(self) -> None:
        while self.rows_box.count() > 1:
            item = self.rows_box.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        bindings = store.load_bindings()["bindings"]
        if not bindings:
            self.empty = label("No custom gestures yet. Press New gesture to make one.", "hint")
            self.rows_box.insertWidget(0, self.empty)
        for i, b in enumerate(bindings):
            self.rows_box.insertWidget(i, BindingRow(b, self._delete))

    def _delete(self, gid: str) -> None:
        # store.delete_binding also starts a cloud push in the background
        if gid and store.delete_binding(gid):
            bus.publish(T.CUSTOM_RELOAD, None)
            self._on_changed()
        self.refresh()

    def show_sync(self, state: str, text: str) -> None:
        """"Synced 9:58 PM", "Offline: no AWS credentials", "Syncing…" or "Sync off"."""
        if self._sync_shown == (state, text):
            return
        self._sync_shown = (state, text)
        self.sync_label.setText(text)
        set_kind(self.sync_label, SYNC_KINDS.get(state, "hint"))
        self.btn_sync.setEnabled(state not in ("off", "syncing"))


# ---------------------------------------------------------------- wizard pages

class NamePage(QWidget):
    def __init__(self, on_next, on_cancel) -> None:
        super().__init__()
        self.edit = QLineEdit()
        self.edit.setMinimumHeight(90)
        btn_cancel = big_button("Cancel")
        btn_next = big_button("Next", "primary")
        btn_cancel.clicked.connect(on_cancel)
        btn_next.clicked.connect(on_next)
        buttons = QHBoxLayout()
        buttons.setSpacing(24)
        buttons.addWidget(btn_cancel)
        buttons.addWidget(btn_next)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(48, 36, 48, 36)
        lay.setSpacing(22)
        lay.addWidget(label("Step 1 of 3 · Name", "step"))
        lay.addWidget(label("Name your gesture", "title"))
        lay.addWidget(label("We filled one in. You can rename it later.", "hint"))
        lay.addWidget(self.edit)
        lay.addStretch(1)
        lay.addLayout(buttons)

    def prepare(self) -> None:
        self.edit.setText(store.next_default_name())

    def name(self) -> str:
        return self.edit.text().strip() or store.next_default_name()


class ActionPage(QWidget):
    def __init__(self, on_save, on_back) -> None:
        super().__init__()
        self.types = action_types()
        self.kind = self.types[0]["type"]

        self.type_group = QButtonGroup(self)
        self.type_group.setExclusive(True)
        type_grid = QGridLayout()
        type_grid.setSpacing(18)
        for i, t in enumerate(self.types):
            b = big_button(t["label"], checkable=True)
            b.setProperty("kind", t["type"])
            self.type_group.addButton(b, i)
            type_grid.addWidget(b, i // 3, i % 3)
        self.type_group.buttons()[0].setChecked(True)
        self.type_group.idClicked.connect(self._pick_type)

        self.value = QLineEdit()
        self.value.setMinimumHeight(90)
        btn_paste = big_button("Paste", height=90)
        btn_paste.setMaximumWidth(240)
        btn_paste.clicked.connect(self._paste)
        value_row = QHBoxLayout()
        value_row.setSpacing(18)
        value_row.addWidget(self.value, 1)
        value_row.addWidget(btn_paste)

        self.scope_group = QButtonGroup(self)
        self.scope_group.setExclusive(True)
        scope_row = QHBoxLayout()
        scope_row.setSpacing(14)
        for i, (_sid, text) in enumerate(SCOPES):
            b = big_button(text, height=SMALL_BUTTON_H, checkable=True)
            b.setMinimumWidth(120)
            self.scope_group.addButton(b, i)
            scope_row.addWidget(b)
        self.scope_group.buttons()[0].setChecked(True)

        self.error = label("", "bad")
        btn_back = big_button("Back")
        self.btn_save = big_button("Save", "primary")
        btn_back.clicked.connect(on_back)
        self.btn_save.clicked.connect(on_save)
        buttons = QHBoxLayout()
        buttons.setSpacing(24)
        buttons.addWidget(btn_back)
        buttons.addWidget(self.btn_save)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(48, 36, 48, 36)
        lay.setSpacing(16)
        lay.addWidget(label("Step 3 of 3 · Action", "step"))
        lay.addWidget(label("What should it do?", "title"))
        lay.addLayout(type_grid)
        lay.addLayout(value_row)
        lay.addWidget(label("Where does it work?", "hint"))
        lay.addLayout(scope_row)
        lay.addWidget(self.error)
        lay.addStretch(1)
        lay.addLayout(buttons)
        self._pick_type(0)

    def prepare(self) -> None:
        self.value.clear()
        self.error.setText("")
        self.scope_group.buttons()[0].setChecked(True)

    def _pick_type(self, idx: int) -> None:
        t = self.types[idx]
        self.kind = t["type"]
        self.value.setPlaceholderText(t.get("hint", ""))

    def _paste(self) -> None:
        text = QGuiApplication.clipboard().text().strip()
        if text:
            self.value.setText(text)
            self.error.setText("")
        else:
            self.error.setText("The clipboard is empty. Copy the link or text first.")

    def spec(self) -> Optional[dict[str, Any]]:
        value = self.value.text().strip()
        if not value:
            self.error.setText("Add a value first. Paste works without a keyboard.")
            return None
        if self.kind == "url" and "://" not in value:
            value = "https://" + value
        return {"type": self.kind, "value": value, "label": action_label(self.kind, value)}

    def scope(self) -> list[str]:
        return [SCOPES[max(0, self.scope_group.checkedId())][0]]


# ---------------------------------------------------------------- window

class SettingsWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Zephyr")
        self.setStyleSheet(STYLE)
        self.resize(1280, 860)
        self.matcher = Matcher()   # our own copy, used for the collision check
        self._name = ""
        self._template: Optional[np.ndarray] = None
        self._threshold = 0.0

        self.stack = QStackedWidget()
        self.list_page = ListPage(self.start_wizard, self.matcher.reload)
        self.name_page = NamePage(self._to_record, self.cancel_wizard)
        self.record_page = RecordPage(self.matcher.nearest)
        self.action_page = ActionPage(self._save, self._to_record)
        self.record_page.recorded.connect(self._recorded)
        self.record_page.back.connect(lambda: self.stack.setCurrentWidget(self.name_page))
        for page in (self.list_page, self.name_page, self.record_page, self.action_page):
            self.stack.addWidget(page)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.stack)

        # a cloud pull lands as T.CUSTOM_RELOAD on the sync thread: only set a flag there,
        # and pick it up (with the sync status) on this timer, on the Qt thread
        self._reload_pending = False
        bus.subscribe(T.CUSTOM_RELOAD, self._on_custom_reload)
        self._poll = QTimer(self)
        self._poll.timeout.connect(self._poll_tick)
        self._poll.start(500)
        self._poll_tick()

    def _on_custom_reload(self, _msg) -> None:
        self._reload_pending = True

    def _poll_tick(self) -> None:
        self.list_page.show_sync(*sync.status())
        if self._reload_pending:
            self._reload_pending = False
            self.matcher.reload()
            if self.stack.currentWidget() is self.list_page:
                self.list_page.refresh()

    def showEvent(self, event) -> None:
        self.list_page.refresh()
        super().showEvent(event)

    # ---- wizard flow ----

    def start_wizard(self) -> None:
        self.name_page.prepare()
        self.stack.setCurrentWidget(self.name_page)

    def cancel_wizard(self) -> None:
        self.stack.setCurrentWidget(self.list_page)

    def _to_record(self) -> None:
        self._name = self.name_page.name()
        self.record_page.reset()
        self.stack.setCurrentWidget(self.record_page)

    def _recorded(self, template, threshold: float) -> None:
        self._template = np.asarray(template, dtype=np.float32)
        self._threshold = float(threshold)
        self.action_page.prepare()
        self.stack.setCurrentWidget(self.action_page)

    def _save(self) -> None:
        spec = self.action_page.spec()
        if spec is None or self._template is None:
            return
        # add_binding also starts a cloud push in the background
        store.add_binding(self._name, self._template, self._threshold, spec,
                          scope=self.action_page.scope())
        bus.publish(T.CUSTOM_RELOAD, None)
        self.matcher.reload()
        self._template = None
        self.list_page.refresh()
        self.stack.setCurrentWidget(self.list_page)
