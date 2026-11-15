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
from zephyr.contracts import GestureKind, PointerEvent, Pose, Role, T
from zephyr.core_win import strokes
from zephyr.core_win.filters import OneEuro2D
from zephyr.core_win.handctx import IDLE, PINCHING, POINTER, HandCtx, box_map

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


def pinch_is_down(ctx: HandCtx, eng=None, from_pointer: bool = False) -> bool:
    """A new pinch: thumb and index closed, not a fist, and clearly apart for pinch.open_before_ms
    just before. Measured: a hand resting or holding a cup flickers across the pinch thresholds and
    gave 64 taps in a minute of normal movement; a real click (point, then pinch) always starts open.
    While writing, the gap between two strokes of a letter is short, so the pen uses
    pinch.pen_open_before_ms instead.
    From the pointer a pinch may read as a fist: the other three fingers are already curled, so the
    index bending to the thumb curls the last one (measured: 2 of 10 clicks were thrown away as
    fists). It still counts when the hand was pointing within pinch.fist_grace_ms."""
    if ctx.hand.pinch >= float(cfg.pinch.down):
        return False
    if ctx.hand.pose is Pose.TWO:
        return False    # the two-finger swipe pose: blur at the start of a flick looked like a pinch
    if ctx.hand.pose is Pose.FIST and not (
            from_pointer and ctx.ts - (ctx.pt_last_point or -10**9) <= int(cfg.pinch.get("fist_grace_ms", 250))):
        return False
    writing = eng is not None and eng.ink_enabled()
    need = int(cfg.pinch.get("pen_open_before_ms", 60) if writing else cfg.pinch.get("open_before_ms", 150))
    if ctx.tap_pending is not None:
        need = int(cfg.pinch.get("pen_open_before_ms", 60))   # the second pinch of a double comes quickly
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
    # A click starts from a nearly still hand (measured: under 0.7 hand widths a second). A hand
    # flying sideways blurs, and one blurred frame looked like a pinch: accidental clicks mid-swipe.
    if pinch_is_down(ctx, eng, from_pointer=True) and ctx.speed < float(cfg.pinch.get("max_start_speed", 2.0)):
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
    ctx.pt_x, ctx.pt_y = _deadband(ctx.pt_x, ctx.pt_y, x, y)


def _deadband(ox: float, oy: float, x: float, y: float) -> tuple[float, float]:
    """Hold the pointer still inside pointer.deadband (screen fraction) of where it is, and past it
    follow by only the distance beyond the band, so there is never a jump. Smoothing alone could not
    get a resting hand under ~10 px of jitter without the pointer trailing 60 px behind a moving one."""
    band = float(cfg.pointer.get("deadband", 0.0))
    if band <= 0:
        return x, y
    from zephyr import contracts
    dx, dy = (x - ox) * contracts.ASPECT, y - oy     # screen is wide: compare in the same units
    d = (dx * dx + dy * dy) ** 0.5
    if d <= band:
        return ox, oy
    k = (d - band) / d
    return ox + (x - ox) * k, oy + (y - oy) * k


RIGHT_CLICK = "win.right_click"     # Windows-only bus topic; actions/windows.py does the click


def _double_ms() -> int:
    return int(cfg.pinch.get("double_ms", 0))


def flush_tap(eng, ctx: HandCtx) -> None:
    """A single pinch waits double_ms after letting go: if no second pinch starts, it is a left click."""
    if ctx.tap_pending is None or ctx.state == PINCHING:
        return
    pos, released = ctx.tap_pending
    if ctx.ts - released >= _double_ms():
        ctx.tap_pending = None
        eng.gesture(GestureKind.PINCH_TAP, ctx, pos=pos)


def drop_tap(ctx: HandCtx) -> None:
    ctx.tap_pending = None
    ctx.pinch_second = False


def start_pinch(eng, ctx: HandCtx, from_pointer: bool) -> None:
    ctx.state = PINCHING
    ctx.pinch_start = ctx.ts
    # the second pinch of a double: it may start after the pointer hid, and it clicks where the first did
    ctx.pinch_second = ctx.tap_pending is not None
    if ctx.pinch_second and not from_pointer:
        from_pointer = True
        ctx.pt_x, ctx.pt_y = ctx.tap_pending[0]
    ctx.pinch_from_pointer = from_pointer
    ctx.pinch_pos = (ctx.pt_x, ctx.pt_y) if from_pointer else None
    if ctx.pinch_second:
        ctx.pinch_pos = ctx.tap_pending[0]
    if from_pointer:
        ctx.pt_frozen = True
        ctx.pt_unfreeze_at = None
        _publish(eng, ctx)
    ctx.pinch_cx0, ctx.pinch_cy0 = ctx.cx, ctx.cy
    ctx.pen_buf = []
    strokes.buffer_point(ctx)


