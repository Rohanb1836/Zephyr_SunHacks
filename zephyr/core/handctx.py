"""zephyr/core/handctx.py    Per-hand gesture state shared by the engine and detectors. Owner: Aadharsh."""
from __future__ import annotations

from collections import deque
from typing import Optional

from zephyr import contracts
from zephyr.contracts import HandObs

# per-hand states
IDLE = "idle"
POINTER = "pointer"
PINCHING = "pinching"
PEN = "pen"
FIST_PENDING = "fist_pending"
LEVEL = "level"
HOLD_PALM = "hold_palm"
HOLD_CUSTOM = "hold_custom"


class HandCtx:
    def __init__(self, hand_id: int) -> None:
        self.hand_id = hand_id
        self.state = IDLE
        self.hand: Optional[HandObs] = None
        # per-frame values (filled by the engine before detectors run)
        self.ts = 0
        self.cx = 0.0           # palm centre, normalized image coords
        self.cy = 0.0
        self.scale = 0.1
        self.facing = False     # palm faces the camera
        self.speed = 0.0        # palm speed, scales per second (about 100 ms window)
        self.raw_pose = None    # this frame's undebounced pose (the published pose lags a few frames)
        self.hist: deque[tuple[int, float, float, int, float]] = deque()   # (ts, cx, cy, swipe class, scale)
        self.swipe_cls = 0      # this frame: 5 = open-hand swipe shape, 2 = two-finger, 0 = neither
        self.swipe_settle = False   # after a swipe: wait for the hand to slow down
        self.swipe_cut = -1         # swipe detector ignores history up to this timestamp
        self.reset()

    def reset(self) -> None:
        """Clear gesture state (not the movement history)."""
        self.state = IDLE
        # fist / level
        self.fist_start: Optional[int] = None
        self.fist_x = self.fist_y = 0.0
        self.fist_travel = 0.0
        self.fist_end: Optional[int] = None
        self.lvl_kind = ""
        self.lvl_y0 = 0.0
        self.lvl_tick = 0
        self.lvl_open_since: Optional[int] = None
        self.knob_filter = None
        self.knob_last = 0.0
        self.knob_unwrapped = 0.0
        self.knob_a0 = 0.0
        self.knob_sent = 0.0
        self.knob_twist = 0.0
        # pointer
        self.pt_frames = 0
        self.pt_last_point: Optional[int] = None
        self.pt_x = self.pt_y = 0.0
        self.pt_frozen = False
        self.pt_unfreeze_at: Optional[int] = None
        self.pt_filter = None
        # pinch
        self.pinch_start: Optional[int] = None
        self.pinch_pos: Optional[tuple[float, float]] = None
        self.pinch_from_pointer = False
        # pen
        self.pen_filter = None
        self.pen_buf: list[tuple[float, float, int]] = []
        self.stroke_id: Optional[int] = None
        # palm hold
        self.palm_start: Optional[int] = None
        # custom hold
        self.cu_start: Optional[int] = None
        self.cu_votes: list[Optional[str]] = []

    # flags that must survive reset() (require a pose change before firing again)
    palm_fired = False
    custom_fired = False

    def push_hist(self, keep_ms: int) -> None:
        self.hist.append((self.ts, self.cx, self.cy, self.swipe_cls, self.scale))
        while self.hist and self.ts - self.hist[0][0] > keep_ms:
            self.hist.popleft()

    def compute_speed(self, window_ms: int = 100) -> float:
        """Palm speed in scales per second over about window_ms."""
        if len(self.hist) < 2:
            return 0.0
        last = self.hist[-1]
        first = last
        for e in reversed(self.hist):
            first = e
            if last[0] - e[0] >= window_ms:
                break
        dt = (last[0] - first[0]) / 1000.0
        if dt <= 0:
            return 0.0
        dx = (last[1] - first[1]) * contracts.ASPECT
        dy = last[2] - first[2]
        return float((dx * dx + dy * dy) ** 0.5) / max(self.scale, 1e-4) / dt


def box_map(x: float, y: float, box) -> tuple[float, float]:
    """Map an image point through the active box [x0, y0, x1, y1] to 0..1 screen space, clamped."""
    x0, y0, x1, y1 = (float(v) for v in box)
    u = (x - x0) / max(x1 - x0, 1e-6)
    v = (y - y0) / max(y1 - y0, 1e-6)
    return min(max(u, 0.0), 1.0), min(max(v, 0.0), 1.0)
