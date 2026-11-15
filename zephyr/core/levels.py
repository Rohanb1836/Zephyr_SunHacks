"""zephyr/core/levels.py    Fist: tap (play/pause) or level (brightness/volume). Owner: Aadharsh.

FIST from idle enters FIST_PENDING. Then exactly one of:
  fist opens to OPEN after fist_tap_min_ms..fist_tap_max_ms with little travel -> FIST_TAP
  fist held level.engage_ms                                                 -> LEVEL
In LEVEL (level.mode: knob, the default): the fist is a dial. Twist it like turning a knob:
  turning right (clockwise as you see it) raises the level, turning left lowers it.
  The twist is the angle of the line from the wrist to the middle of the knuckles, measured from the
  angle when the knob engaged. Inside level.knob_dead_deg nothing changes. Past it, the target change
  is (|twist| - dead) * knob_gain percent, with the sign of the twist. Every tick_ms the engine sends
  LevelEvent(kind, target - already_sent, active=True) when that difference is at least knob_min_step.
  Like a real knob, turning back undoes the change. To go further, open the fist and grab again.
level.mode: vertical keeps the old behaviour (move the fist up or down for a rate).
Left hand brightness, right hand volume (by detected side, so hands.swap_sides is respected).
When the fist has been open for level.release_ms: LevelEvent(active=False).
"""
from __future__ import annotations

import math

from zephyr import contracts
from zephyr.config import cfg
from zephyr.contracts import GestureKind, HoldProgress, LevelEvent, Pose, Side, T
from zephyr.core import holds
from zephyr.core.filters import OneEuro
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
            side = ctx.body_side or ctx.hand.side     # a fist shows its back: the label is unreliable
            ctx.lvl_kind = "brightness" if side is Side.LEFT else "volume"
            ctx.lvl_y0 = ctx.cy
            ctx.lvl_tick = ctx.ts
            ctx.lvl_open_since = None
            ctx.knob_filter = OneEuro(float(lv.get("knob_min_cutoff", 1.0)), float(lv.get("knob_beta", 0.05)))
            ctx.knob_last = knob_angle(ctx)
            ctx.knob_unwrapped = ctx.knob_last
            ctx.knob_a0 = ctx.knob_filter(ctx.knob_unwrapped, ctx.ts / 1000.0)
            ctx.knob_sent = 0.0
            ctx.lvl_moved = False
        return True
    # the fist has opened (or is opening)
    if ctx.fist_end is None:
        ctx.fist_end = ctx.ts
    if _opened(ctx) or (ctx.ts - ctx.fist_end > RELEASE_WAIT_MS and _let_go(ctx)):
        dur = ctx.fist_end - ctx.fist_start
        if holds.fist_tap_ok(dur, ctx.fist_travel) and ctx.speed < float(cfg.swipe.get("busy_speed", 3.0)):
            eng.gesture(GestureKind.FIST_TAP, ctx)
        _end(eng, ctx)
        return True
    if pose in (Pose.POINT, Pose.TWO) or ctx.ts - ctx.fist_end > RELEASE_WAIT_MS or \
            ctx.fist_end - ctx.fist_start > int(hd.fist_tap_max_ms):
        _end(eng, ctx)
        return False        # let the new pose be handled this frame
    return True


def _opened(ctx: HandCtx) -> bool:
    """The fist has opened: an open hand, or index, middle and ring straight (a relaxed open hand
    often has a bent pinky or thumb and reads UNKNOWN, which used to swallow the tap)."""
    return ctx.hand.pose is Pose.OPEN or (ctx.fingers is not None and all(ctx.fingers[:3]))


def _let_go(ctx: HandCtx) -> bool:
    """The fist relaxed into some open hand, not into another gesture. Measured: fists opened into
    hands that read index and middle bent, ring and pinky straight (fingers toward the camera), never
    a clean OPEN. Pointing, two fingers or a pinch mean the user is starting something else."""
    return (ctx.hand.pose not in (Pose.POINT, Pose.TWO, Pose.FIST)
            and ctx.hand.pinch > float(cfg.pinch.up))


def core_fist(ctx: HandCtx) -> bool:
    """Index, middle and ring are curled this frame. The pinky and thumb are ignored: they pop out
    while a real fist twists, and that must not drop the knob."""
    return ctx.fingers is not None and not any(ctx.fingers[:3])


