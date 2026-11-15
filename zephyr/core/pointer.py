"""zephyr/core/pointer.py    Finger pointer, freeze and pinch. Owner: Aadharsh.

Pointer: POINT for pointer.show_frames frames shows it. The index knuckle (landmark 5) is mapped
through pointer.box to 0..1 screen space, clamped, then One Euro filtered. While pinch is below
pinch.prefreeze the output freezes (frozen=True); it unfreezes post_freeze_ms after the pinch opens.
When POINT is lost for hide_after_ms (or the hand clearly switches to OPEN, TWO or FIST),
one PointerEvent(visible=False) is published.

Pinch: down below pinch.down, up above pinch.up (hysteresis).
  released within tap_max_ms  -> GestureEvent(PINCH_TAP, pos=frozen pointer position, or None
                                 when no pointer was showing, so the router can skip the click)
  held longer and ink enabled -> PEN (strokes.py), with the points buffered since pinch down
  held longer, no ink         -> nothing
"""
from __future__ import annotations

from zephyr.bus import bus
from zephyr.config import cfg
from zephyr.contracts import GestureKind, PointerEvent, Pose, T
from zephyr.core import strokes
from zephyr.core.filters import OneEuro2D
from zephyr.core.handctx import IDLE, PINCHING, POINTER, HandCtx, box_map

# A clear switch hides the pointer at once. FIST is not in this list on purpose: the index finger
# dipping toward the thumb for a click often reads as a fist for a few frames, so a fist only takes
# over after pointer.hide_after_ms, like any other unclear frame.
SWITCH_POSES = (Pose.OPEN, Pose.TWO)


def _raw_pos(ctx: HandCtx) -> tuple[float, float]:
    lm = ctx.hand.landmarks
    return box_map(float(lm[5, 0]), float(lm[5, 1]), cfg.pointer.box)


def _filtered(ctx: HandCtx) -> tuple[float, float]:
    p = cfg.pointer
    if ctx.pt_filter is None:
        ctx.pt_filter = OneEuro2D(float(p.min_cutoff), float(p.beta))
    ctx.pt_filter.tune(float(p.min_cutoff), float(p.beta))
    x, y = _raw_pos(ctx)
    return ctx.pt_filter(x, y, ctx.ts / 1000.0)


def _publish(eng, ctx: HandCtx) -> None:
    eng.pub(T.POINTER, PointerEvent(True, ctx.pt_x, ctx.pt_y, ctx.pt_frozen, ctx.ts))


def hide(eng, ctx: HandCtx) -> None:
    eng.pub(T.POINTER, PointerEvent(False, ctx.pt_x, ctx.pt_y, False, ctx.ts))
    ctx.pt_frames = 0
    ctx.pt_frozen = False
    ctx.pt_unfreeze_at = None
    ctx.pt_last_point = None


def pinch_is_down(ctx: HandCtx, eng=None) -> bool:
    """A new pinch: thumb and index closed, not a fist, and clearly apart for pinch.open_before_ms
    just before. Measured: a hand resting or holding a cup flickers across the pinch thresholds and
    gave 64 taps in a minute of normal movement; a real click (point, then pinch) always starts open.
    While writing, the gap between two strokes of a letter is short, so the pen uses
    pinch.pen_open_before_ms instead."""
    if ctx.hand.pinch >= float(cfg.pinch.down) or ctx.hand.pose is Pose.FIST:
        return False
    writing = eng is not None and eng.ink_enabled()
    need = int(cfg.pinch.get("pen_open_before_ms", 60) if writing else cfg.pinch.get("open_before_ms", 150))
    return ctx.open_span >= need


def _preview_showing() -> bool:
    w = bus.latest(T.WRITE)
    return w is not None and getattr(w, "phase", "") == "preview"


def idle_point(eng, ctx: HandCtx) -> bool:
    """Called in IDLE. Counts POINT frames; shows the pointer after show_frames. True if it took over."""
    if ctx.hand.pose is not Pose.POINT:
        ctx.pt_frames = 0
        return False
    ctx.pt_frames += 1
    if ctx.pt_frames < int(cfg.pointer.get("show_frames", 3)):
        return False
    ctx.state = POINTER
    if ctx.pt_filter is not None:
        ctx.pt_filter.reset()              # reset when the pointer reappears
    ctx.pt_x, ctx.pt_y = _filtered(ctx)
    ctx.pt_frozen = False
    ctx.pt_last_point = ctx.ts
    _publish(eng, ctx)
    return True


