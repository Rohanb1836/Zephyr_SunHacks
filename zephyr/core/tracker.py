"""zephyr/core/tracker.py    Stable hand IDs and dropout grace. Owner: Aadharsh.

MediaPipe gives no stable hand ID. Each frame, each detection is matched to the previous hand whose
wrist is nearest (same side preferred), within tracker.max_jump_scales. Unmatched detections get a
new ID. A hand missing for under tracker.dropout_ms keeps its ID and all its state (pose, filters,
detector state); after that it is dropped for good and never handed to another hand.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from zephyr.config import cfg
from zephyr.contracts import Side, dist2d
from zephyr.core import features as F
from zephyr.core.hands import RawHand
from zephyr.core.poses import PoseState

SIDE_FLIP_FRAMES = 3    # MediaPipe's label must disagree this many frames in a row to flip a track's side


class Track:
    def __init__(self, hand_id: int, raw: RawHand, ts_ms: int) -> None:
        self.hand_id = hand_id
        self.side: Side = raw.side
        self._side_votes = 0
        self.lm = raw.landmarks
        self.world = raw.world
        self.score = raw.score
        self.scale = F.raw_scale(raw.landmarks)
        self.first_ms = ts_ms
        self.last_ms = ts_ms
        self.visible = True
        self.poses = PoseState()
        self.state: dict[str, Any] = {}      # detectors keep their per-hand state here

    def update(self, raw: RawHand, ts_ms: int) -> None:
        self.lm, self.world, self.score = raw.landmarks, raw.world, raw.score
        a = float(cfg.hands.get("scale_ema", 0.3))
        self.scale += a * (F.raw_scale(raw.landmarks) - self.scale)
        if raw.side is not self.side:
            self._side_votes += 1
            if self._side_votes >= SIDE_FLIP_FRAMES:
                self.side, self._side_votes = raw.side, 0
        else:
            self._side_votes = 0
        self.last_ms = ts_ms
        self.visible = True

    @property
    def wrist(self) -> np.ndarray:
        return self.lm[0, :2]


class HandTracker:
    def __init__(self) -> None:
        self.tracks: dict[int, Track] = {}
        self._next_id = 1

    def update(self, raws: list[RawHand], ts_ms: int) -> list[Track]:
        """Returns the tracks visible in this frame, in detection order. Also expires old tracks."""
        max_jump = float(cfg.tracker.max_jump_scales)
        cands = []
        for di, r in enumerate(raws):
            for tid, t in self.tracks.items():
                d = dist2d(r.landmarks[0], t.lm[0]) / max(t.scale, 1e-4)
                if d > max_jump:
                    continue
                cost = d + (0.0 if r.side is t.side else 0.75)
                cands.append((cost, di, tid))
        cands.sort()
        used_d, used_t = set(), set()
        assign: dict[int, int] = {}
        for _, di, tid in cands:
            if di in used_d or tid in used_t:
                continue
            assign[di] = tid
            used_d.add(di)
            used_t.add(tid)

        visible: list[Track] = []
        for di, r in enumerate(raws):
            if di in assign:
                t = self.tracks[assign[di]]
                t.update(r, ts_ms)
            else:
                t = Track(self._next_id, r, ts_ms)
                self.tracks[t.hand_id] = t
                self._next_id += 1
            visible.append(t)

        dropout = int(cfg.tracker.dropout_ms)
        seen = {t.hand_id for t in visible}
        for tid in list(self.tracks):
            t = self.tracks[tid]
            if tid not in seen:
                t.visible = False
                if ts_ms - t.last_ms > dropout:
                    del self.tracks[tid]
        return visible

    def alive(self, hand_id: int) -> bool:
        """True if the hand is visible now or inside its dropout grace."""
        return hand_id in self.tracks
