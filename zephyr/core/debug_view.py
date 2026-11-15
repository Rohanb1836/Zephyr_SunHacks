"""zephyr/core/debug_view.py    Live check for the core engine. Owner: Aadharsh.

    python -m zephyr.core.debug_view            (camera)
    python -m zephyr.core.debug_view clip.mp4   (clip)

Shows the mirrored camera with each hand's id, side, pose, pinch and palm-facing, plus the
latest gesture/level/pointer events and the median grab-to-publish latency.
Keys: q quits, r reloads config.yaml now.
Check in the first hour: raise your RIGHT hand; it must say "right". If not, set hands.swap_sides: true.
Show your palm; "palm" must appear. If it shows for the back of your hand, set poses.palm_sign: -1.
"""
from __future__ import annotations

import sys
import time
from collections import deque

import cv2
import numpy as np

from zephyr.bus import bus
from zephyr.config import cfg, reload
from zephyr.contracts import T
from zephyr.core import features as F
from zephyr.core.camera import FrameHub
from zephyr.core.engine import Engine

EDGES = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10), (10, 11),
         (11, 12), (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (17, 18), (18, 19), (19, 20), (0, 17)]


def main() -> int:
    hub = FrameHub(clip=sys.argv[1] if len(sys.argv) > 1 else None)
    hub.start()
    Engine(hub).start()
    events: deque[str] = deque(maxlen=6)
    lat: deque[int] = deque(maxlen=120)
    bus.subscribe(T.GESTURE, lambda e: events.appendleft(f"gesture {e.key} ({e.side.value})"))
    bus.subscribe(T.SYSTEM, lambda e: events.appendleft(f"system {e.mode.value} {e.reason}"))
    bus.subscribe(T.LEVEL, lambda e: e.active or events.appendleft(f"level {e.kind} released"))
    bus.subscribe(T.STROKE, lambda e: e.phase != "point" and events.appendleft(f"stroke {e.phase} #{e.stroke_id}"))
    bus.subscribe(T.LATENCY, lambda d: lat.append(d["publish_ms"] - d["capture_ms"]))
    while True:
        got = hub.latest()
        if got is None:
            time.sleep(0.01)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
            continue
        img = got[0].copy()
        h, w = img.shape[:2]
        hf = bus.latest(T.HANDS)
        for hand in (hf.hands if hf else ()):
            pts = [(int(p[0] * w), int(p[1] * h)) for p in hand.landmarks]
            col = (160, 160, 160) if hand.role.value == "stranger" else (80, 220, 80)
            for a, b in EDGES:
                cv2.line(img, pts[a], pts[b], col, 2)
            facing = "palm" if F.palm_facing(hand.landmarks, hand.side, float(cfg.poses.get("palm_sign", 1))) else "back"
            label = f"#{hand.hand_id} {hand.side.value} {hand.pose.value} pinch {hand.pinch:.2f} {facing}"
            cv2.putText(img, label, (pts[0][0] - 60, pts[0][1] + 25), cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 2)
        p = bus.latest(T.POINTER)
        if p is not None and p.visible:
            cv2.circle(img, (int(p.x * w), int(p.y * h)), 8, (0, 0, 255) if p.frozen else (255, 120, 0), -1)
        hold = bus.latest(T.HOLD)
        if hold is not None and hold.progress > 0:
            cv2.ellipse(img, (w - 40, 40), (25, 25), -90, 0, 360 * hold.progress, (0, 200, 255), 4)
            cv2.putText(img, hold.kind, (w - 120, 85), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 255), 1)
        sysm = bus.latest(T.SYSTEM)
        med = int(np.median(lat)) if lat else -1
        cv2.putText(img, f"{sysm.mode.value if sysm else '?'}   cam {hub.fps:.0f} fps   latency {med} ms",
                    (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        for i, ev in enumerate(events):
            cv2.putText(img, ev, (10, 50 + 20 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
        cv2.imshow("zephyr core", img)
        k = cv2.waitKey(1) & 0xFF
        if k == ord("q"):
            break
        if k == ord("r"):
            reload()
    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