def pointer_step(eng, ctx: HandCtx) -> bool:
    """POINTER state. Returns True when the frame was consumed."""
    if pinch_is_down(ctx, eng):
        start_pinch(eng, ctx, from_pointer=True)
        return True
    pose = ctx.hand.pose
    if pose in SWITCH_POSES:
        hide(eng, ctx)
        ctx.state = IDLE
        return False
    if pose is Pose.POINT:
        ctx.pt_last_point = ctx.ts
        _update_pos(ctx)
        _publish(eng, ctx)
        return True
    # NONE / UNKNOWN glitch: keep the pointer where it is until hide_after_ms
    if ctx.ts - (ctx.pt_last_point or ctx.ts) >= int(cfg.pointer.hide_after_ms):
        hide(eng, ctx)
        ctx.state = IDLE
        return False
    _publish(eng, ctx)
    return True


def _update_pos(ctx: HandCtx) -> None:
    pc = cfg.pinch
    x, y = _filtered(ctx)          # always run the filter so it stays warm
    if ctx.hand.pinch < float(pc.prefreeze):
        ctx.pt_frozen = True
        ctx.pt_unfreeze_at = None
        return
    if ctx.pt_frozen:
        if ctx.pt_unfreeze_at is None:
            ctx.pt_unfreeze_at = ctx.ts + int(pc.post_freeze_ms)
        if ctx.ts < ctx.pt_unfreeze_at:
            return
        ctx.pt_frozen = False
        ctx.pt_unfreeze_at = None
    ctx.pt_x, ctx.pt_y = x, y


def start_pinch(eng, ctx: HandCtx, from_pointer: bool) -> None:
    ctx.state = PINCHING
    ctx.pinch_start = ctx.ts
    ctx.pinch_from_pointer = from_pointer
    ctx.pinch_pos = (ctx.pt_x, ctx.pt_y) if from_pointer else None
    if from_pointer:
        ctx.pt_frozen = True
        ctx.pt_unfreeze_at = None
        _publish(eng, ctx)
    ctx.pen_buf = []
    strokes.buffer_point(ctx)


def pinch_step(eng, ctx: HandCtx) -> bool:
    """PINCHING state. Always consumes the frame."""
    pc = cfg.pinch
    held = ctx.ts - ctx.pinch_start
    if ctx.hand.pinch > float(pc.up):
        # A tap with no pointer showing only has a job while the writing preview is up (accept).
        # Anywhere else it was noise, and in TV mode it would press Select on the TV.
        if held <= int(pc.tap_max_ms) and (ctx.pinch_from_pointer or _preview_showing()):
            eng.gesture(GestureKind.PINCH_TAP, ctx, pos=ctx.pinch_pos)
        _after_release(eng, ctx)
        return True
    strokes.buffer_point(ctx)
    if ctx.pinch_from_pointer:
        _publish(eng, ctx)                 # frozen dot stays on screen
    if held > int(pc.tap_max_ms) and eng.ink_enabled():
        if ctx.pinch_from_pointer:
            hide(eng, ctx)
        strokes.begin(eng, ctx)
    return True


def _after_release(eng, ctx: HandCtx) -> None:
    ctx.pinch_start = None
    ctx.pen_buf = []
    if ctx.pinch_from_pointer and ctx.hand.pose is Pose.POINT:
        ctx.state = POINTER
        ctx.pt_last_point = ctx.ts
        ctx.pt_unfreeze_at = ctx.ts + int(cfg.pinch.post_freeze_ms)
        _publish(eng, ctx)
    else:
        if ctx.pinch_from_pointer:
            hide(eng, ctx)
        ctx.state = IDLE


def cancel(eng, ctx: HandCtx) -> None:
    if ctx.state in (POINTER, PINCHING) and (ctx.state == POINTER or ctx.pinch_from_pointer):
        hide(eng, ctx)
    ctx.pinch_start = None
    ctx.pen_buf = []
    ctx.state = IDLE
