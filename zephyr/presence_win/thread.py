"""Presence worker and frame processor shared with deterministic clip replay."""
import threading
import time

from zephyr.bus import bus
from zephyr.config import cfg
from zephyr.contracts import HoldProgress, PersonObs, PresenceState, Role, T
from zephyr.presence_win.attention import Attention
from zephyr.presence_win.gate import Gate, now_ms
from zephyr.presence_win.handshake import Handshake
from zephyr.presence_win.people import PeopleTracker


class PresenceProcessor:
    """Process one mirrored frame at a time using a caller-provided timestamp."""

    def __init__(self, gate, models):
        self.gate = gate
        self.models = models
        self.tracker = PeopleTracker()
        self.handshake = Handshake()
        self.attention = {}
        self.generation = -1
        self.last_ts = -1

    def process(self, frame, ts_ms, now, hands=None):
        ts_ms = max(int(ts_ms), self.last_ts + 1)
        self.last_ts = ts_ms
        poses, faces = self.models.detect(frame, ts_ms)
        people = self.tracker.update(poses, faces, now)
        gate = self.gate
        with gate.lock:
            owner, guest, enrolled = gate.owner_id, gate.guest_id, gate.enrolment
            attentive_now = gate.attentive
        if self.generation != enrolled:
            self.attention = {}
            self.generation = enrolled
        current = {p.person_id: p for p in people}
        controller = guest if guest is not None else owner
        if controller is not None:
            if controller not in self.attention:
                # start from the gate's view: True right after arming or taking control back
                self.attention[controller] = Attention(initial=attentive_now)
            attention = self.attention[controller]
            attentive = attention.update(current.get(controller), now, controller == owner)
        else:
            attentive = False
        gate.update(people, attentive, now)
        with gate.lock:
            owner, guest, attentive = gate.owner_id, gate.guest_id, gate.attentive
        granted, progress = self.handshake.update(people, owner, now, hands, gate.role_of)
        if granted is not None and guest is None:
            gate.grant(granted, now)
            with gate.lock:
                guest, attentive = gate.guest_id, gate.attentive
        controller = guest if guest is not None else owner
        observations = tuple(PersonObs(p.person_id,
            Role.OWNER if p.person_id == owner else Role.GUEST if p.person_id == guest else Role.STRANGER,
            p.nose, p.wrists, p.shoulder_width,
            self.attention[p.person_id].state if p.person_id in self.attention else False) for p in people)
        reason = ("no_owner" if owner is None else "looking_away" if not attentive else
                  "guest_active" if guest is not None else "ok")
        state = PresenceState(now, observations, owner, controller,
            None if controller is None else Role.GUEST if guest is not None else Role.OWNER,
            attentive, progress, reason)
        bus.publish(T.PRESENCE, state)
        bus.publish(T.HOLD, HoldProgress("grant", progress))
        return state


def start_presence(hub):
    from zephyr.presence_win.models import Models
    models = Models()  # Fail synchronously so run.py can fall back to its stub.
    gate = Gate()
    threading.Thread(target=_run, args=(hub, gate, models), daemon=True, name="zephyr-presence").start()
    return gate


def _run(hub, gate, models):
    processor = PresenceProcessor(gate, models)
    last_frame = -1
    try:
        while True:
            started = time.monotonic()
            got = hub.latest()
            if got is None or got[2] == last_frame:
                time.sleep(0.005)
                continue
            frame, ts, last_frame = got
            try:
                processor.process(frame, ts, now_ms(), bus.latest(T.HANDS))
            except Exception as exc:
                gate.update((), False, now_ms())
                print(f"[presence] frame error: {exc!r}")
                time.sleep(0.2)
            delay = 1.0 / max(float(cfg.presence.fps), 1.0) - (time.monotonic() - started)
            if delay > 0:
                time.sleep(delay)
    finally:
        models.close()