def _level(eng, ctx: HandCtx) -> bool:
    lv = cfg.level
    pose = ctx.hand.pose
    if not (pose is Pose.FIST or (core_fist(ctx) and pose is not Pose.OPEN)):
        if ctx.lvl_open_since is None:
            ctx.lvl_open_since = ctx.ts
        # a clear open hand lets go at once; an unclear frame gets release_ms before the knob drops
        if _opened(ctx) or ctx.ts - ctx.lvl_open_since >= int(lv.get("release_ms", 250)):
            # A fist that grabbed the knob but never turned it is a tap. Measured: people hold a
            # "quick" fist for 1.2 to 2.1 s, far past the 400 ms it takes to grab the knob, so
            # without this every tap became an empty knob grab and play/pause never fired.
            held = (ctx.lvl_open_since or ctx.ts) - ctx.fist_start
            # ...but not when the hand flies off as it opens: a fist resting between swipes that
            # opens straight into the next swipe is not play/pause (measured in a swipe session).
            if ((_opened(ctx) or _let_go(ctx)) and not ctx.lvl_moved
                    and ctx.speed < float(cfg.swipe.get("busy_speed", 3.0))
                    and ctx.fist_travel < float(cfg.holds.fist_tap_max_travel)
                    and held <= int(cfg.holds.get("fist_tap_slow_max_ms", 2500))):
                eng.gesture(GestureKind.FIST_TAP, ctx)
            cancel(eng, ctx)
            return False
        return True
    ctx.lvl_open_since = None
    if str(lv.get("mode", "knob")) == "knob":
        _knob(eng, ctx)
        return True
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
        ctx.lvl_moved = True
        ctx.lvl_tick += tick
        if ctx.ts - ctx.lvl_tick >= tick:      # fell behind (slow frame): do not burst
            ctx.lvl_tick = ctx.ts
    return True


def knob_angle(ctx: HandCtx) -> float:
    """Hand twist in degrees: 0 when the knuckles are straight above the wrist, positive when the
    top of the fist leans to the right of the (mirrored) image, which is clockwise as the user sees it."""
    lm = ctx.hand.landmarks
    kx = (float(lm[5, 0]) + float(lm[9, 0]) + float(lm[13, 0]) + float(lm[17, 0])) / 4
    ky = (float(lm[5, 1]) + float(lm[9, 1]) + float(lm[13, 1]) + float(lm[17, 1])) / 4
    dx = (kx - float(lm[0, 0])) * contracts.ASPECT
    dy = float(lm[0, 1]) - ky
    return math.degrees(math.atan2(dx, dy))


def knob_twist(ctx: HandCtx) -> float:
    """Filtered twist since the knob engaged, in degrees (unwrapped across +-180)."""
    a = knob_angle(ctx)
    d = (a - ctx.knob_last + 180.0) % 360.0 - 180.0
    ctx.knob_last = a
    lv = cfg.level
    # A wrist cannot turn this far in one frame: it is a landmark glitch on the fist (measured: the
    # angle jumped 50 degrees in 0.2 s while the hand rested, and set the volume 31 points lower).
    if abs(d) <= float(lv.get("knob_max_step_deg", 60)):
        ctx.knob_unwrapped += d
    ctx.knob_filter.min_cutoff = float(lv.get("knob_min_cutoff", 1.0))
    ctx.knob_filter.beta = float(lv.get("knob_beta", 0.05))
    twist = ctx.knob_filter(ctx.knob_unwrapped, ctx.ts / 1000.0) - ctx.knob_a0
    return -twist if lv.get("knob_invert", False) else twist


def _knob(eng, ctx: HandCtx) -> None:
    lv = cfg.level
    if not core_fist(ctx):
        return          # the fist is opening: hold the level, do not read the moving hand
    twist = knob_twist(ctx)
    ctx.knob_twist = twist
    dead = float(lv.get("knob_dead_deg", 8))
    target = math.copysign(max(0.0, abs(twist) - dead) * float(lv.get("knob_gain", 1.0)), twist)
    tick = int(lv.tick_ms)
    if ctx.ts - ctx.lvl_tick < tick:
        return
    ctx.lvl_tick = ctx.ts
    delta = target - ctx.knob_sent
    if abs(delta) >= float(lv.get("knob_min_step", 0.5)):
        eng.pub(T.LEVEL, LevelEvent(ctx.lvl_kind, delta, True))
        ctx.knob_sent = target
        ctx.lvl_moved = True


def _end(eng, ctx: HandCtx) -> None:
    eng.pub(T.HOLD, HoldProgress("level", 0.0, ctx.hand_id))
    ctx.state = IDLE
    ctx.fist_start = None
    ctx.fist_end = None


def cancel(eng, ctx: HandCtx) -> None:
    if ctx.state == LEVEL:
        eng.pub(T.LEVEL, LevelEvent(ctx.lvl_kind, 0.0, False))
    _end(eng, ctx)
