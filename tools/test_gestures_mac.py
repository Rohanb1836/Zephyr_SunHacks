"""Regression tests for the Mac engine's loosened limits (zephyr/core, config.yaml). Owner: Aadharsh.

    python tools/test_gestures_mac.py

The Mac twin of tools/test_gestures.py: the same synthetic hands through the real tracker, pose
classifier and engine (no camera, no model), for the behaviours the Mac engine shares with the
Windows-tuned one. Run it with python -m zephyr.core.selftest after changing zephyr/core or the core
sections of config.yaml. Does not run on Windows (config_windows.yaml would be laid over the values).
"""
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from zephyr.bus import bus  # noqa: E402
from zephyr.config import cfg  # noqa: E402
from zephyr.contracts import ContextState, Role, Side, SystemMode, T, WriteState  # noqa: E402
from zephyr.core.engine import Engine  # noqa: E402
from zephyr.core.hands import RawHand  # noqa: E402

FINGERS = {"open": (1, 1, 1, 1), "fist": (0, 0, 0, 0), "point": (1, 0, 0, 0), "ppinch": (1, 0, 0, 0),
           "two": (1, 1, 0, 0), "tpinch": (1, 1, 0, 0), "fpinch": (0, 0, 0, 0),
           "relax": (0, 0, 1, 1), "lazy": (1, 1, 1, 0)}


def hand(kind="open", ox=0.5, oy=0.6, s=0.1, side=Side.RIGHT, width=1.0):
    """Same construction as zephyr/core/selftest.py. width < 1 narrows the palm (hand turned edge-on)."""
    ext = FINGERS[kind]
    lm = np.zeros((21, 3), np.float32)
    lm[0] = [ox, oy, 0]
    xs = {5: -0.35, 9: -0.1, 13: 0.15, 17: 0.38}
    mir = 1 if side is Side.RIGHT else -1
    for k in (5, 9, 13, 17):
        lm[k] = [ox + mir * xs[k] * s * width, oy - 1.0 * s, 0]
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
    if kind in ("open", "relax"):
        lm[3] = [ox - mir * 0.8 * s, oy - 0.7 * s, 0]
        lm[4] = [ox - mir * 1.05 * s, oy - 0.9 * s, 0]
    else:
        lm[3] = [ox - mir * 0.5 * s, oy - 0.75 * s, 0]
        lm[4] = [ox - mir * 0.3 * s, oy - 0.8 * s, 0]
    if kind in ("ppinch", "tpinch", "fpinch"):
        lm[4] = lm[8] + np.array([0.01 * s, 0, 0], np.float32)
        lm[3] = (lm[2] + lm[4]) / 2
    return RawHand(lm, lm.copy(), side, 0.95)


class Owner:
    def role_of(self, h): return Role.OWNER
    def can_act(self): return True, "ok"
    def on_arm(self, hs): return True
    def on_disarm(self): pass
    def guest_active(self): return False
    def revoke_guest(self): pass


class Rig:
    """An armed engine plus everything it published."""
    def __init__(self):
        self.eng = Engine(None, gate=Owner(), detector=None)
        self.eng.mode = SystemMode.ARMED
        self.t, self.fid = 0, 0
        self.events = []

    def run(self, frames, dt=33):
        for raws in frames:
            self.t += dt
            self.fid += 1
            self.eng.process_raws(raws, self.t, self.fid)

    def keys(self):
        return [m.key for top, m in self.events if top == T.GESTURE]

    def of(self, topic):
        return [m for top, m in self.events if top == topic]


RIG = [None]
for _topic in (T.GESTURE, T.LEVEL, T.STROKE):
    bus.subscribe(_topic, lambda m, _topic=_topic: RIG[0] and RIG[0].events.append((_topic, m)))


def rig(ink=False, preview=False):
    bus.publish(T.CONTEXT, ContextState("app", "App", "default", ink) if ink else None)
    bus.publish(T.WRITE, WriteState("preview", "hello") if preview else None)
    bus.publish(T.PRESENCE, None)
    RIG[0] = Rig()
    return RIG[0]