def _pinch_travel(ctx: HandCtx) -> float:
    """How far the pinching hand has moved since the pinch closed, in hand scales."""
    from zephyr import contracts
    dx = (ctx.cx - ctx.pinch_cx0) * contracts.ASPECT
    dy = ctx.cy - ctx.pinch_cy0
    return (dx * dx + dy * dy) ** 0.5 / max(ctx.scale, 1e-4)


def pinch_step(eng, ctx: HandCtx) -> bool:
    """PINCHING state. Always consumes the frame."""
    pc = cfg.pinch
    held = ctx.ts - ctx.pinch_start
    click_max = int(pc.get("click_max_ms", 1000))
    preview = _preview_showing()
    if ctx.hand.pinch > float(pc.up):
        # Released: a click. People pinch slower than it looks: measured clicks held 470 to 870 ms,
        # so click_max_ms is about a second, not the old 300 ms (which missed 10 of 10).
        # A tap with no pointer showing only has a job while the writing preview is up (accept).
        # Anywhere else it was noise, and in TV mode it would press Select on the TV.
        if held <= click_max and (ctx.pinch_from_pointer or preview):
            if ctx.pinch_second:
                # pinch twice = right click (the first pinch's left click is never sent)
                ctx.tap_pending = None
                if ctx.hand.role is not Role.STRANGER:
                    eng.pub(RIGHT_CLICK, ctx.pinch_pos)
            elif ctx.pinch_from_pointer and not preview and _double_ms() > 0:
                ctx.tap_pending = (ctx.pinch_pos, ctx.ts)   # left click once double_ms passes alone
            else:
                eng.gesture(GestureKind.PINCH_TAP, ctx, pos=ctx.pinch_pos)
        elif ctx.pinch_second and ctx.tap_pending is not None:
            # the second pinch was not a click (held too long): the first one still clicks
            pos, _ = ctx.tap_pending
            ctx.tap_pending = None
            eng.gesture(GestureKind.PINCH_TAP, ctx, pos=pos)
        ctx.pinch_second = False
        _after_release(eng, ctx)
        return True
    strokes.buffer_point(ctx)
    if ctx.pinch_from_pointer:
        _publish(eng, ctx)                 # frozen dot stays on screen
    # Pen: only where ink is on, never over the writing preview (a pinch there accepts).
    # A pinch made while pointing stays a click until the hand moves (pinch and move writes,
    # pinch and let go clicks); the points since the pinch closed are buffered, so no ink is lost.
    if held > int(pc.tap_max_ms) and not preview and eng.ink_enabled():
        if (not ctx.pinch_from_pointer or held > click_max
                or _pinch_travel(ctx) > float(pc.get("pen_start_travel_scales", 0.25))):
            if ctx.pinch_from_pointer:
                hide(eng, ctx)
            if ctx.tap_pending is not None:
                pos, _ = ctx.tap_pending
                ctx.tap_pending = None
                eng.gesture(GestureKind.PINCH_TAP, ctx, pos=pos)
            ctx.pinch_second = False
            strokes.begin(eng, ctx)
    return True


def _after_release(eng, ctx: HandCtx) -> None:
    ctx.pinch_start = None
    ctx.pinch_end_at = ctx.ts     # a pinching hand reads as a fist: no fist tap straight after
    ctx.pen_buf = []
    ctx.palm_block_until = max(ctx.palm_block_until, ctx.ts + int(cfg.holds.get("after_release_ms", 0)))
    # Back to the pointer unless the hand clearly became something else. As a click lets go the
    # index is still bent and the hand reads as a fist for a few frames; hiding the pointer for
    # that made it blink off and back on after a click. pointer_step hides it after
    # pointer.hide_after_ms if the pointing hand does not come back.
    if ctx.pinch_from_pointer and ctx.hand.pose not in SWITCH_POSES:
        ctx.state = POINTER
        ctx.pt_last_point = ctx.ts
        ctx.pt_unfreeze_at = ctx.ts + int(cfg.pinch.post_freeze_ms)
        _publish(eng, ctx)
    else:
        if ctx.pinch_from_pointer:
            hide(eng, ctx)
        ctx.state = IDLE


def cancel(eng, ctx: HandCtx) -> None:
    drop_tap(ctx)       # paused, lost or handed to another gesture: a waiting click is dropped
    if ctx.state in (POINTER, PINCHING) and (ctx.state == POINTER or ctx.pinch_from_pointer):
        hide(eng, ctx)
    ctx.pinch_start = None
    ctx.pen_buf = []
    ctx.state = IDLE
