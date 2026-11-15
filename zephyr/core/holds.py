"""zephyr/core/holds.py    Palm hold, both palms, fist tap rule, custom gesture hold. Owner: Aadharsh.

Palm hold: exactly one allowed hand OPEN, palm facing the camera, speed under palm_max_speed,
           for palm_ms. Progress every frame. After firing, the pose must change before it fires again.
Both palms: two OPEN hands, palms facing, for both_palms_ms. While two open hands are visible the
           single palm hold is cancelled. After firing, both palms must drop before it can fire again.
Fist tap:  a fist lasting fist_tap_min_ms..fist_tap_max_ms, ending in OPEN, moving under fist_tap_max_travel.
Custom:    pose UNKNOWN and the matcher returns the same id for custom_ms with custom_agree of the
           frames agreeing -> GestureEvent(CUSTOM, name=id). Pose must change before it fires again.
"""
from __future__ import annotations

from collections import Counter
from typing import Optional

from zephyr.config import cfg
from zephyr.contracts import GestureKind, HoldProgress, Pose, Side, T
from zephyr.core import features as F
from zephyr.core.handctx import HOLD_CUSTOM, HOLD_PALM, IDLE, HandCtx


def fist_tap_ok(dur_ms: int, travel_scales: float) -> bool:
    h = cfg.holds
    return int(h.fist_tap_min_ms) <= dur_ms <= int(h.fist_tap_max_ms) and travel_scales < float(h.fist_tap_max_travel)


# ---------------- both palms ----------------
class BothPalms:
    """Two open, facing hands held still for both_palms_ms. Global (not per hand)."""

    def __init__(self) -> None:
        self.start: Optional[int] = None
        self.need_release = False
        self.active = False         # two open facing hands are up (allowing for short dropouts)
        self.last_ok = -10**9

    def step(self, eng, ctxs: list[HandCtx], ts: int) -> bool:
        """ctxs: candidate hands (same role). Returns True on the frame it fires."""
        # Palm facing is also judged by position (the hand on the left of the picture is the left
        # hand), because with two hands up MediaPipe sometimes gives both the same left/right label.
        opens = sorted((c for c in ctxs if c.hand.pose is Pose.OPEN), key=lambda c: -c.scale)[:2]
        opens.sort(key=lambda c: c.cx)
        sign = float(cfg.poses.get("palm_sign", 1))
        open_facing = [c for c, side in zip(opens, (Side.LEFT, Side.RIGHT))
                       if len(opens) == 2 and (c.facing or F.palm_facing(c.hand.landmarks, side, sign))]
        for c in opens if len(opens) == 2 else ():
            c.palm_fired = True     # part of a two-palm pose: no single palm hold until the pose changes
        if len(open_facing) >= 2:
            self.last_ok = ts
        elif ts - self.last_ok <= int(cfg.holds.get("grace_ms", 150)) and (self.start is not None or self.need_release):
            return False            # one hand flickered for a frame or two: keep the hold going
        self.active = len(open_facing) >= 2
        if not self.active:
            if self.start is not None:
                eng.pub(T.HOLD, HoldProgress("arm_toggle", 0.0))
            self.start = None
            self.need_release = False
            return False
        if self.need_release:
            return False
        if self.start is None:
            self.start = ts
        p = (ts - self.start) / max(int(cfg.holds.both_palms_ms), 1)
        eng.pub(T.HOLD, HoldProgress("arm_toggle", min(1.0, p)))
        if p >= 1.0:
            eng.pub(T.HOLD, HoldProgress("arm_toggle", 0.0))
            self.start = None
            self.need_release = True
            return True
        return False

    def cancel(self, eng) -> None:
        if self.start is not None:
            eng.pub(T.HOLD, HoldProgress("arm_toggle", 0.0))
        self.start = None


# ---------------- palm hold ----------------
def palm_can_start(ctx: HandCtx, n_open_allowed: int, both_active: bool) -> bool:
    if ctx.palm_fired or ctx.ts < ctx.palm_block_until:
        return False
    return (ctx.hand.pose is Pose.OPEN and ctx.facing and n_open_allowed == 1 and not both_active
            and ctx.hand.pinch > float(cfg.pinch.up)      # a pinching hand is clicking or writing, not a palm
            and ctx.still_speed < float(cfg.holds.palm_max_speed))


