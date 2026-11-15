"""Focused checks for replay time and live/replay presence processing."""
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

    def test_processor_publishes_owner_and_attention(self):
        import numpy as np
        point = lambda x, y: SimpleNamespace(x=x, y=y, visibility=1.0)
        body = [point(0.3, 0.3) for _ in range(17)]
        body[11], body[12] = point(0.2, 0.5), point(0.4, 0.5)
        body[15], body[16] = point(0.2, 0.7), point(0.4, 0.7)
        face = [point(0.3, 0.3), point(0.3, 0.3)]
        models = SimpleNamespace(detect=lambda frame, ts: (
            SimpleNamespace(pose_landmarks=[body]),
            SimpleNamespace(face_landmarks=[face], facial_transformation_matrixes=[np.eye(4)])))
        clock = [1000]
        gate = Gate(clock=lambda: clock[0])
        processor = PresenceProcessor(gate, models)
        processor.process(None, clock[0], clock[0])
        self.assertTrue(gate.on_arm(()))
        for _ in range(6):
            clock[0] += 100
            state = processor.process(None, clock[0], clock[0])
        self.assertEqual(state.controller_role, Role.OWNER)
        self.assertTrue(state.controller_attentive)
        self.assertEqual(gate.can_act(), (True, "ok"))

    def test_replay_uses_each_frame_and_closes_resources(self):
        cv2 = ModuleType("cv2")
        with patch.dict(sys.modules, {"cv2": cv2}):
            from tools import replay
            import zephyr.core.engine as engine_module
            sys.modules["zephyr.core.engine"] = engine_module
        calls = []

        class Capture:
            def __init__(self, path): self.index = 0
            def isOpened(self): return True
            def get(self, key): return 10.0
            def read(self):
                self.index += 1
                return (True, SimpleNamespace(shape=(480, 640, 3))) if self.index <= 3 else (False, None)
            def release(self): calls.append("capture closed")

        class Models:
            def close(self): calls.append("models closed")

        class Detector:
            def close(self): calls.append("detector closed")

        class Engine:
            def __init__(self, hub, gate):
                self.gate, self.detector, self.mode = gate, Detector(), SystemMode.DISARMED
            def process(self, frame, ts, fid): calls.append(("engine", ts, self.gate.clock()))

        class Processor:
            def __init__(self, gate, models): pass
            def process(self, frame, ts, now, hands): calls.append(("presence", ts, now))

        cv2.VideoCapture, cv2.CAP_PROP_FPS = Capture, 5
        models = ModuleType("zephyr.presence.models")
        models.Models = Models
        with patch.dict(sys.modules, {"cv2": cv2, "zephyr.presence.models": models,
                                     "zephyr.core.engine": engine_module}), \
             patch.object(engine_module, "Engine", Engine), \
             patch("zephyr.presence.thread.PresenceProcessor", Processor):
            replay.run_presence_clip("sample.mp4")
        self.assertEqual([x for x in calls if isinstance(x, tuple) and x[0] == "engine"],
                         [("engine", 1000, 1000), ("engine", 1100, 1100), ("engine", 1200, 1200)])
        self.assertIn("capture closed", calls)
        self.assertIn("models closed", calls)
        self.assertIn("detector closed", calls)


if __name__ == "__main__":
    unittest.main()
