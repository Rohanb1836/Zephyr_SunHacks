"""Thread-safe gate used by the gesture engine."""
import threading
import time

from zephyr.config import cfg
from zephyr.contracts import Role, dist2d


def now_ms():
    return int(time.monotonic() * 1000)


class Gate:
    def __init__(self, clock=now_ms):
        self.lock = threading.RLock()
        self.clock = clock
        self.people = ()
        self.owner_id = None
        self.guest_id = None
        self.owner_seen = 0
        self.guest_seen = 0
        self.guest_started = 0
        self.attentive = False
        self.cache = {}
        self.enrolment = 0
        self.widths = {}   # person_id -> (largest recent shoulder width, time seen)

    def _reach(self, person):
        """Furthest a wrist of this person can be from their nose in the image. Uses the widest
        shoulders seen in the last few seconds: turning sideways shrinks the 2D shoulder width,
        and that must not make the owner's own hand look out of reach."""
        width = max(person.shoulder_width, self.widths.get(person.person_id, (0.0, 0))[0])
        return float(cfg.presence.get("reach_ratio", 2.5)) * width

    def _within_reach(self, hand, person):
        return dist2d(hand.wrist, person.nose) <= self._reach(person)

    def update(self, people, attentive, now):
        with self.lock:
            if len(self.people) < 2 <= len(people):
                self.cache.clear()
            self.people = tuple(people)
            for p in people:
                old, seen = self.widths.get(p.person_id, (0.0, now))
                # the remembered maximum fades by 10 percent a second, so a wrong large value heals
                old *= 0.9 ** max(0.0, (now - seen) / 1000)
                self.widths[p.person_id] = (max(p.shoulder_width, old), now)
            for pid in [pid for pid, (_, seen) in self.widths.items() if now - seen > 10000]:
                del self.widths[pid]
            ids = {p.person_id for p in people}
            if self.owner_id in ids:
                self.owner_seen = now
            if self.guest_id in ids:
                self.guest_seen = now
            if self.guest_id is not None and (now - self.guest_seen >= int(cfg.access.guest_lost_ms)
                    or now - self.guest_started >= float(cfg.access.guest_timeout_s) * 1000):
                self.guest_id = None
                self.cache.clear()
                self.attentive = False
            else:
                self.attentive = attentive

    def role_of(self, hand):
        if cfg.presence.mode == "off":
            return Role.OWNER
        with self.lock:
            if self.owner_id is None:
                return Role.STRANGER
            controller = self.guest_id if self.guest_id is not None else self.owner_id
            if cfg.presence.mode == "lenient" and len(self.people) == 1 and self.people[0].person_id == controller:
                # one body in view: accept its hands without needing visible body wrists, but only
                # within arm's reach. A second person reaching in from outside the frame has no
                # detected body, and their hand is too far from the owner's nose to be the owner's.
                if not self._within_reach(hand, self.people[0]):
                    return Role.STRANGER
                role = Role.GUEST if self.guest_id is not None else Role.OWNER
                self.cache[hand.hand_id] = (role, self.clock())
                return role
            candidates = [(dist2d(hand.wrist, wrist), p) for p in self.people for wrist in p.wrists]
            if candidates:
                distance, person = min(candidates, key=lambda item: item[0])
                if distance < float(cfg.presence.attach_ratio) * person.shoulder_width and \
                        self._within_reach(hand, person):
                    role = (Role.OWNER if person.person_id == self.owner_id else
                            Role.GUEST if person.person_id == self.guest_id else Role.STRANGER)
                    self.cache[hand.hand_id] = (role, self.clock())
                    return role
            role, stamp = self.cache.get(hand.hand_id, (Role.STRANGER, 0))
            return role if self.clock() - stamp <= 500 else Role.STRANGER

    def can_act(self):
        if cfg.presence.mode == "off":
            return True, "ok"
        with self.lock:
            if self.owner_id is None:
                return False, "no_owner"
            if self.clock() - self.owner_seen >= int(cfg.presence.owner_lost_ms):
                self.on_disarm()
                return False, "owner_left"
            controller = self.guest_id if self.guest_id is not None else self.owner_id
            # A body missing for a frame or two is a detector dropout, not someone leaving:
            # only count it once it has been gone for away_ms.
            seen = self.guest_seen if self.guest_id is not None else self.owner_seen
            present = any(p.person_id == controller for p in self.people) or \
                self.clock() - seen < int(cfg.presence.away_ms)
            if not present or not self.attentive:
                return False, "looking_away"
            return True, "ok"

    def on_arm(self, hands):
        if cfg.presence.mode == "off":
            return True
        with self.lock:
            if not self.people:
                return False
            if len(self.people) == 1:
                owner = self.people[0]
            else:
                candidates = [(dist2d(h.wrist, w), p) for h in hands for p in self.people for w in p.wrists]
                if not candidates:
                    return False
                distance, owner = min(candidates, key=lambda item: item[0])
                if distance >= float(cfg.presence.attach_ratio) * owner.shoulder_width:
                    return False
            self.owner_id = owner.person_id
            self.owner_seen = self.clock()
            self.guest_id = None
            self.cache.clear()
            self.enrolment += 1
            # they just showed both palms to the camera: they are looking. Starting at False showed
            # "Paused: looking away" for about 0.4 s after every arm.
            self.attentive = True
            return True

    def on_disarm(self):
        with self.lock:
            self.owner_id = self.guest_id = None
            self.cache.clear()
            self.attentive = False

    def guest_active(self):
        with self.lock:
            return self.guest_id is not None

    def revoke_guest(self):
        with self.lock:
            self.guest_id = None
            self.cache.clear()
            self.attentive = True   # the owner just took control back with both palms, facing the camera

    def grant(self, person_id, now):
        with self.lock:
            if self.owner_id is not None and any(p.person_id == person_id for p in self.people):
                self.guest_id = person_id
                self.guest_seen = self.guest_started = now
                self.cache.clear()
                self.attentive = False
