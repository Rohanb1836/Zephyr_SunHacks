"""zephyr/ui/widgets.py  Drawing code for each overlay element. Owner: Swayam.

Every function takes the painter, the overlay size and the UiState, and draws
one element. Look: dark translucent cards, white text, one accent colour, grey
for anything ignored. No blur effects. Motion is 120 to 220 ms ease-out, except
"Access granted" which takes 600 ms.
"""
from __future__ import annotations

import math
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen,
                           QRadialGradient)

from zephyr.contracts import Role
from zephyr.ui.state import (ACCESS_IN_MS, ACCESS_TOTAL_MS, CHIP_ANIM_MS, LEVEL_HIDE_MS, TOAST_MS,
                             WRITE_ERROR_MS, UiState)

ACCENT = QColor(64, 196, 255)
ACCENT_SOFT = QColor(64, 196, 255, 90)
GREY = QColor(150, 150, 150)
WHITE = QColor(255, 255, 255)
CARD = QColor(0, 0, 0, 178)          # about 70 percent black
RED = QColor(255, 90, 90)
AMBER = QColor(255, 190, 70)
GREEN = QColor(90, 220, 140)

BONES = [
    (0, 1), (1, 2), (2, 3), (3, 4),          # thumb
    (0, 5), (5, 6), (6, 7), (7, 8),          # index
    (5, 9), (9, 10), (10, 11), (11, 12),     # middle
    (9, 13), (13, 14), (14, 15), (15, 16),   # ring
    (13, 17), (17, 18), (18, 19), (19, 20),  # pinky
    (0, 17),                                 # palm edge
]

HOLD_LABELS = {"arm_toggle": "Hold both palms", "palm_hold": "Search", "level": "Level",
               "custom": "Custom gesture"}


def ease_out(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return 1 - (1 - x) ** 3


def font(pt: float, bold: bool = False) -> QFont:
    f = QFont()
    f.setPointSizeF(pt)
    f.setBold(bold)
    return f


def with_alpha(c: QColor, a: float) -> QColor:
    out = QColor(c)
    out.setAlphaF(max(0.0, min(1.0, c.alphaF() * a)))
    return out


def rounded_card(p: QPainter, rect: QRectF, radius: float, color: QColor = CARD) -> None:
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawRoundedRect(rect, radius, radius)


def text_width(f: QFont, text: str) -> float:
    return QFontMetricsF(f).horizontalAdvance(text)


# ---------------------------------------------------------------- HUD card

def hud_rect(w: float, h: float, hud_size: tuple[int, int], corner: str) -> QRectF:
    cw, ch = hud_size
    m = 24.0
    x = w - cw - m if "right" in corner else m
    y = h - ch - m - 26 if "bottom" in corner else m + 60
    return QRectF(x, y, cw, ch)


def hand_points(landmarks, rect: QRectF) -> list[QPointF]:
    return [QPointF(rect.left() + float(x) * rect.width(), rect.top() + float(y) * rect.height())
            for x, y in ((lm[0], lm[1]) for lm in landmarks)]


def draw_hud(p: QPainter, w: float, h: float, s: UiState, hud_size, corner) -> QRectF:
    rect = hud_rect(w, h, hud_size, corner)
    rounded_card(p, rect, 14)
    inner = rect.adjusted(10, 10, -10, -10)

    frame = s.hands
    pose_text = "no hand"
    if frame is not None and frame.hands:
        controller = [hd for hd in frame.hands if hd.role is not Role.STRANGER]
        if controller:
            pose_text = max(controller, key=lambda hd: hd.scale).pose.value
        for hand in frame.hands:
            ignored = hand.role is Role.STRANGER
            color = GREY if ignored else ACCENT
            pts = hand_points(hand.landmarks, inner)
            p.setPen(QPen(with_alpha(color, 0.9), 2.2, Qt.SolidLine, Qt.RoundCap))
            for a, b in BONES:
                p.drawLine(pts[a], pts[b])
            p.setPen(Qt.NoPen)
            p.setBrush(WHITE if not ignored else GREY)
            for pt in pts:
                p.drawEllipse(pt, 2.6, 2.6)
            if ignored:
                p.setFont(font(11, True))
                p.setPen(GREY)
                wrist = pts[0]
                p.drawText(QPointF(wrist.x() - 22, min(inner.bottom() - 2, wrist.y() + 16)), "ignored")
    else:
        p.setFont(font(12))
        p.setPen(with_alpha(WHITE, 0.45))
        p.drawText(rect, Qt.AlignCenter, "Show a hand")

    # pose name under the card
    p.setFont(font(12, True))
    p.setPen(with_alpha(WHITE, 0.85))
    p.drawText(QRectF(rect.left(), rect.bottom() + 4, rect.width(), 20), Qt.AlignHCenter, pose_text)
    return rect


# ---------------------------------------------------------------- status pill + app chip

TONE_COLORS = {"off": GREY, "ready": GREEN, "paused": AMBER, "guest": ACCENT}


def draw_status(p: QPainter, w: float, s: UiState, t: int) -> None:
    text, tone = s.status_text()
    f = font(16, True)
    tw = text_width(f, text)
    pill_w, pill_h = tw + 56, 40
    chip_text = s.chip_label
    cf = font(14, True)
    chip_w = text_width(cf, chip_text) + 32 if chip_text else 0
    gap = 10 if chip_text else 0
    total = pill_w + gap + chip_w
    x0 = (w - total) / 2
    y0 = 18.0

    pill = QRectF(x0, y0, pill_w, pill_h)
    rounded_card(p, pill, pill_h / 2)
    p.setPen(Qt.NoPen)
    p.setBrush(TONE_COLORS.get(tone, GREY))
    p.drawEllipse(QPointF(pill.left() + 22, pill.center().y()), 6, 6)
    p.setFont(f)
    p.setPen(WHITE)
    p.drawText(pill.adjusted(36, 0, -16, 0), Qt.AlignVCenter | Qt.AlignLeft, text)

    if chip_text:
        k = ease_out((t - s.chip_t) / CHIP_ANIM_MS)
        chip = QRectF(pill.right() + gap, y0 + 4 - (1 - k) * 10, chip_w, pill_h - 8)
        p.setOpacity(0.25 + 0.75 * k)
        rounded_card(p, chip, (pill_h - 8) / 2, QColor(ACCENT.red(), ACCENT.green(), ACCENT.blue(), 200))
        p.setFont(cf)
        p.setPen(QColor(0, 0, 0))
        p.drawText(chip, Qt.AlignCenter, chip_text)
        p.setOpacity(1.0)


# ---------------------------------------------------------------- hold ring

def draw_ring(p: QPainter, c: QPointF, r: float, progress: float, width: float = 5.0,
              color: QColor = ACCENT) -> None:
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(QColor(255, 255, 255, 50), width))
    p.drawEllipse(c, r, r)
    p.setPen(QPen(color, width, Qt.SolidLine, Qt.RoundCap))
    span = int(-360 * 16 * max(0.0, min(1.0, progress)))
    p.drawArc(QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r), 90 * 16, span)


