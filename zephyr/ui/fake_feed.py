"""zephyr/ui/fake_feed.py  Fake bus messages for testing the overlay alone. Owner: Swayam.

Run:  python -m zephyr.ui.fake_feed
It plays a 30 second loop through every overlay element (status pill, app chip,
hold ring, pointer, toasts, sliders, ink, preview bubble, stranger hand,
handshake ring, access granted) so each one can be checked against the
"reacts within one frame" acceptance test (S4, S5, S8, S9) before the real
engine exists. Use --once to stop after one loop.
"""
from __future__ import annotations

import argparse
import math
import threading
import time

import numpy as np

from zephyr.bus import bus
from zephyr.contracts import (ActionFired, ContextState, HandObs, HandsFrame, HoldProgress,
                              LevelValue, PersonObs, PointerEvent, Pose, PresenceState, Role, Side,
                              StrokeEvent, SystemMode, SystemState, T, WriteState)
from zephyr.custom.selftest import fake_world


def _hand(cx: float, cy: float, role: Role = Role.OWNER, hand_id: int = 1,
          pose: Pose = Pose.UNKNOWN, shape: str = "rock_on") -> HandObs:
    rng = np.random.default_rng(int(time.time() * 1000) % 100000)
    world = fake_world(shape, rng, noise=0.001)
    lm = np.zeros((21, 3), np.float32)
    lm[:, 0] = cx + world[:, 0] * 2.2
    lm[:, 1] = cy + world[:, 1] * 2.2
    return HandObs(hand_id=hand_id, side=Side.RIGHT, landmarks=lm, world=world, scale=0.12,
                   pose=pose, pinch=1.0, score=0.95, role=role)


def _presence(role: Role, grant: float = 0.0, reason: str = "ok") -> PresenceState:
    owner = PersonObs(1, Role.OWNER, (0.5, 0.3), ((0.4, 0.7),), 0.3, True)
    return PresenceState(ts_ms=0, people=(owner,), owner_id=1,
                         controller_id=1 if role is Role.OWNER else 2,
                         controller_role=role, controller_attentive=True,
                         grant_progress=grant, reason=reason)


def play(stop: threading.Event, once: bool) -> None:
    ctx = [ContextState("com.google.Chrome", "Chrome", "chrome", False),
           ContextState("com.spotify.client", "Spotify", "spotify", False),
           ContextState("com.microsoft.Word", "Word", "word", True)]
    frame_id = 0
    start = time.monotonic()
    stroke_id = 0
    while not stop.is_set():
        t = time.monotonic() - start
        loop = t % 30.0
        frame_id += 1

        # hands: owner hand drifts; a stranger hand appears from 20 to 24 s
        hands = [_hand(0.5 + 0.08 * math.sin(t), 0.55 + 0.04 * math.cos(t * 1.3))]
        if 20 <= loop < 24:
            hands.append(_hand(0.22, 0.6, role=Role.STRANGER, hand_id=2, shape="three"))
        bus.publish(T.HANDS, HandsFrame(frame_id, int(t * 1000), tuple(hands)))

        # system and presence
        if loop < 2:
            bus.publish(T.SYSTEM, SystemState(SystemMode.DISARMED))
            bus.publish(T.HOLD, HoldProgress("arm_toggle", min(1.0, loop / 1.0) if loop > 1 else 0.0, 1))
            bus.publish(T.PRESENCE, _presence(Role.OWNER))
        elif 21 <= loop < 22.5:
            bus.publish(T.SYSTEM, SystemState(SystemMode.SUSPENDED, "looking_away"))
        elif 24 <= loop < 25:
            bus.publish(T.PRESENCE, _presence(Role.OWNER, grant=min(0.99, loop - 24)))
        elif 25 <= loop < 28:
            bus.publish(T.SYSTEM, SystemState(SystemMode.ARMED))
            bus.publish(T.PRESENCE, _presence(Role.GUEST))
        else:
            bus.publish(T.SYSTEM, SystemState(SystemMode.ARMED))
            bus.publish(T.PRESENCE, _presence(Role.OWNER))
        # like presence/thread.py: a "grant" hold goes out with every presence update, even at 0
        pres = bus.latest(T.PRESENCE)
        bus.publish(T.HOLD, HoldProgress("grant", pres.grant_progress if pres else 0.0))

        # app chip changes every 4 s from 2 to 14 s
        if 2 <= loop < 14:
            bus.publish(T.CONTEXT, ctx[int((loop - 2) // 4) % 3])

        # toasts at 3, 7 and 11 s (one failed)
        for at, label, app, ok in ((3, "Next tab", "Chrome", True), (7, "Next track", "Spotify", True),
                                   (11, "Could not open", "Word", False)):
            if at <= loop < at + 0.034:
                bus.publish(T.ACTION, ActionFired(label, app, ok))

        # pointer from 4 to 8 s, frozen in the middle
        if 4 <= loop < 8:
            bus.publish(T.POINTER, PointerEvent(True, 0.5 + 0.25 * math.cos(t * 2), 0.45 + 0.2 * math.sin(t * 2),
                                                frozen=5.5 <= loop < 6.0))
        else:
            bus.publish(T.POINTER, PointerEvent(False))

        # custom hold ring at 8 to 9 s
        if 8 <= loop < 9:
            bus.publish(T.HOLD, HoldProgress("custom", loop - 8, 1))
        elif 9 <= loop < 9.05:
            bus.publish(T.HOLD, HoldProgress("custom", 0.0, 1))

        # volume slider 9 to 11 s, brightness 12 to 13 s
        if 9 <= loop < 11:
            bus.publish(T.LEVEL_VALUE, LevelValue("volume", 40 + 25 * (loop - 9)))
        if 12 <= loop < 13:
            bus.publish(T.LEVEL_VALUE, LevelValue("brightness", 70 - 30 * (loop - 12)))

        # air writing 14 to 20 s: ink, spinner, preview, accepted
        if 14 <= loop < 17:
            phase = "begin" if loop < 14.034 else "point"
            if phase == "begin":
                stroke_id += 1
            x = 0.35 + 0.3 * (loop - 14) / 3
            y = 0.45 + 0.05 * math.sin((loop - 14) * 9)
            bus.publish(T.STROKE, StrokeEvent(phase, stroke_id, x, y, int(t * 1000)))
            bus.publish(T.WRITE, WriteState("inking"))
        elif 17 <= loop < 18:
            bus.publish(T.WRITE, WriteState("transcribing", anchor=(0.5, 0.55)))
        elif 18 <= loop < 19.5:
            bus.publish(T.WRITE, WriteState("preview", "Spotify", anchor=(0.5, 0.55)))
        elif 19.5 <= loop < 20:
            bus.publish(T.WRITE, WriteState("accepted"))
        else:
            bus.publish(T.WRITE, WriteState("idle"))

        if once and t > 30:
            stop.set()
            break
        time.sleep(1 / 30)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--no-settings", action="store_true")
    args = ap.parse_args()
    stop = threading.Event()
    threading.Thread(target=play, args=(stop, args.once), daemon=True, name="fake-feed").start()
    from zephyr.ui.app import run_ui
    code = run_ui(open_settings=not args.no_settings)
    stop.set()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
