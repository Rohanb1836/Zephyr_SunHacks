"""zephyr/core/hands.py    MediaPipe HandLandmarker wrapper (Tasks API, VIDEO mode). Owner: Aadharsh.

Runs in the vision thread. Takes a mirrored BGR frame, returns raw detections:
image-space landmarks (normalized, for positions) and world landmarks (metres, for angles).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from zephyr.config import ROOT, cfg
from zephyr.contracts import Side


@dataclass
class RawHand:
    landmarks: np.ndarray   # (21, 3) float32, normalized image coords (mirrored frame)
    world: np.ndarray       # (21, 3) float32, metres
    side: Side
    score: float


class HandDetector:
    def __init__(self) -> None:
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions
        from mediapipe.tasks.python import vision

        self._mp = mp
        h = cfg.hands
        model = Path(h.get("model", "models/hand_landmarker.task"))
        if not model.is_absolute():
            model = ROOT / model
        if not model.exists():
            raise FileNotFoundError(f"{model} missing. Run: python tools/get_models.py")
        opts = vision.HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(model)),
            running_mode=vision.RunningMode.VIDEO,
            num_hands=int(h.max_hands),
            min_hand_detection_confidence=float(h.det_conf),
            min_hand_presence_confidence=float(h.get("presence_conf", 0.5)),
            min_tracking_confidence=float(h.track_conf),
        )
        self._lm = vision.HandLandmarker.create_from_options(opts)
        self._last_ts = -1

    def detect(self, bgr: np.ndarray, ts_ms: int) -> list[RawHand]:
        # detect_for_video raises if a timestamp repeats or goes backwards.
        if ts_ms <= self._last_ts:
            ts_ms = self._last_ts + 1
        self._last_ts = ts_ms
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        img = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        res = self._lm.detect_for_video(img, ts_ms)
        out: list[RawHand] = []
        swap = bool(cfg.hands.get("swap_sides", False))
        for i, lms in enumerate(res.hand_landmarks or []):
            lm = np.array([[p.x, p.y, p.z] for p in lms], dtype=np.float32)
            if res.hand_world_landmarks and i < len(res.hand_world_landmarks):
                wl = np.array([[p.x, p.y, p.z] for p in res.hand_world_landmarks[i]], dtype=np.float32)
            else:
                wl = lm.copy()
            label, score = "Right", 1.0
            if res.handedness and i < len(res.handedness) and res.handedness[i]:
                cat = res.handedness[i][0]
                label, score = cat.category_name, float(cat.score)
            side = Side.LEFT if label.lower().startswith("l") else Side.RIGHT
            if swap:
                side = Side.RIGHT if side is Side.LEFT else Side.LEFT
            out.append(RawHand(lm, wl, side, score))
        return out

    def close(self) -> None:
        try:
            self._lm.close()
        except Exception:
            pass