def n(count, fn):
    return [fn(i) for i in range(count)]


class PinchTests(unittest.TestCase):
    def test_a_slow_click_still_clicks(self):
        """Measured clicks were held 470 to 870 ms; the old 300 ms limit missed 10 of 10."""
        r = rig()
        r.run(n(10, lambda i: [hand("point")]))
        r.run(n(18, lambda i: [hand("ppinch")]))          # about 600 ms
        r.run(n(20, lambda i: [hand("point")]))
        taps = [m for m in r.of(T.GESTURE) if m.key == "pinch_tap"]
        self.assertEqual(len(taps), 1)
        self.assertIsNotNone(taps[0].pos)

    def test_a_long_hold_is_not_a_click(self):
        r = rig()
        r.run(n(10, lambda i: [hand("point")]))
        r.run(n(40, lambda i: [hand("ppinch")]))          # 1.3 s
        r.run(n(5, lambda i: [hand("point")]))
        self.assertNotIn("pinch_tap", r.keys())

    def test_flicker_from_a_resting_hand_never_taps(self):
        """pinch.open_before_ms: a gripping hand flickered across the pinch thresholds (64 taps in a
        minute on Windows before this). On the GitHub landmark set, 4% of plain pointing hands
        already read as a pinch."""
        r = rig()
        r.run(n(10, lambda i: [hand("point")]))
        r.run(n(40, lambda i: [hand("ppinch" if i % 2 else "point")]))
        self.assertLessEqual(r.keys().count("pinch_tap"), 1)

    def test_where_ink_is_on_a_still_pinch_clicks_and_a_moving_pinch_writes(self):
        r = rig(ink=True)
        r.run(n(10, lambda i: [hand("point")]))
        r.run(n(15, lambda i: [hand("ppinch")]))
        r.run(n(20, lambda i: [hand("point")]))
        self.assertEqual(r.keys().count("pinch_tap"), 1)
        self.assertEqual(r.of(T.STROKE), [])
        r = rig(ink=True)
        r.run(n(10, lambda i: [hand("point")]))
        r.run(n(25, lambda i: [hand("ppinch", ox=0.5 + 0.004 * i)]))
        r.run(n(5, lambda i: [hand("point", ox=0.6)]))
        phases = [s.phase for s in r.of(T.STROKE)]
        self.assertTrue(phases and phases[0] == "begin" and phases[-1] == "end")
        self.assertNotIn("pinch_tap", r.keys())


    def test_a_click_that_reads_as_a_fist_still_clicks(self):
        """While pointing, the index bending to the thumb curls the last straight finger: a fist."""
        r = rig()
        r.run(n(10, lambda i: [hand("point")]))
        r.run(n(4, lambda i: [hand("fist")]))             # the index dips: reads as a fist
        r.run(n(6, lambda i: [hand("fpinch")]))           # thumb and index meet
        r.run(n(8, lambda i: [hand("point")]))
        taps = [m for m in r.of(T.GESTURE) if m.key == "pinch_tap"]
        self.assertEqual(len(taps), 1)
        self.assertIsNotNone(taps[0].pos)
        self.assertEqual(r.of(T.LEVEL), [])

    def test_a_resting_hand_holds_the_pointer_still(self):
        """pointer.deadband: landmark jitter of a pixel or so must not move the pointer."""
        ptr = []
        bus.subscribe(T.POINTER, ptr.append)
        r = rig()
        r.run(n(25, lambda i: [hand("point")]))            # the filter settles
        ptr.clear()
        r.run(n(30, lambda i: [hand("point", ox=0.5 + 0.0004 * (-1) ** i)]))
        self.assertEqual(len({(p.x, p.y) for p in ptr if p.visible}), 1)
        r.run(n(10, lambda i: [hand("point", ox=0.5 + 0.01 * (i + 1))]))   # a real move still follows
        self.assertGreater(ptr[-1].x, ptr[0].x + 0.1)


