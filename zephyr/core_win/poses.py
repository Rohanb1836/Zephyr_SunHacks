"""zephyr/core/poses.py    Pose classifier with hysteresis and debounce. Owner: Aadharsh.

    OPEN:  all four fingers extended (the thumb only counts when poses.open_needs_thumb is true,
           because a resting thumb is normal on a raised palm)
    FIST:  all four fingers curled
    POINT: index extended; middle, ring, pinky curled (thumb ignored, so a pinch keeps the pose)
    TWO:   index and middle extended; ring and pinky curled
    anything else with score above poses.unknown_min_score: UNKNOWN (custom gestures live here)

Finger hysteresis: extended above poses.extend_deg, curled below poses.curl_deg, in between keep the
previous state. The pinky has its own looser pair (pinky_extend_deg, pinky_curl_deg) because a relaxed
pinky is rarely straight. Debounce: the published pose changes only when the new raw pose wins
debounce_frames of the last `window` frames. Until a hand has debounce_frames frames, publish NONE.
"""
from __future__ import annotations

from collections import Counter, deque

import numpy as np

from zephyr.config import cfg
from zephyr.contracts import Pose
from zephyr.core_win import features as F


class PoseState:
    """Per-hand classifier state. One per tracked hand."""

    def __init__(self) -> None:
        self.fingers: list[bool | None] = [None, None, None, None]   # index, middle, ring, pinky
        self.thumb: bool = False
        self.raw: Pose = Pose.NONE
        self.history: deque[Pose] = deque(maxlen=int(cfg.poses.window))
        self.frames = 0
        self.pose: Pose = Pose.NONE

    def update(self, lm: np.ndarray, world: np.ndarray, scale: float, score: float) -> Pose:
        p = cfg.poses
        ext, curl = float(p.extend_deg), float(p.curl_deg)
        p_ext, p_curl = float(p.get("pinky_extend_deg", ext)), float(p.get("pinky_curl_deg", curl))
        for i, ang in enumerate(F.finger_angles(world)):
            prev = self.fingers[i]
            ext, curl = (p_ext, p_curl) if i == 3 else (float(p.extend_deg), float(p.curl_deg))
            if ang > ext:
                self.fingers[i] = True
            elif ang < curl:
                self.fingers[i] = False
            elif prev is None:
                self.fingers[i] = ang > (ext + curl) / 2
        self.thumb = F.thumb_extended(lm, world, scale, float(p.thumb_deg), float(p.thumb_tip_scales))
        thumb_ok = self.thumb or not bool(p.get("open_needs_thumb", False))
        self.raw = classify(self.fingers, thumb_ok, score, float(p.unknown_min_score))

        win = int(p.window)
        if self.history.maxlen != win:
            self.history = deque(self.history, maxlen=win)
        self.history.append(self.raw)
        self.frames += 1
        need = int(p.debounce_frames)
        if self.frames < need:
            self.pose = Pose.NONE
            return self.pose
        best, count = Counter(self.history).most_common(1)[0]
        if best is not self.pose and count >= need:
            self.pose = best
        elif self.pose is Pose.NONE and count >= need:
            self.pose = best
        return self.pose


def classify(fingers: list, thumb: bool, score: float, unknown_min_score: float) -> Pose:
    i, m, r, k = (bool(f) for f in fingers)
    if i and m and r and k and thumb:
        return Pose.OPEN
    if not (i or m or r or k):
        return Pose.FIST
    if i and not (m or r or k):
        return Pose.POINT
    if i and m and not (r or k):
        return Pose.TWO
    return Pose.UNKNOWN if score > unknown_min_score else Pose.NONE
