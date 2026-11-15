"""zephyr/core/camera.py    Camera thread and FrameHub. Owner: Aadharsh.

FrameHub keeps ONLY the newest frame (one slot, overwritten under a lock, no queue).
    hub = FrameHub()          # or FrameHub(clip="clips/x.mp4")
    hub.start()               # opens the camera on THIS thread (main thread on macOS for the permission prompt)
    hub.latest()              # -> (bgr_frame, ts_ms, frame_id) or None before the first frame

Live frames are mirrored once here (selfie view). Clips are stored already mirrored, so they are not flipped.
Quick test:  python -m zephyr.core.camera     (prints fps and frame age once a second)
"""
from __future__ import annotations

import sys
import threading
import time
from typing import Optional

import os

os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")   # keep OpenCV's own retry warnings out of the console

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from zephyr import contracts
from zephyr.config import cfg


def _now_ms() -> int:
    return int(time.monotonic() * 1000)


def _backend() -> int:
    # Capture backend selection only (no OS actions). DirectShow opens fast on Windows; AVFoundation on macOS.
    if sys.platform.startswith("win"):
        return cv2.CAP_DSHOW
    if sys.platform == "darwin":
        return cv2.CAP_AVFOUNDATION
    return cv2.CAP_ANY


class FrameHub:
    def __init__(self, clip: str | None = None):
        self.clip = clip
        self._lock = threading.Lock()
        self._slot: Optional[tuple[np.ndarray, int, int]] = None
        self._frame_id = 0
        self._cap: Optional[cv2.VideoCapture] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self.ended = threading.Event()     # set when a clip reaches its end
        self.fps = 0.0                     # measured capture rate
        self.size = (0, 0)                 # (width, height) of real frames
        self._warned = False

    # ---------- public ----------
    def start(self) -> None:
        """Open the source on the calling thread, then start the reader thread. Never raises:
        if the camera cannot open, the reader keeps retrying and latest() stays None."""
        if self._running:
            return
        self._running = True
        if self.clip:
            self._cap = cv2.VideoCapture(self.clip)
            if not self._cap.isOpened():
                print(f"[camera] cannot open clip {self.clip!r}")
            target = self._clip_loop
        else:
            self._open_live()
            target = self._live_loop
        self._thread = threading.Thread(target=target, name="zephyr-camera", daemon=True)
        self._thread.start()

    def latest(self) -> tuple[np.ndarray, int, int] | None:
        """Newest mirrored BGR frame, its ts_ms and frame_id. None before the first frame."""
        with self._lock:
            return self._slot

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)
        if self._cap is not None:
            self._cap.release()

    # ---------- internals ----------
    def _open_live(self) -> bool:
        c = cfg.camera
        cap = cv2.VideoCapture(int(c.index), _backend())
        if not cap.isOpened():
            cap.release()
            cap = cv2.VideoCapture(int(c.index))      # fall back to the default backend
        if not cap.isOpened():
            if not self._warned:
                print(f"[camera] cannot open camera index {c.index}; retrying quietly every second")
                self._warned = True
            self._cap = None
            return False
        if self._warned:
            print("[camera] camera opened")
            self._warned = False
        if sys.platform.startswith("win"):
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(c.width))
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(c.height))
        cap.set(cv2.CAP_PROP_FPS, int(c.fps))
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self._cap = cap
        ok, frame = cap.read()                        # first read here, on the opening thread
        if ok and frame is not None:
            self._store(frame, _now_ms(), mirror=bool(c.mirror))
        return True

    def _store(self, frame: np.ndarray, ts_ms: int, mirror: bool) -> None:
        if mirror:
            frame = cv2.flip(frame, 1)
        h, w = frame.shape[:2]
        if (w, h) != self.size:
            self.size = (w, h)
            contracts.ASPECT = w / h
        with self._lock:
            self._frame_id += 1
            self._slot = (frame, ts_ms, self._frame_id)

    def _live_loop(self) -> None:
        fails = 0
        n, t_last = 0, time.monotonic()
        while self._running:
            if self._cap is None:
                time.sleep(1.0)
                self._open_live()
                continue
            ok, frame = self._cap.read()
            ts = _now_ms()
            if not ok or frame is None:
                fails += 1
                if fails >= int(cfg.camera.get("reopen_after_fails", 30)):
                    print("[camera] camera lost, reopening")
                    try:
                        self._cap.release()
                    except Exception:
                        pass
                    self._cap = None
                    fails = 0
                else:
                    time.sleep(0.01)
                continue
            fails = 0
            self._store(frame, ts, mirror=bool(cfg.camera.mirror))
            n += 1
            now = time.monotonic()
            if now - t_last >= 1.0:
                self.fps = n / (now - t_last)
                n, t_last = 0, now

    def _clip_loop(self) -> None:
        """Plays a clip in real time using the clip's own timestamps (already mirrored: no flip)."""
        cap = self._cap
        if cap is None or not cap.isOpened():
            self.ended.set()
            return
        t0_ms = _now_ms()
        last_ts = -1
        while self._running:
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            pos = int(cap.get(cv2.CAP_PROP_POS_MSEC))
            ts = t0_ms + pos
            if ts <= last_ts:
                ts = last_ts + 1
            last_ts = ts
            wait = (ts - _now_ms()) / 1000.0
            if wait > 0:
                time.sleep(wait)
            self._store(frame, ts, mirror=False)
        self.ended.set()
        print("[camera] clip ended")


if __name__ == "__main__":
    hub = FrameHub(clip=sys.argv[1] if len(sys.argv) > 1 else None)
    hub.start()
    print("[camera] Ctrl+C to stop. Unplug and replug the camera to test recovery.")
    try:
        while True:
            time.sleep(1.0)
            got = hub.latest()
            if got is None:
                print("[camera] no frame yet")
                continue
            frame, ts, fid = got
            age = _now_ms() - ts
            print(f"[camera] {hub.fps:5.1f} fps   frame age {age:3d} ms   id {fid}   size {hub.size}")
    except KeyboardInterrupt:
        hub.stop()