class FistTests(unittest.TestCase):
    def test_a_half_second_fist_is_a_tap(self):
        """A natural quick fist lasts about half a second, past the 400 ms knob grab."""
        r = rig()
        r.run(n(6, lambda i: [hand("open")]))
        r.run(n(17, lambda i: [hand("fist")]))            # about 560 ms
        r.run(n(8, lambda i: [hand("open")]))
        self.assertEqual(r.keys().count("fist_tap"), 1)
        self.assertFalse([e for e in r.of(T.LEVEL) if e.delta])

    def test_a_slow_fist_is_still_a_tap(self):
        """Measured "quick" fists were held 1.2 to 2.1 s."""
        r = rig()
        r.run(n(6, lambda i: [hand("open")]))
        r.run(n(50, lambda i: [hand("fist")]))            # 1.65 s
        r.run(n(8, lambda i: [hand("open")]))
        self.assertEqual(r.keys().count("fist_tap"), 1)

    def test_a_long_held_fist_is_not_a_tap(self):
        r = rig()
        r.run(n(6, lambda i: [hand("open")]))
        r.run(n(95, lambda i: [hand("fist")]))            # 3.1 s
        r.run(n(8, lambda i: [hand("open")]))
        self.assertNotIn("fist_tap", r.keys())

    def test_a_fist_that_opens_into_a_swipe_is_not_a_tap(self):
        """A fist resting between swipes opened straight into the next swipe and fired play/pause."""
        r = rig()
        r.run(n(6, lambda i: [hand("open", ox=0.3)]))
        r.run(n(30, lambda i: [hand("fist", ox=0.3)]))
        r.run(n(6, lambda i: [hand("open", ox=0.3 + 0.05 * (i + 1))]))
        self.assertNotIn("fist_tap", r.keys())

    def test_no_fist_tap_when_a_pen_stroke_ends(self):
        """A writing hand reads as a fist, and letting go fired play/pause."""
        r = rig(ink=True)
        r.run(n(10, lambda i: [hand("open")]))
        r.run(n(12, lambda i: [hand("ppinch", ox=0.5 + 0.004 * i)]))   # the stroke starts
        r.run(n(12, lambda i: [hand("fpinch", ox=0.548 + 0.004 * i)]))  # still writing, reads as a fist
        # lets go as measured live: the hand relaxes to index and middle bent, ring and pinky
        # straight, and the fist reading flickers for a few frames on the way
        r.run([[hand(k, ox=0.6)] for k in ("relax", "fist", "relax", "fist", "relax", "fist")])
        r.run(n(12, lambda i: [hand("relax", ox=0.6)]))
        self.assertTrue(r.of(T.STROKE))
        self.assertNotIn("fist_tap", r.keys())

    def test_no_fist_tap_right_after_a_pinch(self):
        """A pinching hand reads as a fist; letting go must not fire play/pause."""
        r = rig()
        r.run(n(10, lambda i: [hand("point")]))
        r.run(n(8, lambda i: [hand("ppinch")]))
        r.run(n(5, lambda i: [hand("fist")]))
        r.run(n(8, lambda i: [hand("open")]))
        self.assertNotIn("fist_tap", r.keys())


    def test_a_landmark_glitch_on_the_fist_does_not_move_the_level(self):
        """level.knob_max_step_deg: a wrist cannot turn 90 degrees in one frame."""
        def turned(deg):
            raw = hand("fist")
            th = np.radians(deg)
            lm = raw.landmarks.copy()
            d = lm[:, :2] - lm[0, :2]
            lm[:, 0] = lm[0, 0] + d[:, 0] * np.cos(th) - d[:, 1] * np.sin(th)
            lm[:, 1] = lm[0, 1] + d[:, 0] * np.sin(th) + d[:, 1] * np.cos(th)
            return RawHand(lm, raw.world, raw.side, raw.score)
        r = rig()
        r.run(n(20, lambda i: [hand("fist")]))            # the knob is grabbed
        r.run([[turned(90)]])                             # one glitched frame
        r.run(n(20, lambda i: [hand("fist")]))
        self.assertFalse([e for e in r.of(T.LEVEL) if e.delta])
        r.run(n(20, lambda i: [turned(2 * (i + 1))]))     # a real twist still works
        self.assertGreater(sum(e.delta for e in r.of(T.LEVEL)), 20)

    def test_a_fist_opening_into_a_relaxed_hand_is_still_a_tap(self):
        """A relaxed open hand has a bent pinky and thumb and does not read as a clean open hand."""
        r = rig()
        r.run(n(6, lambda i: [hand("open")]))
        r.run(n(8, lambda i: [hand("fist")]))
        r.run(n(8, lambda i: [hand("lazy")]))
        self.assertEqual(r.keys().count("fist_tap"), 1)


