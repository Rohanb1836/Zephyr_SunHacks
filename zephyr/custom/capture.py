"""zephyr/custom/capture.py  Recording logic for a new custom gesture. Owner: Swayam.

No Qt in here, so it can be tested with fake frames. The wizard in ui/recorder.py
calls tick() from its timer with bus.latest(T.HANDS).

Rules (Swayam doc section 5.2):
  - capture custom.samples frames from the controller's hand (never a stranger's)
  - reject if fewer than 80 percent of frames had a hand
  - reject if more than 30 percent of frames read as a built-in pose
  - template is the per-coordinate median, threshold from templates.build_template
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from zephyr.contracts import HandObs, HandsFrame, Role
from zephyr.custom.templates import BUILT_IN_POSES, BuiltTemplate, build_template, normalize_hand

MIN_HAND_RATIO = 0.80
MAX_BUILTIN_RATIO = 0.30

POSE_WORDS = {"open": "open hand", "fist": "fist", "point": "pointing finger", "two": "two fingers"}


def controller_hand(frame: Optional[HandsFrame]) -> Optional[HandObs]:
    """The biggest hand that belongs to the owner or the guest."""
    if frame is None:
        return None
    hands = [h for h in frame.hands if h.role is not Role.STRANGER]
    if not hands:
        return None
    return max(hands, key=lambda h: h.scale)


@dataclass
class CaptureResult:
    ok: bool
    message: str
    built: Optional[BuiltTemplate] = None
    preview: Optional[np.ndarray] = None   # (21, 2) image landmarks of one good frame, for drawing


@dataclass
class CaptureSession:
    samples: int
    default_threshold: float
    max_threshold: float = 0.30
    countdown_ms: int = 3000
    timeout_ms: int = 5000           # give up capturing if frames stop arriving
    state: str = "idle"              # idle, countdown, capturing, done
    result: Optional[CaptureResult] = None

    _t0: int = 0
    _cap_t0: int = 0
    _last_frame_id: Optional[int] = None
    _frames_seen: int = 0
    _hand_frames: int = 0
    _builtin: Counter = field(default_factory=Counter)
    _vectors: list = field(default_factory=list)
    _preview: Optional[np.ndarray] = None

    def start(self, now_ms: int) -> None:
        self.state = "countdown"
        self.result = None
        self._t0 = now_ms
        self._last_frame_id = None
        self._frames_seen = 0
        self._hand_frames = 0
        self._builtin = Counter()
        self._vectors = []
        self._preview = None

    def countdown_left(self, now_ms: int) -> int:
        """3, 2, 1 while counting down, 0 otherwise."""
        if self.state != "countdown":
            return 0
        left = self.countdown_ms - (now_ms - self._t0)
        return max(1, int(np.ceil(left / 1000.0))) if left > 0 else 0

    @property
    def progress(self) -> float:
        if self.state == "done":
            return 1.0
        if self.state != "capturing":
            return 0.0
        return min(1.0, self._frames_seen / max(1, self.samples))

    def tick(self, now_ms: int, frame: Optional[HandsFrame]) -> None:
        if self.state == "countdown":
            if now_ms - self._t0 >= self.countdown_ms:
                self.state = "capturing"
                self._cap_t0 = now_ms
                # the frame showing during the countdown does not count
                self._last_frame_id = frame.frame_id if frame is not None else None
            return
        if self.state != "capturing":
            return

        if frame is not None and frame.frame_id != self._last_frame_id:
            self._last_frame_id = frame.frame_id
            self._frames_seen += 1
            hand = controller_hand(frame)
            if hand is not None:
                self._hand_frames += 1
                if hand.pose in BUILT_IN_POSES:
                    self._builtin[hand.pose.value] += 1
                else:
                    self._vectors.append(normalize_hand(hand))
                    if self._preview is None or len(self._vectors) == self.samples // 2:
                        self._preview = np.array(hand.landmarks[:, :2], dtype=np.float32)

        if self._frames_seen >= self.samples or now_ms - self._cap_t0 > self.timeout_ms:
            self._finish()

    def _finish(self) -> None:
        self.state = "done"
        seen = self._frames_seen
        if seen == 0:
            self.result = CaptureResult(False, "No camera frames arrived. Is the camera running?")
            return
        if self._hand_frames / seen < MIN_HAND_RATIO:
            self.result = CaptureResult(False, "Keep your hand in view the whole time, then try again.")
            return
        builtin_total = sum(self._builtin.values())
        if builtin_total / seen > MAX_BUILTIN_RATIO:
            pose = self._builtin.most_common(1)[0][0]
            word = POSE_WORDS.get(pose, pose)
            self.result = CaptureResult(
                False, f"That reads as the built-in {word}. Try a more different shape.")
            return
        if len(self._vectors) < 5:
            self.result = CaptureResult(False, "Not enough clear frames. Try again.")
            return
        built = build_template(self._vectors, self.default_threshold, self.max_threshold)
        self.result = CaptureResult(True, "Got it.", built=built, preview=self._preview)
