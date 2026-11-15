"""Presence worker; run.py calls start_presence(hub)."""
import threading
import time

from zephyr.bus import bus
from zephyr.config import cfg
from zephyr.contracts import HoldProgress, PersonObs, PresenceState, Role, T
from zephyr.presence.attention import Attention
from zephyr.presence.gate import Gate, now_ms
from zephyr.presence.handshake import Handshake
from zephyr.presence.models import Models
from zephyr.presence.people import PeopleTracker


def start_presence(hub):
    models = Models()  # Fail synchronously so run.py can fall back to its stub.
    gate = Gate()
    threading.Thread(target=_run, args=(hub, gate, models), daemon=True, name="zephyr-presence").start()
    return gate


def _run(hub, gate, models):
    tracker = PeopleTracker()
    handshake = Handshake()
    attention = {}
    generation = -1
    last_frame = -1
    last_ts = -1
    try:
        while True:
            started = time.monotonic()
            got = hub.latest()
            if got is None or got[2] == last_frame:
                time.sleep(0.005)
                continue
            frame, ts, last_frame = got
            ts = max(int(ts), last_ts + 1)
            last_ts = ts
            try:
                poses, faces = models.detect(frame, ts)
                now = now_ms()
                people = tracker.update(poses, faces, now)
                with gate.lock:
                    owner, guest, enrolled = gate.owner_id, gate.guest_id, gate.enrolment
                if generation != enrolled:
                    attention = {}
                    generation = enrolled
                current = {p.person_id: p for p in people}
                controller = guest if guest is not None else owner
                if controller is not None:
                    state = attention.setdefault(controller, Attention())
                    attentive = state.update(current.get(controller), now, controller == owner)
                else:
                    attentive = False
                gate.update(people, attentive, now)
                with gate.lock:
                    owner, guest = gate.owner_id, gate.guest_id
                    attentive = gate.attentive
                granted, progress = handshake.update(people, owner, now, bus.latest(T.HANDS), gate.role_of)
                if granted is not None and guest is None:
                    gate.grant(granted, now)
                    guest = granted
                    attentive = False
                controller = guest if guest is not None else owner
                observations = tuple(PersonObs(p.person_id,
                    Role.OWNER if p.person_id == owner else Role.GUEST if p.person_id == guest else Role.STRANGER,
                    p.nose, p.wrists, p.shoulder_width,
                    attention[p.person_id].state if p.person_id in attention else False) for p in people)
                reason = ("no_owner" if owner is None else "looking_away" if not attentive else
                          "guest_active" if guest is not None else "ok")
                bus.publish(T.PRESENCE, PresenceState(now, observations, owner, controller,
                    None if controller is None else Role.GUEST if guest is not None else Role.OWNER,
                    attentive, progress, reason))
                bus.publish(T.HOLD, HoldProgress("grant", progress))
            except Exception as exc:
                # Do not retain a stale 'looking' state when inference fails.
                gate.update((), False, now_ms())
                print(f"[presence] frame error: {exc!r}")
                time.sleep(0.2)
            delay = 1.0 / max(float(cfg.presence.fps), 1.0) - (time.monotonic() - started)
            if delay > 0:
                time.sleep(delay)
    finally:
        models.close()
