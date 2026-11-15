"""zephyr/hud/handfx.py    Clear, state-coloured hand drawing for the overlay HUD. Owner: Aadharsh.

Pure painting helpers. They read the bus, never touch widgets, and are safe to call from paintEvent.

    from zephyr.hud import handfx
    label = handfx.draw_hands(p, inner_rect, hands_frame, t_ms)   # skeletons inside the HUD card
    handfx.draw_ripples(p, w, h, t_ms)                            # click ripple at the pointer

What the audience reads from it:
    hand colour     grey = off or ignored, blue = ready, green flash = gesture fired,
                    amber = paused (looking away), purple = guest in control
    fingers         extended fingers are bright with a white tip, curled fingers are dim
    pinch           a line between thumb and index that closes to a dot on click
    swipe           a fading trail behind the palm and an arrow when the swipe fires
    knob            a dial with a needle around the fist while brightness or volume is being turned
"""
from __future__ import annotations

import math
import time
from collections import deque
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen

from zephyr.bus import bus
from zephyr.config import cfg
from zephyr.contracts import GestureKind, Pose, Role, SystemMode, T
from zephyr.core import features as F

OFF = QColor(150, 150, 150)
READY = QColor(64, 196, 255)
FIRE = QColor(90, 220, 140)
PAUSED = QColor(255, 190, 70)
GUEST = QColor(186, 130, 255)
WHITE = QColor(255, 255, 255)

FINGERS = ((5, 6, 7, 8), (9, 10, 11, 12), (13, 14, 15, 16), (17, 18, 19, 20))
THUMB = (1, 2, 3, 4)
PALM = ((0, 1), (0, 5), (5, 9), (9, 13), (13, 17), (0, 17))
POSE_WORDS = {Pose.OPEN: "Open hand", Pose.FIST: "Fist", Pose.POINT: "Point",
              Pose.TWO: "Two fingers", Pose.UNKNOWN: "Custom shape", Pose.NONE: ""}

FLASH_MS = 350
TRAIL_MS = 280
ARROW_MS = 450
RIPPLE_MS = 420


def _now() -> int:
    return int(time.monotonic() * 1000)


class _Fx:
    """Small event memory fed by bus handlers (each handler only stores a value)."""

    def __init__(self) -> None:
        self.flash: dict[int, int] = {}                      # hand_id -> fired at
        self.arrow: Optional[tuple[str, int, int]] = None    # (direction, fired at, hand_id)
        self.ripples: deque[tuple[float, float, int]] = deque(maxlen=6)
        self.trails: dict[int, deque[tuple[int, float, float]]] = {}
        self.last_frame = -1
        self.level_t = -10**9         # last active LevelEvent
        self.level_kind = ""
        self.engaging = 0.0           # level hold progress (fist held, knob about to engage)
        bus.subscribe(T.GESTURE, self._on_gesture)
        bus.subscribe(T.LEVEL, self._on_level)
        bus.subscribe(T.HOLD, self._on_hold)

    def _on_level(self, e) -> None:
        self.level_kind = e.kind
        self.level_t = _now() if e.active else -10**9

    def _on_hold(self, e) -> None:
        if e.kind == "level":
            self.engaging = e.progress
            if e.progress >= 1.0:
                self.level_t = _now()

    def _on_gesture(self, e) -> None:
        t = _now()
        self.flash[e.hand_id] = t
        if e.kind is GestureKind.SWIPE:
            self.arrow = (e.direction, t, e.hand_id)
        elif e.kind is GestureKind.PINCH_TAP and e.pos is not None:
            self.ripples.append((e.pos[0], e.pos[1], t))


_fx: Optional[_Fx] = None


def fx() -> _Fx:
    global _fx
    if _fx is None:
        _fx = _Fx()
    return _fx


def _mix(a: QColor, b: QColor, k: float) -> QColor:
    k = min(1.0, max(0.0, k))
    return QColor(int(a.red() + (b.red() - a.red()) * k), int(a.green() + (b.green() - a.green()) * k),
                  int(a.blue() + (b.blue() - a.blue()) * k))


def _alpha(c: QColor, a: float) -> QColor:
    out = QColor(c)
    out.setAlphaF(min(1.0, max(0.0, a)))
    return out


def _font(px: float, bold: bool = True) -> QFont:
    f = QFont()
    f.setPixelSize(max(8, int(px)))
    f.setBold(bold)
    return f


def hand_colour(hand, mode: SystemMode, t: int) -> QColor:
    if hand.role is Role.STRANGER or mode is SystemMode.DISARMED:
        return OFF
    if mode is SystemMode.SUSPENDED:
        return PAUSED
    base = GUEST if hand.role is Role.GUEST else READY
    age = t - fx().flash.get(hand.hand_id, -10**9)
    if 0 <= age < FLASH_MS:
        return _mix(FIRE, base, age / FLASH_MS)
    return base


