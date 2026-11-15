"""zephyr/custom/engine_check.py  Custom gestures through the real engine. Owner: Swayam.

Run:  python -m zephyr.custom.engine_check

No camera, no model. Feeds synthetic hands into Aadharsh's engine (same hand
builder as core/selftest.py), records a gesture from the HANDS frames the engine
publishes, saves it, lets the engine reload the matcher on T.CUSTOM_RELOAD, then
holds it and checks that exactly one custom.<id> gesture fires. Uses a temporary
data folder, so real bindings are not touched.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np

from zephyr.bus import bus
from zephyr.contracts import Role, Side, T
from zephyr.custom import store

RNG = np.random.default_rng(11)


def hand(kind="open", ox=0.5, oy=0.6, s=0.1, side=Side.RIGHT, jitter=0.0):
    """Synthetic RawHand, built the same way as core/selftest.py, plus optional jitter."""
    from zephyr.core.hands import RawHand
    ext = {"open": (1, 1, 1, 1), "fist": (0, 0, 0, 0), "point": (1, 0, 0, 0), "two": (1, 1, 0, 0),
           "rock": (1, 0, 0, 1), "callme": (0, 0, 0, 1)}[kind]
    thumb = kind in ("open", "rock", "callme")
    lm = np.zeros((21, 3), np.float32)
    lm[0] = [ox, oy, 0]
    xs = {5: -0.35, 9: -0.1, 13: 0.15, 17: 0.38}
    mir = 1 if side is Side.RIGHT else -1
    for k in (5, 9, 13, 17):
        lm[k] = [ox + mir * xs[k] * s, oy - 1.0 * s, 0]
    for k, e in zip((5, 9, 13, 17), ext):
        x, y = lm[k][0], lm[k][1]
        if e:
            for j in (1, 2, 3):
                lm[k + j] = [x, y - 0.35 * s * j, 0]
        else:
            lm[k + 1] = [x, y - 0.3 * s, 0]
            lm[k + 2] = [x, y - 0.2 * s, 0]
            lm[k + 3] = [x, y - 0.05 * s, 0]
    lm[1] = [ox - mir * 0.3 * s, oy - 0.3 * s, 0]
    lm[2] = [ox - mir * 0.55 * s, oy - 0.5 * s, 0]
    if thumb:
        lm[3] = [ox - mir * 0.8 * s, oy - 0.7 * s, 0]
        lm[4] = [ox - mir * 1.05 * s, oy - 0.9 * s, 0]
    else:
        lm[3] = [ox - mir * 0.5 * s, oy - 0.75 * s, 0]
        lm[4] = [ox - mir * 0.3 * s, oy - 0.8 * s, 0]
    if jitter:
        lm[:, :2] += RNG.normal(0, jitter * s, (21, 2)).astype(np.float32)
    return RawHand(lm, lm.copy(), side, 0.95)


class OwnerGate:
    """Every hand is the owner and may act (presence is Ryan's, not tested here)."""
    def role_of(self, h): return Role.OWNER
    def can_act(self): return True, "ok"
    def on_arm(self, hs): return True
    def on_disarm(self): pass
    def guest_active(self): return False
    def revoke_guest(self): pass


def main() -> int:
    from zephyr.custom import sync
    sync.disable()  # local only: never touch the cloud from a test
    tmp = Path(tempfile.mkdtemp(prefix="zephyr_engine_check_"))
    store.DATA_DIR = tmp
    store.BINDINGS_PATH = tmp / "bindings.json"
    store.GESTURES_DIR = tmp / "gestures"

    from zephyr.core.engine import Engine
    from zephyr.custom.capture import CaptureSession
    from zephyr.custom.conf import value
    from zephyr.custom.matcher import Matcher

    gestures, holds = [], []
    bus.subscribe(T.GESTURE, lambda m: gestures.append(m.key))
    bus.subscribe(T.HOLD, lambda m: holds.append((m.kind, m.progress)))

    eng = Engine(None, gate=OwnerGate(), matcher=Matcher(), detector=None)
    clock = {"t": 0, "f": 0}

    def run(frames):
        for raws in frames:
            clock["t"] += 33
            clock["f"] += 1
            eng.process_raws(raws, clock["t"], clock["f"])

    fails = []

    def report(name, ok, detail=""):
        print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  {detail}"))
        if not ok:
            fails.append(name)

    # arm with both palms, then hands down
    run([[hand("open", 0.3, side=Side.LEFT), hand("open", 0.7)] for _ in range(36)])
    run([[] for _ in range(10)])
    report("engine armed", eng.mode.value == "armed", eng.mode)

    # record "rock on" from the frames the engine publishes
    def record(kind):
        s = CaptureSession(samples=int(value("custom", "samples")),
                           default_threshold=float(value("custom", "default_threshold")),
                           max_threshold=float(value("custom", "max_threshold")), countdown_ms=0)
        s.start(clock["t"])
        for _ in range(60):
            run([[hand(kind, jitter=0.01)]])
            s.tick(clock["t"], bus.latest(T.HANDS))
            if s.state == "done":
                break
        run([[] for _ in range(10)])
        return s.result

    res = record("rock")
    report("record rock on from engine frames", bool(res and res.ok), res and res.message)
    if not (res and res.ok):
        return 1
    store.add_binding("Rock on", res.built.template, res.built.threshold,
                      {"type": "url", "value": "https://example.com", "label": "Open example.com"})

    # a built-in pose must be refused by the recorder
    bad = record("two")
    report("recorder refuses a built-in pose", bool(bad and not bad.ok and "built-in" in bad.message),
           bad and bad.message)

    # the engine reloads the matcher on CUSTOM_RELOAD
    bus.publish(T.CUSTOM_RELOAD, None)
    gestures.clear()
    holds.clear()
    run([[hand("rock", jitter=0.004)] for _ in range(30)])
    run([[] for _ in range(10)])
    customs = [g for g in gestures if g.startswith("custom.")]
    report("holding rock on fires custom.rock_on once", customs == ["custom.rock_on"], gestures)
    report("hold ring progress published", any(k == "custom" and p > 0.5 for k, p in holds))

    # the same recording works for the left hand (templates mirror left hands)
    gestures.clear()
    run([[hand("rock", side=Side.LEFT, jitter=0.004)] for _ in range(30)])
    run([[] for _ in range(10)])
    report("left hand rock on fires too", [g for g in gestures if g.startswith("custom.")] == ["custom.rock_on"],
           gestures)

    # other shapes must not fire the custom gesture
    gestures.clear()
    for kind in ("callme", "point", "fist", "two"):
        run([[hand(kind, jitter=0.004)] for _ in range(25)])
        run([[] for _ in range(10)])
    report("other shapes never fire custom.rock_on", "custom.rock_on" not in gestures, gestures)

    # deleting the binding stops it
    store.delete_binding("rock_on")
    bus.publish(T.CUSTOM_RELOAD, None)
    gestures.clear()
    run([[hand("rock", jitter=0.004)] for _ in range(30)])
    report("deleted gesture no longer fires", not any(g.startswith("custom.") for g in gestures), gestures)

    print("all passed" if not fails else f"{len(fails)} failed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
