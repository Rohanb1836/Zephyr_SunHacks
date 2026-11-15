"""zephyr/core/swipes.py    Five and two finger swipes. Owner: Aadharsh.

Built around how a real swipe looks to the camera: the hand flies sideways, the picture blurs, and
finger landmarks go bad for a few frames. So the detector follows the PALM, not the pose:

  1. Look back over the last swipe.max_ms and take the sample that is furthest away sideways.
     The swipe is the movement from there to now. No "start from rest" is needed, so a hand that
     enters the picture already moving still counts.
  2. It fires when that movement is at least min_travel_scales wide, mostly sideways
     (|dy| < max_slope * |dx|), reached min_peak_speed, and was already under way one frame ago
     (so one bad landmark frame cannot fake a swipe).
  3. Which swipe it is comes from a vote over the frames of the movement, using only the index,
     middle and ring fingers (the pinky and thumb are ignored because they are the least reliable):
        index + middle + ring extended   -> five-finger swipe
        index + middle extended, ring curled -> two-finger swipe
     At least swipe.open_ratio of the frames must agree. Blurred frames simply do not vote.
  4. After a swipe the hand must slow down below settle_speed before it can swipe again, every swipe
     is blocked for refractory_ms, and the opposite direction for opposite_block_ms, so the return
     movement is swallowed.

Direction is the direction the hand moved in the mirrored frame (what the user sees). The
swipe.natural mapping to next/previous is applied by the actions router, not here.
"""
from __future__ import annotations

from typing import Optional

from zephyr import contracts
from zephyr.config import cfg
from zephyr.core.handctx import HandCtx


def finger_class(fingers) -> int:
    """5 = open-hand swipe, 2 = two-finger swipe, 0 = neither. fingers = (index, middle, ring, pinky)."""
    i, m, r = bool(fingers[0]), bool(fingers[1]), bool(fingers[2])
    if i and m and r:
        return 5
    if i and m and not r:
        return 2
    return 0


class SwipeDetector:
    def __init__(self) -> None:
        self.last_fire_ms = -10**9
        self.last_dir: Optional[str] = None

    def check(self, ctx: HandCtx) -> Optional[tuple[str, int]]:
        """Returns (direction, fingers) when a swipe fires on this frame, else None."""
        s = cfg.swipe
        if ctx.swipe_settle:
            if ctx.speed < float(s.get("settle_speed", 2.0)):
                ctx.swipe_settle = False
                ctx.swipe_cut = ctx.ts     # the rest of that movement is used up too
            return None
        h = ctx.hist
        if len(h) < 3:
            return None
        now = h[-1][0]
        since = now - self.last_fire_ms
        if since < int(s.refractory_ms):
            return None
        aspect = contracts.ASPECT
        scale = max(ctx.scale, 1e-4)
        max_ms = int(s.max_ms)

        # 1. the furthest sideways sample inside the window
        # Two-finger swipes have their own, lower thresholds (swipe.two_*): people make them as a
        # gentler movement than an open-hand swipe, and the two-finger pose rarely happens by accident.
        need5 = float(s.min_travel_scales)
        need2 = float(s.get("two_min_travel_scales", need5))
        need = min(need5, need2)
        k = aspect / scale
        best_i, best_dx = -1, 0.0
        for i in range(len(h) - 2, -1, -1):
            if now - h[i][0] > max_ms or h[i][0] <= ctx.swipe_cut:
                break
            if 0 < i:                       # skip a one-frame spike: far from both neighbours, which agree
                a, b = (h[i][1] - h[i - 1][1]) * k, (h[i][1] - h[i + 1][1]) * k
                if a * b > 0 and min(abs(a), abs(b)) > 0.6 * need and abs(a - b) < 0.3 * need:
                    continue
            dx = (h[-1][1] - h[i][1]) * k
            if abs(dx) > abs(best_dx):
                best_i, best_dx = i, dx
        if best_i < 0 or abs(best_dx) < need:
            return None

        # 2. shape of the movement
        dy = (h[-1][2] - h[best_i][2]) / scale
        if abs(dy) >= float(s.max_slope) * abs(best_dx):
            return None
        # Bridged swipe: a fast swipe blurs, and the hand tracker loses the hand for the whole stroke.
        # Measured on a laptop webcam in room light: the hand vanishes for 160 to 450 ms and comes back
        # 1.5 to 2.7 hand widths to the side, then does the same in reverse for the return stroke.
        # A gap like that, with the hand reappearing level and far enough sideways, IS the swipe. The
        # return jump that follows is swallowed by opposite_block_ms like any return movement.
        gap = h[-1][0] - h[-2][0]
        jump = (h[-1][1] - h[-2][1]) * k
        bridged = (int(s.get("bridge_min_ms", 120)) <= gap <= int(s.get("bridge_max_ms", 450))
                   and jump * best_dx > 0 and abs(jump) >= 0.6 * need)
        prev_dx = (h[-2][1] - h[best_i][1]) * aspect / scale
        if not bridged and (prev_dx * best_dx <= 0 or abs(prev_dx) < 0.4 * need):
            return None                    # the whole distance appeared in one frame: a glitch
        sign = 1.0 if best_dx > 0 else -1.0
        peak = 0.0
        for j in range(best_i + 1, len(h)):
            dt = (h[j][0] - h[j - 1][0]) / 1000.0
            if dt > 0:
                peak = max(peak, sign * (h[j][1] - h[j - 1][1]) * aspect / scale / dt)
        peak5 = float(s.min_peak_speed)
        peak2 = float(s.get("two_min_peak_speed", peak5))
        if peak < min(peak5, peak2):
            return None
        settle5 = float(s.get("settle_speed", 2.0))
        settle2 = float(s.get("two_settle_speed", settle5))
        if ctx.speed < min(settle5, settle2):
            return None                    # the hand has already stopped: an old movement never fires late

        # 3. vote on the hand shape over the movement
        span = len(h) - best_i
        n5 = sum(1 for j in range(best_i, len(h)) if h[j][3] == 5)
        n2 = sum(1 for j in range(best_i, len(h)) if h[j][3] == 2)
        if max(n5, n2) / span < float(s.get("open_ratio", 0.5)):
            return None
        fingers = 5 if n5 >= n2 else 2
        if fingers == 5 and (abs(best_dx) < need5 or peak < peak5 or ctx.speed < settle5):
            return None                    # the lower thresholds are for two-finger swipes only
        if fingers == 2 and (abs(best_dx) < need2 or peak < peak2 or ctx.speed < settle2):
            return None
        if bridged and h[-1][3] != fingers:
            return None                    # after a blur gap the hand must come back in the same shape
                                           # (one that came back as a fist was a hand closing, not a swipe)
        direction = "right" if best_dx > 0 else "left"

        # 4. one movement, one swipe
        ctx.swipe_cut = now                # samples up to here are used up (history stays for speed)
        ctx.swipe_settle = True
        if self.last_dir and direction != self.last_dir and since < int(s.opposite_block_ms):
            return None                    # the return movement: swallowed
        self.last_fire_ms = now
        self.last_dir = direction
        return direction, fingers