class PalmTests(unittest.TestCase):
    def test_palm_hold_needs_the_palm_shown_flat(self):
        """Accidental Search came from edge-on hands (palm width 0.16 and 0.30; real holds 0.66+)."""
        r = rig()
        r.run(n(40, lambda i: [hand("open", width=0.35)]))
        self.assertNotIn("palm_hold", r.keys())
        r = rig()
        r.run(n(40, lambda i: [hand("open")]))
        self.assertEqual(r.keys().count("palm_hold"), 1)

class SwipeAndTrackerTests(unittest.TestCase):
    def test_a_swipe_lost_to_motion_blur_still_counts_once(self):
        """The tracker lost fast swipes for 160 to 450 ms and found the hand far to the side."""
        r = rig()
        r.run(n(10, lambda i: [hand("open", ox=0.40)]))
        r.run(n(7, lambda i: []))                          # 231 ms blurred out
        r.run(n(6, lambda i: [hand("open", ox=0.65)]))   # 3.3 hand widths (measured: 1.5 to 2.7)
        r.run(n(6, lambda i: []))                          # the return stroke, also blurred
        r.run(n(10, lambda i: [hand("open", ox=0.40)]))
        swipes = [k for k in r.keys() if k.startswith("swipe")]
        self.assertEqual(swipes, ["swipe5.right"])

    def test_a_gentle_two_finger_swipe_counts_but_not_an_open_hand_one(self):
        """Measured two-finger swipes travelled 0.8-1.4 hand widths, under the open-hand 1.5."""
        r = rig()
        r.run(n(8, lambda i: [hand("two", ox=0.40)]))
        r.run(n(5, lambda i: [hand("two", ox=0.40 + 0.0195 * (i + 1))]))   # 1.3 widths in 165 ms
        r.run(n(8, lambda i: [hand("two", ox=0.4975)]))
        self.assertEqual([k for k in r.keys() if k.startswith("swipe")], ["swipe2.right"])
        r = rig()
        r.run(n(8, lambda i: [hand("open", ox=0.40)]))
        r.run(n(5, lambda i: [hand("open", ox=0.40 + 0.0195 * (i + 1))]))
        r.run(n(8, lambda i: [hand("open", ox=0.4975)]))
        self.assertFalse([k for k in r.keys() if k.startswith("swipe")])

    def test_a_hand_that_leaves_for_long_is_not_a_swipe(self):
        r = rig()
        r.run(n(10, lambda i: [hand("open", ox=0.40)]))
        r.run(n(20, lambda i: []))                          # 660 ms: put down, raised elsewhere
        r.run(n(10, lambda i: [hand("open", ox=0.75)]))
        self.assertFalse([k for k in r.keys() if k.startswith("swipe")])

    def test_the_other_hand_does_not_inherit_a_lost_hand(self):
        r = rig()
        r.run(n(5, lambda i: [hand("open", side=Side.RIGHT)]))
        first = set(r.eng.ctxs)
        r.run(n(3, lambda i: []))
        r.run(n(5, lambda i: [hand("open", side=Side.LEFT)]))
        self.assertTrue(set(r.eng.ctxs) - first, "the left hand got the right hand's id")


# gesture-to-gesture tests, shared with the other engine's suite (tools/flow_tests.py)
from types import SimpleNamespace as _NS  # noqa: E402
from flow_tests import flow_case  # noqa: E402
FlowTests = flow_case(_NS(hand=hand, n=n, rig=rig, RawHand=RawHand))


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    unittest.main(verbosity=1)
