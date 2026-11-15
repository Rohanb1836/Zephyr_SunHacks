"""Checks for the Mac presence gate (zephyr/presence): looking away, dropouts, arming. Owner: Ryan.

    python tools/test_presence_mac.py

The Mac twin of tools/test_presence_replay.py, for the fixes the Mac shares with presence_win.
"""
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from zephyr.config import cfg
from zephyr.contracts import Role, SystemMode
from zephyr.presence.gate import Gate
from zephyr.presence.people import Person
from zephyr.presence.thread import PresenceProcessor


class PresenceReplayTests(unittest.TestCase):
    def test_gate_uses_clip_time_for_cache_and_owner_loss(self):
        clock = [1000]
        gate = Gate(clock=lambda: clock[0])
        person = Person(1, (0.3, 0.3), ((0.4, 0.5),), 0.4)
        gate.update((person,), True, clock[0])
        self.assertTrue(gate.on_arm(()))
        gate.update((person,), True, clock[0])
        self.assertEqual(gate.can_act(), (True, "ok"))
        gate.update((), False, clock[0])
        clock[0] += int(cfg.presence.owner_lost_ms)
        self.assertEqual(gate.can_act(), (False, "owner_left"))

    def _hand(self, hand_id, wrist):
        import numpy as np
        lm = np.zeros((21, 3), np.float32)
        lm[0, 0], lm[0, 1] = wrist
        return SimpleNamespace(hand_id=hand_id, wrist=wrist, landmarks=lm)

    def test_people_keep_their_id_through_a_short_dropout(self):
        from zephyr.presence.people import PeopleTracker
        point = lambda x, y: SimpleNamespace(x=x, y=y, visibility=1.0)
        body = [point(0.3, 0.3) for _ in range(17)]
        body[11], body[12] = point(0.2, 0.5), point(0.4, 0.5)
        seen = SimpleNamespace(pose_landmarks=[body])
        gone = SimpleNamespace(pose_landmarks=[])
        faces = SimpleNamespace(face_landmarks=[], facial_transformation_matrixes=[])
        tracker = PeopleTracker()
        first = tracker.update(seen, faces, 1000)[0].person_id
        tracker.update(gone, faces, 1500)
        self.assertEqual(tracker.update(seen, faces, 2000)[0].person_id, first)  # 1 s gap

    def test_a_hand_over_the_face_is_not_looking_away(self):
        import numpy as np
        from zephyr.presence.attention import Attention
        att = Attention()
        facing = Person(1, (0.5, 0.35), (), 0.25, np.eye(4), None)
        hidden = Person(1, (0.5, 0.35), (), 0.25, None, None)   # body seen, face covered
        now = 0
        for _ in range(15):
            now += 100
            att.update(facing, now, True)
        self.assertTrue(att.state)
        for _ in range(8):                  # 0.8 s of a palm in front of the face
            now += 100
            self.assertTrue(att.update(hidden, now, True))
        for _ in range(20):                 # still covered after the hold: then it counts
            now += 100
            att.update(hidden, now, True)
        self.assertFalse(att.state)

    def test_a_glance_does_not_pause_but_a_real_turn_does(self):
        """presence.yaw_deg, pitch_deg, away_ms: glancing at the keyboard or a second window is fine."""
        import numpy as np
        from zephyr.presence.attention import Attention

        def head(yaw=0.0, pitch=0.0):
            y, p = np.radians(yaw), np.radians(pitch)
            ry = np.array([[np.cos(y), 0, np.sin(y)], [0, 1, 0], [-np.sin(y), 0, np.cos(y)]])
            rx = np.array([[1, 0, 0], [0, np.cos(p), -np.sin(p)], [0, np.sin(p), np.cos(p)]])
            m = np.eye(4)
            m[:3, :3] = ry @ rx
            return Person(1, (0.5, 0.35), (), 0.25, m, None)
        att = Attention(initial=True)
        now = 0
        for _ in range(15):
            now += 100
            att.update(head(), now, True)
        for person in (head(yaw=30), head(yaw=-30), head(pitch=28), head(pitch=-28)):
            for _ in range(30):             # 3 s of each glance
                now += 100
                self.assertTrue(att.update(person, now, True))
        for _ in range(6):                  # a real turn, but only for 0.6 s
            now += 100
            self.assertTrue(att.update(head(yaw=60), now, True))
        for _ in range(5):
            now += 100
            att.update(head(), now, True)
        for _ in range(20):                 # turned away and staying there
            now += 100
            att.update(head(yaw=60), now, True)
        self.assertFalse(att.state)
        for _ in range(4):                  # looking back resumes quickly
            now += 100
            att.update(head(), now, True)
        self.assertTrue(att.state)

    def test_arming_does_not_flash_looking_away(self):
        """Every arm showed "Paused: looking away" for about 0.4 s."""
        import numpy as np
        point = lambda x, y: SimpleNamespace(x=x, y=y, visibility=1.0)
        body = [point(0.3, 0.3) for _ in range(17)]
        body[11], body[12] = point(0.2, 0.5), point(0.4, 0.5)
        face = [point(0.3, 0.3), point(0.3, 0.3)]
        models = SimpleNamespace(detect=lambda frame, ts: (
            SimpleNamespace(pose_landmarks=[body]),
            SimpleNamespace(face_landmarks=[face], facial_transformation_matrixes=[np.eye(4)])))
        clock = [1000]
        gate = Gate(clock=lambda: clock[0])
        processor = PresenceProcessor(gate, models)
        processor.process(None, clock[0], clock[0])
        self.assertTrue(gate.on_arm(()))
        self.assertEqual(gate.can_act(), (True, "ok"))
        clock[0] += 100
        state = processor.process(None, clock[0], clock[0])     # first presence frame after arming
        self.assertTrue(state.controller_attentive)
        self.assertEqual(gate.can_act(), (True, "ok"))

    def test_one_missed_body_frame_does_not_pause(self):
        clock = [1000]
        gate = Gate(clock=lambda: clock[0])
        owner = Person(1, (0.5, 0.35), (), 0.25)
        gate.update((owner,), True, clock[0])
        self.assertTrue(gate.on_arm(()))
        gate.update((owner,), True, clock[0])
        clock[0] += 100
        gate.update((), True, clock[0])     # the body detector missed one frame
        self.assertEqual(gate.can_act(), (True, "ok"))
        clock[0] += int(cfg.presence.away_ms)
        gate.update((), True, clock[0])
        self.assertEqual(gate.can_act(), (False, "looking_away"))

if __name__ == "__main__":
    unittest.main()