def palm_start(eng, ctx: HandCtx) -> None:
    ctx.state = HOLD_PALM
    ctx.palm_start = ctx.ts
    ctx.palm_bad_since = None
    eng.pub(T.HOLD, HoldProgress("palm_hold", 0.0, ctx.hand_id))


def palm_step(eng, ctx: HandCtx, n_open_allowed: int, both_active: bool) -> bool:
    """Returns True when the frame was consumed; False when the hold broke (fall through to idle)."""
    pose = ctx.hand.pose
    if ctx.hand.pinch < float(cfg.pinch.down):
        palm_cancel(eng, ctx)           # the thumb and index closed: this is a pinch, hand it over now
        return False
    ok = (pose is Pose.OPEN and ctx.facing and n_open_allowed == 1 and not both_active
          and ctx.still_speed < float(cfg.holds.palm_max_speed))
    if not ok:
        # a clear new pose or a second palm ends the hold at once; a shaky frame gets a short grace
        if pose in (Pose.FIST, Pose.POINT, Pose.TWO) or both_active:
            palm_cancel(eng, ctx)
            return False
        if ctx.palm_bad_since is None:
            ctx.palm_bad_since = ctx.ts
        if ctx.ts - ctx.palm_bad_since > int(cfg.holds.get("grace_ms", 150)):
            palm_cancel(eng, ctx)
            return False
        return True
    ctx.palm_bad_since = None
    p = (ctx.ts - ctx.palm_start) / max(int(cfg.holds.palm_ms), 1)
    eng.pub(T.HOLD, HoldProgress("palm_hold", min(1.0, p), ctx.hand_id))
    if p >= 1.0:
        eng.gesture(GestureKind.PALM_HOLD, ctx)
        eng.pub(T.HOLD, HoldProgress("palm_hold", 0.0, ctx.hand_id))
        ctx.palm_fired = True
        ctx.state = IDLE
        ctx.palm_start = None
    return True


def palm_cancel(eng, ctx: HandCtx) -> None:
    eng.pub(T.HOLD, HoldProgress("palm_hold", 0.0, ctx.hand_id))
    ctx.state = IDLE
    ctx.palm_start = None


# ---------------- custom hold ----------------
def custom_match(eng, ctx: HandCtx) -> Optional[str]:
    try:
        res = eng.matcher.match(ctx.hand)
    except Exception as exc:
        eng.warn_once("matcher", f"[engine] matcher.match error: {exc!r}")
        return None
    return res[0] if res else None


def custom_try_start(eng, ctx: HandCtx) -> bool:
    if ctx.custom_fired or ctx.hand.pose is not Pose.UNKNOWN:
        return False
    gid = custom_match(eng, ctx)
    if gid is None:
        return False
    ctx.state = HOLD_CUSTOM
    ctx.cu_start = ctx.ts
    ctx.cu_votes = [gid]
    eng.pub(T.HOLD, HoldProgress("custom", 0.0, ctx.hand_id))
    return True


def custom_step(eng, ctx: HandCtx) -> bool:
    if ctx.hand.pose is not Pose.UNKNOWN:
        custom_cancel(eng, ctx)
        return False
    ctx.cu_votes.append(custom_match(eng, ctx))
    h = cfg.holds
    p = (ctx.ts - ctx.cu_start) / max(int(h.custom_ms), 1)
    eng.pub(T.HOLD, HoldProgress("custom", min(1.0, p), ctx.hand_id))
    if p >= 1.0:
        votes = ctx.cu_votes
        best, n = Counter(v for v in votes if v is not None).most_common(1)[0] if any(votes) else (None, 0)
        if best is not None and n / len(votes) >= float(h.get("custom_agree", 0.8)):
            eng.gesture(GestureKind.CUSTOM, ctx, name=best)
            ctx.custom_fired = True
        custom_cancel(eng, ctx)
    return True


def custom_cancel(eng, ctx: HandCtx) -> None:
    eng.pub(T.HOLD, HoldProgress("custom", 0.0, ctx.hand_id))
    ctx.state = IDLE
    ctx.cu_start = None
    ctx.cu_votes = []
