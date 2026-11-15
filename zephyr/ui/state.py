"""zephyr/ui/state.py  Thread-safe store the overlay paint timer reads from. Owner: Swayam.

Two patterns (Swayam doc 4.1):
  - latest value topics are read with bus.latest() on the UI thread, once per tick
  - streams we must not miss (strokes, action toasts) are appended to deques by
    tiny bus handlers and drained here on the UI thread

Bus handlers in this file only append or store. They never touch Qt.
Everything else (animation clocks, ink, toasts) lives on the UI thread.
"""
from __future__ import annotations

import time
from collections import deque
from typing import Any, Optional

from zephyr.bus import bus
from zephyr.contracts import (ActionFired, ContextState, HandsFrame, HoldProgress, LevelValue,
                              PointerEvent, PresenceState, Role, StrokeEvent, SystemMode,
                              SystemState, T, WriteState)

# timings in ms (Swayam doc 4.3 and 4.4)
POINTER_FADE_MS = 120
TOAST_MS = 1200
LEVEL_HIDE_MS = 800
CHIP_ANIM_MS = 220
WRITE_ERROR_MS = 1500
ACCESS_IN_MS = 600
ACCESS_TOTAL_MS = 2000
HOLD_STALE_MS = 250      # a hold that stops updating is hidden after this


def now_ms() -> int:
    return int(time.monotonic() * 1000)


