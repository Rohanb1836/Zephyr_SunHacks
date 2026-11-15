"""Detect deliberate contact between the owner and another person."""
from zephyr.config import cfg
from zephyr.contracts import Pose, Role, dist2d


class Handshake:
    def __init__(self):
        self.candidate = None
        self.started = None
        self.missed = 0
        self.cooldown_until = 0

    def update(self, people, owner_id, now, hands=None, role_of=None):
        if now < self.cooldown_until or owner_id is None:
            self.candidate, self.started, self.missed = None, None, 0
            return None, 0.0
        owner = next((p for p in people if p.person_id == owner_id), None)
        candidate = None
        if owner:
            for other in people:
                if other.person_id == owner_id:
                    continue
                if cfg.access.gesture == "fistbump":
                    if hands is None or role_of is None:
                        continue
                    own = [h for h in hands.hands if h.pose == Pose.FIST and role_of(h) == Role.OWNER]
                    foreign = [h for h in hands.hands if h.pose == Pose.FIST and
                               any(dist2d(h.wrist, w) < float(cfg.presence.attach_ratio) * other.shoulder_width
                                   for w in other.wrists)]
                    close = any(dist2d(a.landmarks[9], b.landmarks[9]) < 0.8 * min(a.scale, b.scale)
                                for a in own for b in foreign)
                else:
                    close = any(dist2d(a, b) < float(cfg.access.wrist_ratio) * owner.shoulder_width
                                and min(owner.nose[0], other.nose[0]) <= (a[0] + b[0]) / 2 <= max(owner.nose[0], other.nose[0])
                                for a in owner.wrists for b in other.wrists)
                if close:
                    candidate = other.person_id
                    break
        if candidate is None:
            if self.started is not None and self.missed < 2:
                self.missed += 1
                duration = 300 if cfg.access.gesture == "fistbump" else int(cfg.access.hold_ms)
                return None, min(1.0, (now - self.started) / duration)
            self.candidate, self.started, self.missed = None, None, 0
            return None, 0.0
        if candidate != self.candidate:
            self.candidate, self.started = candidate, now
        self.missed = 0
        duration = 300 if cfg.access.gesture == "fistbump" else int(cfg.access.hold_ms)
        progress = min(1.0, (now - self.started) / duration)
        if progress >= 1:
            self.cooldown_until = now + 3000
            self.candidate, self.started = None, None
            return candidate, 1.0
        return None, progress
