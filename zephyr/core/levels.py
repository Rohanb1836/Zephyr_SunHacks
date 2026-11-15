"""zephyr/core/levels.py    Fist: tap (play/pause) or level (brightness/volume). Owner: Aadharsh.

FIST from idle enters FIST_PENDING. Then exactly one of:
  fist opens to OPEN after fist_tap_min_ms..fist_tap_max_ms with little travel -> FIST_TAP
  fist held level.engage_ms                                                 -> LEVEL
In LEVEL: offset = (y0 - y) / scale (up is positive). Inside the dead zone nothing is published.
Outside it, rate = clamp((|offset| - dead_zone) * gain, min_rate, max_rate) %/s, and every tick_ms
LevelEvent(kind, rate * tick_s * sign, active=True). Left hand brightness, right hand volume.
When the fist has been open for level.release_ms: LevelEvent(active=False).
"""
from __future__ import annotations

from zephyr import contracts
from zephyr.config import cfg
from zephyr.contracts import GestureKind, HoldProgress, LevelEvent, Pose, Side, T
from zephyr.core import holds
from zephyr.core.handctx import FIST_PENDING, IDLE, LEVEL, HandCtx

RELEASE_WAIT_MS = 250   # after a fist opens, how long transitional poses may last before OPEN


def start(eng, ctx: HandCtx) -> None:
    ctx.state = FIST_PENDING
    ctx.fist_start = ctx.ts
    ctx.fist_x, ctx.fist_y = ctx.cx, ctx.cy
    ctx.fist_travel = 0.0
    ctx.fist_end = None


def step(eng, ctx: HandCtx) -> bool:
    """Runs FIST_PENDING and LEVEL. Returns True when the frame was consumed."""
    if ctx.state == FIST_PENDING:
        return _pending(eng, ctx)
    if ctx.state == LEVEL:
        return _level(eng, ctx)
    return False


def _pending(eng, ctx: HandCtx) -> bool:
    lv, hd = cfg.level, cfg.holds
    pose = ctx.hand.pose
    travel = (((ctx.cx - ctx.fist_x) * contracts.ASPECT) ** 2 + (ctx.cy - ctx.fist_y) ** 2) ** 0.5 / max(ctx.scale, 1e-4)
    ctx.fist_travel = max(ctx.fist_travel, travel)
    if pose is Pose.FIST:
        ctx.fist_end = None
        held = ctx.ts - ctx.fist_start
        eng.pub(T.HOLD, HoldProgress("level", min(1.0, held / max(int(lv.engage_ms), 1)), ctx.hand_id))
        if held >= int(lv.engage_ms):
            ctx.state = LEVEL
            ctx.lvl_kind = "brightness" if ctx.hand.side is Side.LEFT else "volume"
            ctx.lvl_y0 = ctx.cy
            ctx.lvl_tick = ctx.ts
            ctx.lvl_open_since = None
        return True
    # the fist has opened (or is opening)
    if ctx.fist_end is None:
        ctx.fist_end = ctx.ts
    if pose is Pose.OPEN:
        dur = ctx.fist_end - ctx.fist_start
        if holds.fist_tap_ok(dur, ctx.fist_travel):
            eng.gesture(GestureKind.FIST_TAP, ctx)
        _end(eng, ctx)
        return True
    if pose in (Pose.POINT, Pose.TWO) or ctx.ts - ctx.fist_end > RELEASE_WAIT_MS or \
            ctx.fist_end - ctx.fist_start > int(hd.fist_tap_max_ms):
        _end(eng, ctx)
        return False        # let the new pose be handled this frame
    return True


def _level(eng, ctx: HandCtx) -> bool:
    lv = cfg.level
    if ctx.hand.pose is not Pose.FIST:
        if ctx.lvl_open_since is None:
            ctx.lvl_open_since = ctx.ts
        if ctx.ts - ctx.lvl_open_since >= int(lv.get("release_ms", 150)):
            cancel(eng, ctx)
            return False
        return True
    ctx.lvl_open_since = None
    offset = (ctx.lvl_y0 - ctx.cy) / max(ctx.scale, 1e-4)
    dz = float(lv.dead_zone_scales)
    if abs(offset) <= dz:
        ctx.lvl_tick = ctx.ts
        return True
    tick = int(lv.tick_ms)
    if ctx.ts - ctx.lvl_tick >= tick:
        rate = min(max((abs(offset) - dz) * float(lv.gain), float(lv.min_rate)), float(lv.max_rate))
        sign = 1.0 if offset > 0 else -1.0
        eng.pub(T.LEVEL, LevelEvent(ctx.lvl_kind, rate * tick / 1000.0 * sign, True))
        ctx.lvl_tick += tick
        if ctx.ts - ctx.lvl_tick >= tick:      # fell behind (slow frame): do not burst
            ctx.lvl_tick = ctx.ts
    return True


def _end(eng, ctx: HandCtx) -> None:
    eng.pub(T.HOLD, HoldProgress("level", 0.0, ctx.hand_id))
    ctx.state = IDLE
    ctx.fist_start = None
    ctx.fist_end = None


def cancel(eng, ctx: HandCtx) -> None:
    if ctx.state == LEVEL:
        eng.pub(T.LEVEL, LevelEvent(ctx.lvl_kind, 0.0, False))
    _end(eng, ctx)
