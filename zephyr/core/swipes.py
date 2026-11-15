"""zephyr/core/swipes.py    Five and two finger swipes. Owner: Aadharsh.

Fires when all hold over one movement (inside swipe.max_ms):
  pose was OPEN (5 fingers) or TWO (2 fingers) for the whole movement
  horizontal travel >= min_travel_scales
  peak speed >= min_peak_speed scales/s
  abs(dy) < max_slope * abs(dx)
  the hand was nearly still (rest_speed) just before the movement started
Then refractory_ms for every swipe, and the opposite direction is blocked for opposite_block_ms.
Direction is the direction the hand moved in the mirrored frame (what the user sees). The
swipe.natural mapping to next/previous is applied by the actions router, not here.
"""
from __future__ import annotations

from typing import Optional

from zephyr import contracts
from zephyr.config import cfg
from zephyr.core.handctx import HandCtx

FINGERS = {"open": 5, "two": 2}


class SwipeDetector:
    def __init__(self) -> None:
        self.last_fire_ms = -10**9
        self.last_dir: Optional[str] = None

    def check(self, ctx: HandCtx) -> Optional[tuple[str, int]]:
        """Returns (direction, fingers) when a swipe fires on this frame, else None."""
        s = cfg.swipe
        h = list(ctx.hist)
        if len(h) < 3:
            return None
        now = h[-1][0]
        pose = h[-1][3]
        if pose not in FINGERS:
            return None
        since = now - self.last_fire_ms
        if since < int(s.refractory_ms):
            return None
        aspect = contracts.ASPECT
        scale = max(ctx.scale, 1e-4)

        def step_speed(a, b) -> float:
            dt = (b[0] - a[0]) / 1000.0
            if dt <= 0:
                return 0.0
            dx = (b[1] - a[1]) * aspect
            dy = b[2] - a[2]
            return (dx * dx + dy * dy) ** 0.5 / scale / dt

        # walk back while the pose stays the same and we are inside max_ms
        start = len(h) - 1
        while start > 0 and h[start - 1][3] == pose and now - h[start - 1][0] <= int(s.max_ms):
            start -= 1
        # try each candidate start, oldest first; it must be preceded by a resting frame
        for i in range(max(start, 1), len(h) - 1):
            if step_speed(h[i - 1], h[i]) >= float(s.rest_speed):
                continue
            dx = (h[-1][1] - h[i][1]) * aspect / scale
            dy = (h[-1][2] - h[i][2]) / scale
            if abs(dx) < float(s.min_travel_scales):
                continue
            if abs(dy) >= float(s.max_slope) * abs(dx):
                continue
            peak = max(step_speed(h[j - 1], h[j]) for j in range(i + 1, len(h)))
            if peak < float(s.min_peak_speed):
                continue
            direction = "left" if dx < 0 else "right"
            if (self.last_dir and direction != self.last_dir
                    and since < int(s.opposite_block_ms)):
                return None
            self.last_fire_ms = now
            self.last_dir = direction
            ctx.hist.clear()           # this movement is used up
            return direction, FINGERS[pose]
        return None
