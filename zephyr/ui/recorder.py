"""zephyr/ui/recorder.py  Step 2 of the new-gesture wizard: record. Owner: Swayam.

Shows the live hand, counts down 3, 2, 1, captures custom.samples frames from
the controller's hand, then either accepts the recording, warns about a
collision with an existing gesture, or says why it was rejected.
The capture rules live in custom/capture.py so they can be tested without Qt.
"""
from __future__ import annotations

from typing import Callable, Optional

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from zephyr.bus import bus
from zephyr.contracts import Role, T
from zephyr.custom.capture import CaptureResult, CaptureSession, controller_hand
from zephyr.custom.conf import section
from zephyr.ui.style import big_button, label
from zephyr.ui.state import now_ms
from zephyr.ui.widgets import ACCENT, BONES, GREY, WHITE


class LiveHand(QWidget):
    """Big live view of the hand skeleton, with the countdown and capture ring on top."""

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumSize(520, 390)
        self.countdown = 0
        self.progress = 0.0
        self.frozen: Optional[np.ndarray] = None   # (21, 2) captured hand to show after recording
        self.tint = ACCENT

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(4, 4, -4, -4)
        # keep a 4:3 box, like the camera
        if r.width() / r.height() > 4 / 3:
            nw = r.height() * 4 / 3
            r = QRectF(r.center().x() - nw / 2, r.top(), nw, r.height())
        else:
            nh = r.width() * 3 / 4
            r = QRectF(r.left(), r.center().y() - nh / 2, r.width(), nh)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 200))
        p.drawRoundedRect(r, 24, 24)

        if self.frozen is not None:
            self._draw_hand(p, r, self.frozen, self.tint)
        else:
            frame = bus.latest(T.HANDS)
            if frame is not None:
                for hand in frame.hands:
                    color = GREY if hand.role is Role.STRANGER else ACCENT
                    self._draw_hand(p, r, hand.landmarks[:, :2], color)
            if frame is None or controller_hand(frame) is None:
                p.setPen(QColor(255, 255, 255, 120))
                f = p.font()
                f.setPointSize(18)
                p.setFont(f)
                p.drawText(r, Qt.AlignCenter, "Show your hand to the camera")

        if self.countdown:
            f = p.font()
            f.setPointSize(110)
            f.setBold(True)
            p.setFont(f)
            p.setPen(WHITE)
            p.drawText(r, Qt.AlignCenter, str(self.countdown))
        if self.progress > 0:
            c, rad = r.center(), min(r.width(), r.height()) * 0.42
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(ACCENT, 10, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(QRectF(c.x() - rad, c.y() - rad, 2 * rad, 2 * rad), 90 * 16,
                      int(-360 * 16 * self.progress))
        p.end()

    @staticmethod
    def _draw_hand(p: QPainter, r: QRectF, xy: np.ndarray, color: QColor) -> None:
        pts = [QPointF(r.left() + float(x) * r.width(), r.top() + float(y) * r.height()) for x, y in xy]
        p.setPen(QPen(color, 5, Qt.SolidLine, Qt.RoundCap))
        for a, b in BONES:
            p.drawLine(pts[a], pts[b])
        p.setPen(Qt.NoPen)
        p.setBrush(WHITE)
        for pt in pts:
            p.drawEllipse(pt, 5, 5)


class RecordPage(QWidget):
    """Emits recorded(template, threshold) when the user accepts the recording."""

    recorded = Signal(object, float)
    back = Signal()

    def __init__(self, nearest: Callable[[np.ndarray], Optional[tuple[str, str, float]]]) -> None:
        super().__init__()
        cfg = section("custom")
        self._nearest = nearest
        self._collision_limit = 2.0 * float(cfg["default_threshold"])
        self.session = CaptureSession(
            samples=int(cfg["samples"]),
            default_threshold=float(cfg["default_threshold"]),
            max_threshold=float(cfg.get("max_threshold", 0.30)),
            countdown_ms=int(cfg.get("countdown_ms", 3000)),
        )
        self._result: Optional[CaptureResult] = None

        self.step = label("Step 2 of 3 · Record", "step")
        self.title = label("Hold your new hand shape", "title")
        self.hint = label("Pick a shape that is not an open hand, fist, pointing finger or two "
                          "fingers. Hold it still while the ring fills.", "hint")
        self.live = LiveHand()
        self.status = label("", "hint")

        self.btn_record = big_button("Record", "primary")
        self.btn_again = big_button("Record again")
        self.btn_keep = big_button("Use it anyway")
        self.btn_next = big_button("Next", "primary")
        self.btn_back = big_button("Back")

        self.btn_record.clicked.connect(self.start)
        self.btn_again.clicked.connect(self.start)
        self.btn_keep.clicked.connect(self._accept)
        self.btn_next.clicked.connect(self._accept)
        self.btn_back.clicked.connect(self._go_back)

        buttons = QHBoxLayout()
        buttons.setSpacing(24)
        for b in (self.btn_back, self.btn_again, self.btn_keep, self.btn_record, self.btn_next):
            buttons.addWidget(b)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(48, 36, 48, 36)
        lay.setSpacing(18)
        lay.addWidget(self.step)
        lay.addWidget(self.title)
        lay.addWidget(self.hint)
        lay.addWidget(self.live, 1)
        lay.addWidget(self.status)
        lay.addLayout(buttons)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self.reset()

    # ---- flow ----

    def reset(self) -> None:
        self.session.state = "idle"
        self._result = None
        self.live.frozen = None
        self.live.countdown = 0
        self.live.progress = 0.0
        self.status.setObjectName("hint")
        self.status.setText("")
        self._restyle(self.status)
        self._show_buttons(record=True)
        self._timer.start(16)

    def start(self) -> None:
        self._result = None
        self.live.frozen = None
        self.status.setObjectName("hint")
        self.status.setText("Get ready…")
        self._restyle(self.status)
        self._show_buttons()
        self.session.start(now_ms())

    def _tick(self) -> None:
        t = now_ms()
        s = self.session
        if s.state in ("countdown", "capturing"):
            s.tick(t, bus.latest(T.HANDS))
            self.live.countdown = s.countdown_left(t)
            self.live.progress = s.progress if s.state == "capturing" else 0.0
            if s.state == "capturing":
                self.status.setText("Hold it…")
            if s.state == "done":
                self._finished(s.result)
        self.live.update()

    def _finished(self, result: Optional[CaptureResult]) -> None:
        self.live.countdown = 0
        self.live.progress = 0.0
        self._result = result
        if result is None or not result.ok:
            self.status.setObjectName("bad")
            self.status.setText(result.message if result else "Something went wrong. Try again.")
            self._restyle(self.status)
            self._show_buttons(again=True)
            return
        self.live.frozen = result.preview
        near = self._nearest(result.built.template)
        if near is not None and near[2] < self._collision_limit:
            self.status.setObjectName("warn")
            self.status.setText(f"Too close to {near[1]}. Try a more different shape.")
            self.live.tint = QColor(255, 204, 102)
            self._restyle(self.status)
            self._show_buttons(again=True, keep=True)
            return
        self.live.tint = ACCENT
        self.status.setObjectName("good")
        self.status.setText("Got it. That one is yours.")
        self._restyle(self.status)
        self._show_buttons(again=True, next_=True)

    def _accept(self) -> None:
        if self._result is not None and self._result.ok:
            self._timer.stop()
            self.recorded.emit(self._result.built.template, float(self._result.built.threshold))

    def _go_back(self) -> None:
        self._timer.stop()
        self.session.state = "idle"
        self.back.emit()

    # ---- helpers ----

    def _show_buttons(self, record: bool = False, again: bool = False, keep: bool = False,
                      next_: bool = False) -> None:
        self.btn_record.setVisible(record)
        self.btn_again.setVisible(again)
        self.btn_keep.setVisible(keep)
        self.btn_next.setVisible(next_)
        recording = not (record or again or keep or next_)
        self.btn_back.setEnabled(not recording)

    @staticmethod
    def _restyle(widget: QWidget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)