def draw_hold(p: QPainter, w: float, h: float, s: UiState, hud: QRectF) -> None:
    hold = s.hold
    if hold is None:
        return
    if s.pointer_alpha > 0.5 and hold.kind not in ("arm_toggle",):
        c = QPointF(s.pointer_xy[0] * w, s.pointer_xy[1] * h)
        draw_ring(p, c, 26, hold.progress)
        return
    # around the hand in the HUD
    center = hud.center()
    if s.hands is not None:
        inner = hud.adjusted(10, 10, -10, -10)
        for hand in s.hands.hands:
            if hand.hand_id == hold.hand_id or hold.hand_id == -1:
                pts = hand_points(hand.landmarks, inner)
                center = QPointF(sum(pt.x() for pt in pts) / 21, sum(pt.y() for pt in pts) / 21)
                break
    draw_ring(p, center, 48, hold.progress)
    label = HOLD_LABELS.get(hold.kind, hold.kind)
    p.setFont(font(12, True))
    p.setPen(WHITE)
    p.drawText(QRectF(hud.left(), hud.top() - 24, hud.width(), 20), Qt.AlignHCenter, label)


# ---------------------------------------------------------------- pointer dot

def draw_pointer(p: QPainter, w: float, h: float, s: UiState) -> None:
    a = s.pointer_alpha
    if a <= 0.01:
        return
    c = QPointF(s.pointer_xy[0] * w, s.pointer_xy[1] * h)
    frozen = s.pointer is not None and s.pointer.frozen
    r = 9.0 if frozen else 12.0
    glow = QRadialGradient(c, r * 3)
    glow.setColorAt(0.0, with_alpha(ACCENT, 0.55 * a))
    glow.setColorAt(1.0, QColor(ACCENT.red(), ACCENT.green(), ACCENT.blue(), 0))
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(glow))
    p.drawEllipse(c, r * 3, r * 3)
    p.setBrush(with_alpha(WHITE, a))
    p.drawEllipse(c, r * 0.55, r * 0.55)
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(with_alpha(ACCENT, a), 2.5))
    p.drawEllipse(c, r, r)


# ---------------------------------------------------------------- action toast

