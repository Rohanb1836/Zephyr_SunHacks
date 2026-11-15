"""Head pose and debounced attention."""
import math
import statistics

import numpy as np

from zephyr.config import cfg


def yaw_pitch_degrees(matrix):
    r = np.asarray(matrix, dtype=np.float64)[:3, :3]
    return (math.degrees(math.atan2(r[0, 2], r[2, 2])),
            math.degrees(math.asin(max(-1.0, min(1.0, -r[1, 2])))))


class Attention:
    def __init__(self):
        self.samples = []
        self.baseline = None
        self.state = False
        self.changed_at = None

    def update(self, person, now, owner):
        raw = False
        if person is not None and person.face_matrix is not None:
            yaw, pitch = yaw_pitch_degrees(person.face_matrix)
            if owner and self.baseline is None:
                self.samples.append((yaw, pitch))
                if len(self.samples) >= 5:
                    self.baseline = (statistics.median(x for x, _ in self.samples),
                                     statistics.median(y for _, y in self.samples))
            baseline = self.baseline if owner and self.baseline else (0.0, 0.0)
            limit_y = float(cfg.presence.yaw_deg) if owner else 35.0
            limit_p = float(cfg.presence.pitch_deg) if owner else 35.0
            raw = abs(yaw - baseline[0]) < limit_y and abs(pitch - baseline[1]) < limit_p
        elif person is not None and person.face_landmarks and len(person.face_landmarks) > 454:
            lm = person.face_landmarks
            span = lm[454].x - lm[234].x
            if abs(span) > 1e-6:
                raw = 0.3 < (lm[1].x - lm[234].x) / span < 0.7
        if raw == self.state:
            self.changed_at = None
        else:
            if self.changed_at is None:
                self.changed_at = now
            delay = int(cfg.presence.back_ms if raw else cfg.presence.away_ms)
            if now - self.changed_at >= delay:
                self.state = raw
                self.changed_at = None
        return self.state
