"""zephyr/core/strokes.py    Pen strokes for air writing. Owner: Aadharsh.

Pen position: midpoint of thumb tip (4) and index tip (8), mapped through pointer.box, with its own
One Euro filter (pen.min_cutoff, pen.beta). On entering PEN: "begin" with the first point buffered
since pinch down, then "point" for the rest of the buffer and every frame after, "end" on pinch up.
stroke_id increments per stroke.
"""
from __future__ import annotations

from zephyr.config import cfg
from zephyr.contracts import StrokeEvent, T
from zephyr.core_win.filters import OneEuro2D
from zephyr.core_win.handctx import IDLE, PEN, HandCtx, box_map

_next_stroke = 0


def _pen_pos(ctx: HandCtx) -> tuple[float, float]:
    lm = ctx.hand.landmarks
    x = (float(lm[4, 0]) + float(lm[8, 0])) / 2
    y = (float(lm[4, 1]) + float(lm[8, 1])) / 2
    u, v = box_map(x, y, cfg.pointer.box)
    pc = cfg.get("pen")
    mc = float(pc.min_cutoff) if pc else 1.5
    beta = float(pc.beta) if pc else 0.02
    if ctx.pen_filter is None:
        ctx.pen_filter = OneEuro2D(mc, beta)
    ctx.pen_filter.tune(mc, beta)
    return ctx.pen_filter(u, v, ctx.ts / 1000.0)


def buffer_point(ctx: HandCtx) -> None:
    """Called every frame while pinching, before the pen starts."""
    if not ctx.pen_buf and ctx.pen_filter is not None:
        ctx.pen_filter.reset()
    x, y = _pen_pos(ctx)
    ctx.pen_buf.append((x, y, ctx.ts))


def begin(eng, ctx: HandCtx) -> None:
    global _next_stroke
    _next_stroke += 1
    ctx.stroke_id = _next_stroke
    ctx.state = PEN
    buf = ctx.pen_buf or [(*_pen_pos(ctx), ctx.ts)]
    x, y, t = buf[0]
    eng.pub(T.STROKE, StrokeEvent("begin", ctx.stroke_id, x, y, t))
    for x, y, t in buf[1:]:
        eng.pub(T.STROKE, StrokeEvent("point", ctx.stroke_id, x, y, t))
    ctx.pen_buf = []


def step(eng, ctx: HandCtx) -> bool:
    """PEN state. Always consumes the frame."""
    x, y = _pen_pos(ctx)
    if ctx.hand.pinch > float(cfg.pinch.up):
        end(eng, ctx, x, y)
        return True
    eng.pub(T.STROKE, StrokeEvent("point", ctx.stroke_id, x, y, ctx.ts))
    return True


def end(eng, ctx: HandCtx, x: float | None = None, y: float | None = None) -> None:
    if ctx.stroke_id is not None:
        if x is None:
            x, y = (ctx.pen_filter.fx.x_prev or 0.0, ctx.pen_filter.fy.x_prev or 0.0) if ctx.pen_filter else (0.0, 0.0)
        eng.pub(T.STROKE, StrokeEvent("end", ctx.stroke_id, x, y, ctx.ts))
    ctx.stroke_id = None
    ctx.pinch_start = None
    ctx.state = IDLE