def draw_toast(p: QPainter, w: float, h: float, s: UiState, t: int) -> None:
    if s.toast is None:
        return
    age = t - s.toast_t
    if age > TOAST_MS + 200:
        return
    a = ease_out(age / 160) if age < 160 else (1.0 if age < TOAST_MS else 1 - (age - TOAST_MS) / 200)
    toast = s.toast
    text = f"{toast.app_label}: {toast.label}" if toast.app_label else toast.label
    f = font(24, True)
    tw = text_width(f, text)
    rect = QRectF((w - tw) / 2 - 28, h - 120 + (1 - a) * 12, tw + 56, 58)
    p.setOpacity(max(0.0, a))
    bg = QColor(120, 20, 20, 200) if not toast.ok else CARD
    rounded_card(p, rect, 16, bg)
    p.setFont(f)
    p.setPen(WHITE)
    p.drawText(rect, Qt.AlignCenter, text)
    p.setOpacity(1.0)


# ---------------------------------------------------------------- level slider

def _sun(p: QPainter, c: QPointF, color: QColor) -> None:
    p.setPen(QPen(color, 2.2, Qt.SolidLine, Qt.RoundCap))
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(c, 6, 6)
    for i in range(8):
        ang = i * math.pi / 4
        p.drawLine(QPointF(c.x() + 9 * math.cos(ang), c.y() + 9 * math.sin(ang)),
                   QPointF(c.x() + 13 * math.cos(ang), c.y() + 13 * math.sin(ang)))


def _speaker(p: QPainter, c: QPointF, color: QColor) -> None:
    path = QPainterPath()
    path.moveTo(c.x() - 11, c.y() - 4)
    path.lineTo(c.x() - 5, c.y() - 4)
    path.lineTo(c.x() + 2, c.y() - 10)
    path.lineTo(c.x() + 2, c.y() + 10)
    path.lineTo(c.x() - 5, c.y() + 4)
    path.lineTo(c.x() - 11, c.y() + 4)
    path.closeSubpath()
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawPath(path)
    p.setPen(QPen(color, 2.2, Qt.SolidLine, Qt.RoundCap))
    p.setBrush(Qt.NoBrush)
    p.drawArc(QRectF(c.x() - 2, c.y() - 7, 12, 14), -60 * 16, 120 * 16)


def draw_level(p: QPainter, w: float, h: float, s: UiState, t: int) -> None:
    lv = s.level
    if lv is None:
        return
    age = t - s.level_t
    if age > LEVEL_HIDE_MS + 200:
        return
    a = 1.0 if age < LEVEL_HIDE_MS else 1 - (age - LEVEL_HIDE_MS) / 200
    p.setOpacity(max(0.0, a))
    bar_h = min(320.0, h * 0.42)
    card = QRectF(w - 96, (h - bar_h) / 2 - 56, 64, bar_h + 112)
    rounded_card(p, card, 20)
    icon_c = QPointF(card.center().x(), card.top() + 28)
    (_sun if lv.kind == "brightness" else _speaker)(p, icon_c, WHITE)
    track = QRectF(card.center().x() - 7, card.top() + 56, 14, bar_h)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 255, 255, 50))
    p.drawRoundedRect(track, 7, 7)
    frac = max(0.0, min(1.0, lv.value / 100.0))
    fill = QRectF(track.left(), track.bottom() - bar_h * frac, track.width(), bar_h * frac)
    p.setBrush(ACCENT)
    p.drawRoundedRect(fill, 7, 7)
    p.setFont(font(13, True))
    p.setPen(WHITE)
    p.drawText(QRectF(card.left(), track.bottom() + 8, card.width(), 22), Qt.AlignHCenter,
               f"{int(round(lv.value))}")
    p.setOpacity(1.0)


# ---------------------------------------------------------------- ink