def _extended(hand) -> tuple[bool, list[bool]]:
    p = cfg.poses
    mid = (float(p.extend_deg) + float(p.curl_deg)) / 2
    pinky_mid = (float(p.get("pinky_extend_deg", p.extend_deg)) + float(p.get("pinky_curl_deg", p.curl_deg))) / 2
    fingers = [a > (pinky_mid if i == 3 else mid) for i, a in enumerate(F.finger_angles(hand.world))]
    thumb = F.thumb_extended(hand.landmarks, hand.world, hand.scale, float(p.thumb_deg), float(p.thumb_tip_scales))
    return thumb, fingers


def _line(p: QPainter, pts, chain, colour: QColor, lw: float, glow: float) -> None:
    path = QPainterPath(pts[chain[0]])
    for i in chain[1:]:
        path.lineTo(pts[i])
    p.setBrush(Qt.NoBrush)
    if glow > 0:
        p.setPen(QPen(_alpha(colour, glow), lw * 2.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(path)
    p.setPen(QPen(colour, lw, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.drawPath(path)


def draw_hands(p: QPainter, rect: QRectF, frame, t: Optional[int] = None) -> str:
    """Draw every hand of a HandsFrame inside rect. Returns the pose word of the controlling hand."""
    t = _now() if t is None else t
    state = fx()
    sysm = bus.latest(T.SYSTEM)
    mode = sysm.mode if sysm is not None else SystemMode.DISARMED
    if frame is None or not frame.hands:
        state.trails.clear()
        return ""
    new_frame = frame.frame_id != state.last_frame
    state.last_frame = frame.frame_id
    lw = max(3.0, rect.height() / 55.0)

    def pt(x: float, y: float) -> QPointF:
        return QPointF(rect.left() + x * rect.width(), rect.top() + y * rect.height())

    p.save()
    p.setClipRect(rect.adjusted(-4, -4, 4, 4))
    seen = set()
    for hand in frame.hands:
        seen.add(hand.hand_id)
        lm = hand.landmarks
        pts = [pt(float(a[0]), float(a[1])) for a in lm]
        colour = hand_colour(hand, mode, t)
        ignored = hand.role is Role.STRANGER
        dim = _alpha(colour, 0.38)
        cx, cy = F.palm_centre(lm)

        # swipe trail (only once the palm has really moved)
        trail = state.trails.setdefault(hand.hand_id, deque())
        if new_frame:
            trail.append((t, cx, cy))
        while trail and t - trail[0][0] > TRAIL_MS:
            trail.popleft()
        if not ignored and mode is SystemMode.ARMED and len(trail) >= 3:
            travel = abs(trail[-1][1] - trail[0][1]) / max(hand.scale, 1e-4)
            if travel > 0.8:
                n = len(trail)
                for i in range(1, n):
                    k = i / n
                    p.setPen(QPen(_alpha(colour, 0.5 * k), lw * (0.8 + 2.2 * k), Qt.SolidLine, Qt.RoundCap))
                    p.drawLine(pt(trail[i - 1][1], trail[i - 1][2]), pt(trail[i][1], trail[i][2]))

        # palm, then fingers: extended bright, curled dim
        thumb_ext, ext = _extended(hand)
        for a, b in PALM:
            _line(p, pts, (a, b), _alpha(colour, 0.75), lw, 0.0 if ignored else 0.12)
        _line(p, pts, (0,) + THUMB, colour if thumb_ext else dim, lw, 0.2 if thumb_ext and not ignored else 0.0)
        for chain, is_ext in zip(FINGERS, ext):
            _line(p, pts, chain, colour if is_ext else dim, lw, 0.22 if is_ext and not ignored else 0.0)
        p.setPen(Qt.NoPen)
        for tip, is_ext in zip((4, 8, 12, 16, 20), [thumb_ext] + ext):
            if is_ext and not ignored:
                p.setBrush(WHITE if mode is not SystemMode.DISARMED else QColor(205, 205, 205))
                p.drawEllipse(pts[tip], lw * 0.95, lw * 0.95)
            else:
                p.setBrush(dim)
                p.drawEllipse(pts[tip], lw * 0.55, lw * 0.55)

        # pinch: a line that closes to a dot
        if not ignored and mode is SystemMode.ARMED and hand.pose is not Pose.FIST:
            pc = cfg.pinch
            if hand.pinch < float(pc.down):
                mid = QPointF((pts[4].x() + pts[8].x()) / 2, (pts[4].y() + pts[8].y()) / 2)
                p.setBrush(_alpha(FIRE, 0.35))
                p.drawEllipse(mid, lw * 3.0, lw * 3.0)
                p.setBrush(WHITE)
                p.drawEllipse(mid, lw * 1.3, lw * 1.3)
            elif hand.pinch < float(pc.prefreeze):
                p.setPen(QPen(_alpha(WHITE, 0.9), lw * 0.6, Qt.DashLine, Qt.RoundCap))
                p.drawLine(pts[4], pts[8])
                p.setPen(Qt.NoPen)

        # knob dial around the fist
        knob_on = t - state.level_t < 1500 or state.engaging > 0.0
        if not ignored and mode is SystemMode.ARMED and hand.pose is Pose.FIST and knob_on:
            _draw_knob(p, lm, pts, colour, lw, state, hand)

        if ignored and mode is not SystemMode.DISARMED:
            p.setFont(_font(lw * 3.0))
            p.setPen(OFF)
            p.drawText(QRectF(pts[0].x() - 60, pts[0].y() + lw * 1.5, 120, lw * 4.5), Qt.AlignHCenter, "ignored")

    # swipe arrow
    if state.arrow is not None:
        direction, t0, hid = state.arrow
        age = t - t0
        if 0 <= age < ARROW_MS:
            k = age / ARROW_MS
            sign = -1.0 if direction == "left" else 1.0
            y = rect.center().y()
            x0 = rect.center().x() - sign * rect.width() * 0.18
            x1 = x0 + sign * rect.width() * (0.22 + 0.2 * (1 - (1 - k) ** 3))
            col = _alpha(FIRE, 1.0 - k)
            head = lw * 3.2
            p.setPen(QPen(col, lw * 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawLine(QPointF(x0, y), QPointF(x1, y))
            p.drawLine(QPointF(x1, y), QPointF(x1 - sign * head, y - head))
            p.drawLine(QPointF(x1, y), QPointF(x1 - sign * head, y + head))
        else:
            state.arrow = None
    p.restore()

    for hid in list(state.trails):
        if hid not in seen:
            del state.trails[hid]
    for hid in [h for h, t0 in state.flash.items() if t - t0 > 5000]:
        state.flash.pop(hid, None)

    acting = [h for h in frame.hands if h.role is not Role.STRANGER]
    if not acting:
        return ""
    return POSE_WORDS.get(max(acting, key=lambda h: h.scale).pose, "")


def _draw_knob(p: QPainter, lm, pts, colour: QColor, lw: float, state: _Fx, hand) -> None:
    kx = sum(pts[i].x() for i in (5, 9, 13, 17)) / 4
    ky = sum(pts[i].y() for i in (5, 9, 13, 17)) / 4
    c = QPointF((kx + pts[0].x()) / 2, (ky + pts[0].y()) / 2)
    r = math.hypot(kx - pts[0].x(), ky - pts[0].y()) * 1.05 + lw * 3
    ang = math.atan2(kx - pts[0].x(), pts[0].y() - ky)       # 0 = straight up, + = clockwise
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(_alpha(colour, 0.35), lw * 0.8))
    p.drawEllipse(c, r, r)
    for k in range(-6, 7):                                   # tick marks every 15 degrees
        a = math.radians(15 * k)
        inner, outer = r - lw * (1.6 if k % 3 == 0 else 0.9), r
        p.setPen(QPen(_alpha(WHITE, 0.55 if k % 3 == 0 else 0.3), lw * 0.5, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(c.x() + inner * math.sin(a), c.y() - inner * math.cos(a)),
                   QPointF(c.x() + outer * math.sin(a), c.y() - outer * math.cos(a)))
    tip = QPointF(c.x() + (r + lw * 1.5) * math.sin(ang), c.y() - (r + lw * 1.5) * math.cos(ang))
    p.setPen(QPen(WHITE, lw * 1.1, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(QPointF(c.x() + r * 0.55 * math.sin(ang), c.y() - r * 0.55 * math.cos(ang)), tip)
    p.setPen(Qt.NoPen)
    p.setBrush(colour)
    p.drawEllipse(tip, lw * 1.2, lw * 1.2)
    label = "Brightness" if state.level_kind == "brightness" or (not state.level_kind and hand.side.value == "left") else "Volume"
    p.setFont(_font(lw * 3.2))
    p.setPen(_alpha(WHITE, 0.9))
    p.drawText(QRectF(c.x() - 90, c.y() + r + lw * 1.5, 180, lw * 5), Qt.AlignHCenter, f"↺ {label} ↻")


def draw_ripples(p: QPainter, w: float, h: float, t: Optional[int] = None) -> None:
    """Expanding ring where a pinch click landed, so the audience sees the click."""
    t = _now() if t is None else t
    for x, y, t0 in list(fx().ripples):
        age = t - t0
        if not 0 <= age < RIPPLE_MS:
            continue
        k = age / RIPPLE_MS
        r = 14 + 46 * (1 - (1 - k) ** 3)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(_alpha(FIRE, 1.0 - k), 4.0 * (1 - k) + 1.0))
        p.drawEllipse(QPointF(x * w, y * h), r, r)


def animating(t: Optional[int] = None) -> bool:
    """True while a flash, arrow or ripple is still fading (the overlay should keep repainting)."""
    t = _now() if t is None else t
    s = fx()
    return (any(t - t0 < FLASH_MS for t0 in s.flash.values())
            or (s.arrow is not None and t - s.arrow[1] < ARROW_MS)
            or any(t - r[2] < RIPPLE_MS for r in s.ripples))