class UiState:
    def __init__(self) -> None:
        self._strokes_in: deque[StrokeEvent] = deque(maxlen=4096)
        self._actions_in: deque[ActionFired] = deque(maxlen=64)
        self._level_in: Optional[tuple[LevelValue, int]] = None
        # one slot per hold kind: presence publishes "grant" about 10 times a second,
        # so a single "latest hold" would keep wiping the engine's palm and custom rings
        self._holds_in: dict[str, tuple[HoldProgress, int]] = {}

        bus.subscribe(T.STROKE, self._strokes_in.append)
        bus.subscribe(T.ACTION, self._actions_in.append)
        bus.subscribe(T.LEVEL_VALUE, self._on_level)
        bus.subscribe(T.HOLD, self._on_hold)

        # latest values, refreshed by update()
        self.hands: Optional[HandsFrame] = None
        self.system: Optional[SystemState] = None
        self.presence: Optional[PresenceState] = None
        self.pointer: Optional[PointerEvent] = None
        self.context: Optional[ContextState] = None
        self.write: Optional[WriteState] = None
        self.hold: Optional[HoldProgress] = None
        self.level: Optional[LevelValue] = None
        self.level_t: int = 0

        # UI-thread state
        self.pointer_alpha = 0.0
        self.pointer_xy = (0.5, 0.5)
        self.toast: Optional[ActionFired] = None
        self.toast_t = 0
        self.strokes: dict[int, list[tuple[float, float]]] = {}
        self.chip_label = ""
        self.chip_prev = ""
        self.chip_t = -10_000
        self.access_kind: Optional[str] = None   # "granted" or "returned"
        self.access_t = -10_000
        self.write_error = ""
        self.write_error_t = -10_000
        self.write_phase = "idle"
        self.spin_t0 = now_ms()

        self._last_role: Optional[Role] = None
        self._last_seen: tuple[Any, ...] = ()
        self._last_tick = now_ms()

    # ---- bus handlers: store only, return at once ----

    def _on_level(self, msg: LevelValue) -> None:
        self._level_in = (msg, now_ms())

    def _on_hold(self, msg: HoldProgress) -> None:
        self._holds_in[msg.kind] = (msg, now_ms())

    # ---- UI thread ----

    def update(self, t: int) -> bool:
        """Pull new data and step animations. Returns True when a repaint is needed."""
        dt = max(0, t - self._last_tick)
        self._last_tick = t

        self.hands = bus.latest(T.HANDS)
        self.system = bus.latest(T.SYSTEM)
        self.presence = bus.latest(T.PRESENCE)
        self.pointer = bus.latest(T.POINTER)
        self.context = bus.latest(T.CONTEXT)
        write = bus.latest(T.WRITE)

        changed = False

        # hold ring: the newest active hold wins. Progress 0 hides it at once, and a hold
        # that went quiet is hidden too. "grant" is drawn from PRESENCE.grant_progress instead.
        self.hold = None
        newest = -1
        for kind, (msg, ht) in list(self._holds_in.items()):
            if kind == "grant" or msg.progress <= 0.0 or t - ht >= HOLD_STALE_MS:
                continue
            if ht > newest:
                self.hold, newest = msg, ht

        if self._level_in is not None:
            self.level, self.level_t = self._level_in

        # pointer fade in and out
        target = 1.0 if (self.pointer is not None and self.pointer.visible) else 0.0
        if self.pointer is not None and self.pointer.visible:
            self.pointer_xy = (self.pointer.x, self.pointer.y)
        if self.pointer_alpha != target:
            step = dt / POINTER_FADE_MS
            if target > self.pointer_alpha:
                self.pointer_alpha = min(target, self.pointer_alpha + step)
            else:
                self.pointer_alpha = max(target, self.pointer_alpha - step)
            changed = True

        # toasts: newest wins
        while self._actions_in:
            self.toast = self._actions_in.popleft()
            self.toast_t = t
            changed = True

        # ink
        while self._strokes_in:
            ev = self._strokes_in.popleft()
            pts = self.strokes.setdefault(ev.stroke_id, [])
            if ev.phase == "begin":
                pts.clear()
            pts.append((ev.x, ev.y))
            changed = True

        # writing flow
        if write is not None and write.phase != self.write_phase:
            if write.phase in ("accepted", "cancelled", "idle"):
                self.strokes.clear()
            if write.phase == "error":
                self.write_error = write.text or "Could not read, try again"
                self.write_error_t = t
            if write.phase == "transcribing":
                self.spin_t0 = t
            self.write_phase = write.phase
            changed = True
        self.write = write

        # app chip animation when the front app changes
        label = self.context.app_label if self.context is not None else ""
        if label != self.chip_label:
            self.chip_prev, self.chip_label, self.chip_t = self.chip_label, label, t
            changed = True

        # access granted / control returned
        role = self.presence.controller_role if self.presence is not None else None
        if role != self._last_role:
            if role is Role.GUEST:
                self.access_kind, self.access_t = "granted", t
            elif role is Role.OWNER and self._last_role is Role.GUEST:
                self.access_kind, self.access_t = "returned", t
            self._last_role = role
            changed = True

        seen = (self.hands, self.system, self.presence, self.pointer, self.context, self.write,
                self.hold, self.level)
        if any(a is not b for a, b in zip(seen, self._last_seen)) or len(seen) != len(self._last_seen):
            changed = True
        self._last_seen = seen

        return changed or self.animating(t)

    def animating(self, t: int) -> bool:
        return (
            (self.toast is not None and t - self.toast_t < TOAST_MS + 200)
            or (self.level is not None and t - self.level_t < LEVEL_HIDE_MS + 200)
            or t - self.chip_t < CHIP_ANIM_MS
            or (self.access_kind is not None and t - self.access_t < ACCESS_TOTAL_MS)
            or t - self.write_error_t < WRITE_ERROR_MS
            or self.write_phase == "transcribing"
            or self.hold is not None
        )

    # ---- helpers for the painters ----

    def status_text(self) -> tuple[str, str]:
        """(text, tone). tone is one of off, ready, paused, guest."""
        if self.system is None:
            return "Starting…", "off"
        mode = self.system.mode
        reason = self.system.reason or (self.presence.reason if self.presence else "")
        if mode is SystemMode.DISARMED:
            return "Off: raise both palms", "off"
        if mode is SystemMode.SUSPENDED:
            text = {
                "looking_away": "Paused: looking away",
                "no_owner": "Paused: no owner",
                "owner_left": "Paused: owner left",
            }.get(reason, "Paused")
            return text, "paused"
        if self.presence is not None and self.presence.controller_role is Role.GUEST:
            return "Guest in control", "guest"
        return "Ready", "ready"