def draw_ink(p: QPainter, w: float, h: float, s: UiState) -> None:
    if not s.strokes:
        return
    for pts in s.strokes.values():
        if len(pts) < 2:
            if pts:
                p.setPen(Qt.NoPen)
                p.setBrush(WHITE)
                p.drawEllipse(QPointF(pts[0][0] * w, pts[0][1] * h), 2.5, 2.5)
            continue
        path = QPainterPath(QPointF(pts[0][0] * w, pts[0][1] * h))
        # quadratic curves through midpoints give a smooth line
        for i in range(1, len(pts) - 1):
            cx, cy = pts[i][0] * w, pts[i][1] * h
            mx, my = (pts[i][0] + pts[i + 1][0]) / 2 * w, (pts[i][1] + pts[i + 1][1]) / 2 * h
            path.quadTo(QPointF(cx, cy), QPointF(mx, my))
        path.lineTo(QPointF(pts[-1][0] * w, pts[-1][1] * h))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(ACCENT_SOFT, 11, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(path)
        p.setPen(QPen(WHITE, 5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(path)


# ---------------------------------------------------------------- preview bubble

def _anchor(s: UiState, w: float, h: float) -> QPointF:
    if s.write is not None and s.write.anchor is not None:
        return QPointF(s.write.anchor[0] * w, s.write.anchor[1] * h)
    # fall back to just under the ink
    ys = [pt[1] for pts in s.strokes.values() for pt in pts]
    xs = [pt[0] for pts in s.strokes.values() for pt in pts]
    if xs:
        return QPointF(sum(xs) / len(xs) * w, max(ys) * h + 40)
    return QPointF(w / 2, h * 0.62)


def _bubble(p: QPainter, center_top: QPointF, w: float, h: float, width: float, height: float,
            color: QColor = CARD) -> QRectF:
    x = min(max(16.0, center_top.x() - width / 2), w - width - 16)
    y = min(max(70.0, center_top.y()), h - height - 140)
    rect = QRectF(x, y, width, height)
    rounded_card(p, rect, 18, color)
    return rect


def draw_write(p: QPainter, w: float, h: float, s: UiState, t: int) -> None:
    phase = s.write_phase
    anchor = _anchor(s, w, h)
    if phase == "transcribing":
        rect = _bubble(p, anchor, w, h, 84, 84)
        c = rect.center()
        ang = ((t - s.spin_t0) % 900) / 900 * 360
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 50), 5))
        p.drawEllipse(c, 20, 20)
        p.setPen(QPen(ACCENT, 5, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(QRectF(c.x() - 20, c.y() - 20, 40, 40), int(-ang * 16), 100 * 16)
    elif phase == "preview" and s.write is not None:
        tf, hf = font(30, True), font(14)
        text = s.write.text or "…"
        hint = "Pinch to accept. Swipe to cancel."
        bw = max(text_width(tf, text), text_width(hf, hint)) + 56
        rect = _bubble(p, anchor, w, h, bw, 104)
        p.setFont(tf)
        p.setPen(WHITE)
        p.drawText(QRectF(rect.left(), rect.top() + 12, rect.width(), 46), Qt.AlignCenter, text)
        p.setFont(hf)
        p.setPen(with_alpha(WHITE, 0.7))
        p.drawText(QRectF(rect.left(), rect.top() + 62, rect.width(), 28), Qt.AlignCenter, hint)

    if t - s.write_error_t < WRITE_ERROR_MS and s.write_error:
        ef = font(18, True)
        bw = text_width(ef, s.write_error) + 48
        rect = _bubble(p, anchor, w, h, bw, 56, QColor(120, 20, 20, 210))
        p.setFont(ef)
        p.setPen(WHITE)
        p.drawText(rect, Qt.AlignCenter, s.write_error)


# ---------------------------------------------------------------- handshake + access

def draw_handshake(p: QPainter, w: float, h: float, s: UiState) -> None:
    pres = s.presence
    if pres is None or not (0.0 < pres.grant_progress < 1.0):
        return
    c = QPointF(w / 2, h / 2)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(0, 0, 0, 140))
    p.drawEllipse(c, 96, 96)
    draw_ring(p, c, 78, pres.grant_progress, width=9)
    p.setFont(font(15, True))
    p.setPen(WHITE)
    p.drawText(QRectF(c.x() - 90, c.y() - 14, 180, 28), Qt.AlignCenter, "Handing over…")


def draw_access(p: QPainter, w: float, h: float, s: UiState, t: int) -> None:
    if s.access_kind is None:
        return
    age = t - s.access_t
    if age >= ACCESS_TOTAL_MS:
        return
    k = ease_out(age / ACCESS_IN_MS)
    fade = 1.0 if age < ACCESS_TOTAL_MS - 300 else (ACCESS_TOTAL_MS - age) / 300
    text = "Access granted" if s.access_kind == "granted" else "Control returned"
    color = ACCENT if s.access_kind == "granted" else GREEN
    c = QPointF(w / 2, h / 2)
    p.setOpacity(max(0.0, fade))
    # expanding ring
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(with_alpha(color, 1 - k * 0.7), 6))
    p.drawEllipse(c, 60 + 140 * k, 60 + 140 * k)
    f = font(20 + 14 * k, True)
    tw = text_width(f, text)
    card = QRectF(c.x() - tw / 2 - 32, c.y() - 40, tw + 64, 80)
    rounded_card(p, card, 22)
    p.setFont(f)
    p.setPen(color)
    p.drawText(card, Qt.AlignCenter, text)
    p.setOpacity(1.0)


def draw_all(p: QPainter, w: float, h: float, s: UiState, t: int, hud_size, corner) -> None:
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.TextAntialiasing, True)
    draw_ink(p, w, h, s)
    hud = draw_hud(p, w, h, s, hud_size, corner)
    draw_status(p, w, s, t)
    draw_level(p, w, h, s, t)
    draw_write(p, w, h, s, t)
    draw_toast(p, w, h, s, t)
    draw_handshake(p, w, h, s)
    draw_access(p, w, h, s, t)
    draw_hold(p, w, h, s, hud)
    draw_pointer(p, w, h, s)
