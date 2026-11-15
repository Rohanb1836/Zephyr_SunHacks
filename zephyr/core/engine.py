"""zephyr/core/engine.py    The gesture state machine. Owner: Aadharsh.

KICKOFF VERSION: runs the vision loop and publishes empty HandsFrames, SystemState and latency,
so everyone can run the full app now. Real hand tracking and gestures land in later commits
with the same entry points:
    Engine(hub, gate, matcher).start()
    run_clip(path, gate=None, matcher=None)
"""
from __future__ import annotations

import threading
import time

from zephyr.bus import bus
from zephyr.config import maybe_reload
from zephyr.contracts import HandsFrame, SystemMode, SystemState, T
from zephyr.stubs import NullGate, NullMatcher


def _now_ms() -> int:
    return int(time.monotonic() * 1000)


class Engine:
    def __init__(self, hub, gate=None, matcher=None):
        self.hub = hub
        self.gate = gate or NullGate()
        self.matcher = matcher or NullMatcher()
        self.mode = SystemMode.DISARMED
        self._thread: threading.Thread | None = None
        self._running = False
        self._last_fid = -1

    def start(self) -> "Engine":
        self._running = True
        bus.publish(T.SYSTEM, SystemState(self.mode, "start"))
        self._thread = threading.Thread(target=self._loop, name="zephyr-vision", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._running = False

    def process(self, frame, ts_ms: int, frame_id: int) -> None:
        """One frame through the pipeline. Also used by run_clip."""
        t_infer = _now_ms()
        bus.publish(T.HANDS, HandsFrame(frame_id, ts_ms, ()))
        t_pub = _now_ms()
        bus.publish(T.LATENCY, {"frame_id": frame_id, "capture_ms": ts_ms,
                                "inference_ms": t_infer, "publish_ms": t_pub})

    def _loop(self) -> None:
        next_cfg_check = 0.0
        while self._running:
            now = time.monotonic()
            if now >= next_cfg_check:
                maybe_reload()
                next_cfg_check = now + 1.0
            got = self.hub.latest()
            if got is None or got[2] == self._last_fid:
                time.sleep(0.002)
                continue
            frame, ts, fid = got
            self._last_fid = fid
            try:
                self.process(frame, ts, fid)
            except Exception as exc:     # never let one bad frame kill the vision thread
                print(f"[engine] frame {fid} error: {exc!r}")


def run_clip(path: str, gate=None, matcher=None) -> None:
    """Run a recorded clip through the real pipeline and publish the normal bus events.
    Uses the clip's own timestamps. Returns when the clip ends.
    Defaults to NullGate and NullMatcher."""
    import cv2
    from zephyr import contracts

    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise FileNotFoundError(path)
    eng = Engine(hub=None, gate=gate, matcher=matcher)
    bus.publish(T.SYSTEM, SystemState(eng.mode, "clip"))
    fid, last_ts = 0, -1
    while True:
        ok, frame = cap.read()
        if not ok or frame is None:
            break
        h, w = frame.shape[:2]
        contracts.ASPECT = w / h
        ts = int(cap.get(cv2.CAP_PROP_POS_MSEC))
        if ts <= last_ts:
            ts = last_ts + 1
        last_ts = ts
        fid += 1
        eng.process(frame, ts, fid)
    cap.release()
