"""Gesture-to-gesture regression tests, shared by the Mac and Windows suites. Owner: Aadharsh.

tools/test_gestures_mac.py and tools/test_gestures.py each call flow_case(this module) with their own
hand(), n() and rig(), so the same moves run through zephyr/core and zephyr/core_win. They started as
the probes in tools/flow_check.py. Tests marked "fails on main" failed on main when they were written;
the rest guard behaviour that was already right.

Synthetic hands only (no camera, no model): they check the engine's rules, not MediaPipe.
"""
import unittest

import numpy as np

from zephyr.bus import bus
from zephyr.config import cfg
from zephyr.contracts import Side, T

_POINTER = []
bus.subscribe(T.POINTER, _POINTER.append)


class _Setting:
    """Set one config value for a test, as editing config.yaml live would, then put it back."""
    def __init__(self, section, key, value):
        self.sec, self.key, self.value = getattr(cfg, section), key, value

    def __enter__(self):
        self.old = self.sec.__dict__.get(self.key)
        self.sec.__dict__[self.key] = self.value

    def __exit__(self, *exc):
        self.sec.__dict__[self.key] = self.old


def flow_case(tg):
    hand, n, rig, RawHand = tg.hand, tg.n, tg.rig, tg.RawHand

    def turned(kind, deg, **kw):
        """hand() rotated about the wrist: 0 upright, 90 lying sideways, 180 hanging fingers down."""
        raw = hand(kind, **kw)
        th = np.radians(deg)
        out = []
        for pts in (raw.landmarks, raw.world):
            lm = pts.copy()
            d = lm[:, :2] - lm[0, :2]
            lm[:, 0] = lm[0, 0] + d[:, 0] * np.cos(th) - d[:, 1] * np.sin(th)
            lm[:, 1] = lm[0, 1] + d[:, 0] * np.sin(th) + d[:, 1] * np.cos(th)
            out.append(lm)
        return RawHand(out[0], out[1], raw.side, raw.score)

    def pointer_hides():
        vis = [p.visible for p in _POINTER]
        return sum(1 for a, b in zip(vis, vis[1:]) if a and not b)

    def swipes_every(r, period):
        """4 open-hand swipes to the right, each followed by a return move, one every period frames."""
        r.run(n(8, lambda i: [hand("open", 0.35)]))
        for _ in range(4):
            r.run(n(5, lambda i: [hand("open", 0.35 + 0.06 * (i + 1))]))
            back = max(period - 5, 5)
            r.run(n(back, lambda i: [hand("open", 0.65 - 0.30 * (i + 1) / back)]))

    class FlowTests(unittest.TestCase):
        def setUp(self):
            _POINTER.clear()

        # ---- pointer stays on after a click ----
        def test_click_that_reads_as_a_fist_keeps_the_pointer(self):
            """Fails on main: the pointer blinked off after the click and had to be found again."""
            r = rig()
            r.run(n(10, lambda i: [hand("point")]))
            r.run(n(10, lambda i: [hand("fpinch")]))
            r.run(n(3, lambda i: [hand("fist")]))
            r.run(n(30, lambda i: [hand("point")]))
            self.assertEqual(r.keys(), ["pinch_tap"])
            self.assertEqual(pointer_hides(), 0)

        def test_three_clicks_in_a_row(self):
            r = rig()
            r.run(n(10, lambda i: [hand("point")]))
            for _ in range(3):
                r.run(n(8, lambda i: [hand("ppinch")]))
                r.run(n(22, lambda i: [hand("point")]))
            self.assertEqual(r.keys(), ["pinch_tap"] * 3)
            self.assertEqual(pointer_hides(), 0)

        # ---- holds.after_release_ms: the open hand after a gesture is not Search ----
        def test_open_hand_after_play_pause_does_not_search(self):
            """Fails on main: the hand left open after a fist tap opened Search."""
            r = rig()
            r.run(n(6, lambda i: [hand("open")]))
            r.run(n(8, lambda i: [hand("fist")]))
            r.run(n(60, lambda i: [hand("open")]))
            self.assertEqual(r.keys(), ["fist_tap"])

        def test_open_hand_after_the_knob_does_not_search(self):
            """Fails on main: letting go of the knob and leaving the hand up opened Search."""
            r = rig()
            r.run(n(20, lambda i: [hand("fist")]))
            r.run(n(20, lambda i: [turned("fist", 2 * (i + 1))]))
            r.run(n(60, lambda i: [hand("open")]))
            self.assertTrue([e for e in r.of(T.LEVEL) if e.delta], "the knob did not turn")
            self.assertNotIn("palm_hold", r.keys())
            self.assertNotIn("fist_tap", r.keys())

        def test_open_hand_after_a_pen_stroke_does_not_search(self):
            """Fails on main: pausing between letters with the hand open opened Search."""
            r = rig(ink=True)
            r.run(n(10, lambda i: [hand("open")]))
            r.run(n(25, lambda i: [hand("ppinch", ox=0.4 + 0.005 * i)]))
            r.run(n(60, lambda i: [hand("open", ox=0.52)]))
            self.assertNotIn("palm_hold", r.keys())

        def test_two_fist_taps_without_search_between(self):
            """Fails on main: fist_tap, palm_hold, fist_tap."""
            r = rig()
            for _ in range(2):
                r.run(n(10, lambda i: [hand("open")]))
                r.run(n(10, lambda i: [hand("fist")]))
                r.run(n(20, lambda i: [hand("open")]))
            self.assertEqual(r.keys(), ["fist_tap", "fist_tap"])

        def test_after_release_is_behind_its_key(self):
            """holds.after_release_ms: 0 brings back the old behaviour."""
            with _Setting("holds", "after_release_ms", 0):
                r = rig()
                r.run(n(6, lambda i: [hand("open")]))
                r.run(n(8, lambda i: [hand("fist")]))
                r.run(n(60, lambda i: [hand("open")]))
                self.assertIn("palm_hold", r.keys())

        def test_palm_hold_with_the_hand_left_up_fires_once(self):
            r = rig()
            r.run(n(120, lambda i: [hand("open")]))
            self.assertEqual(r.keys(), ["palm_hold"])

        # ---- level.idle_park_ms: a resting fist does not lock out the other hand ----
        def test_resting_fist_does_not_block_a_click(self):
            """Fails on main: a fist resting 5 s (chin on a fist) kept the other hand from clicking."""
            r = rig()
            rest = lambda: hand("fist", 0.25, side=Side.LEFT)
            r.run(n(150, lambda i: [rest()]))
            r.run(n(12, lambda i: [rest(), hand("point", 0.7)]))
            r.run(n(8, lambda i: [rest(), hand("ppinch", 0.7)]))
            r.run(n(25, lambda i: [rest(), hand("point", 0.7)]))
            self.assertEqual(r.keys(), ["pinch_tap"])

        def test_resting_fist_does_not_block_a_swipe(self):
            """Fails on main."""
            r = rig()
            rest = lambda: hand("fist", 0.25, side=Side.LEFT)
            r.run(n(150, lambda i: [rest()]))
            r.run(n(8, lambda i: [rest(), hand("open", 0.5)]))
            r.run(n(6, lambda i: [rest(), hand("open", 0.5 + 0.06 * (i + 1))]))
            r.run(n(10, lambda i: [rest(), hand("open", 0.86)]))
            self.assertEqual(r.keys(), ["swipe5.right"])

        # ---- the swipe turn-back rule ----
        def test_swipes_with_a_return_each_about_a_second_apart(self):
            """Fails on main: only the first of 4 swipes fired; the return left later swipes waiting."""
            for period in (36, 30):
                r = rig()
                swipes_every(r, period)
                self.assertEqual(r.keys(), ["swipe5.right"] * 4, f"one swipe every {period * 33} ms")

        def test_swipes_with_a_return_800_ms_apart(self):
            r = rig()
            swipes_every(r, 24)
            self.assertEqual(r.keys(), ["swipe5.right"] * 4)

        def test_swipe_right_then_change_your_mind(self):
            r = rig()
            r.run(n(8, lambda i: [hand("open", 0.35)]))
            r.run(n(5, lambda i: [hand("open", 0.35 + 0.06 * (i + 1))]))
            r.run(n(40, lambda i: [hand("open", 0.65)]))
            r.run(n(5, lambda i: [hand("open", 0.65 - 0.06 * (i + 1))]))
            r.run(n(10, lambda i: [hand("open", 0.35)]))
            self.assertEqual(r.keys(), ["swipe5.right", "swipe5.left"])

        def test_point_swipe_point_brings_the_pointer_back(self):
            r = rig()
            r.run(n(15, lambda i: [hand("point", 0.4)]))
            r.run(n(6, lambda i: [hand("open", 0.4)]))
            r.run(n(5, lambda i: [hand("open", 0.4 + 0.06 * (i + 1))]))
            r.run(n(8, lambda i: [hand("open", 0.7)]))
            r.run(n(15, lambda i: [hand("point", 0.7)]))
            self.assertEqual(r.keys(), ["swipe5.right"])
            self.assertTrue(_POINTER and _POINTER[-1].visible, "the pointer did not come back")

        # ---- hands.rest_tilt_deg: a hanging hand is resting ----
        def test_hanging_open_hand_does_not_search(self):
            """Fails on main: an open hand hanging fingers-down by your side opened Search."""
            r = rig()
            r.run(n(60, lambda i: [turned("open", 175)]))
            self.assertNotIn("palm_hold", r.keys())

        def test_hanging_hand_swinging_does_not_swipe(self):
            """Fails on main: an arm swinging while you walk past read as a swipe."""
            r = rig()
            r.run(n(8, lambda i: [turned("open", 175, ox=0.35)]))
            r.run(n(6, lambda i: [turned("open", 175, ox=0.35 + 0.06 * (i + 1))]))
            r.run(n(10, lambda i: [turned("open", 175, ox=0.71)]))
            self.assertEqual([k for k in r.keys() if k.startswith("swipe")], [])

        def test_rest_tilt_is_behind_its_key(self):
            """hands.rest_tilt_deg: 0 turns the rule off (Search needs the palm upright anyway, so
            this checks the swipe)."""
            with _Setting("hands", "rest_tilt_deg", 0):
                r = rig()
                r.run(n(8, lambda i: [turned("open", 175, ox=0.35)]))
                r.run(n(6, lambda i: [turned("open", 175, ox=0.35 + 0.06 * (i + 1))]))
                r.run(n(10, lambda i: [turned("open", 175, ox=0.71)]))
                self.assertIn("swipe5.right", r.keys())

        # ---- holds.palm_max_tilt_deg: Search needs the palm held up ----
        def test_sideways_palm_does_not_search(self):
            """Fails on main: an open hand lying sideways (resting on the desk edge) opened Search."""
            r = rig()
            r.run(n(60, lambda i: [turned("open", 95)]))
            self.assertNotIn("palm_hold", r.keys())

        def test_slightly_leaning_palm_still_searches(self):
            r = rig()
            r.run(n(60, lambda i: [turned("open", 30)]))
            self.assertEqual(r.keys(), ["palm_hold"])

        def test_palm_tilt_is_behind_its_key(self):
            with _Setting("holds", "palm_max_tilt_deg", 0):
                r = rig()
                r.run(n(60, lambda i: [turned("open", 95)]))
                self.assertIn("palm_hold", r.keys())

    return FlowTests
