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
        self.face_seen = None      # last time a face was found for this person

    def update(self, person, now, owner):
        raw = False
        face = person is not None and (person.face_matrix is not None or
                                       (person.face_landmarks and len(person.face_landmarks) > 454))
        if person is not None and not face:
            # Body in view but no face: almost always a hand in front of the face (a palm hold or
            # a swipe passes right over it), not someone looking away. Keep the last answer for a
            # while. Measured before this: 25 false pauses in 2.5 minutes of normal gesturing.
            hold = int(cfg.presence.get("face_lost_hold_ms", 1200))
            if self.face_seen is not None and now - self.face_seen < hold:
                self.changed_at = None
                return self.state
        if face:
            self.face_seen = now
        if person is not None and person.face_matrix is not None:
            yaw, pitch = yaw_pitch_degrees(person.face_matrix)
            if owner and self.baseline is None:
                self.samples.append((yaw, pitch))
                if len(self.samples) >= int(cfg.presence.get("baseline_samples", 10)):
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
