"""zephyr/custom/selftest.py  Quick checks for the custom gesture code. Owner: Swayam.

Run:  python -m zephyr.custom.selftest
Uses a temporary data folder, so your real bindings are not touched.
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

import numpy as np

from zephyr.contracts import HandObs, HandsFrame, Pose, Role, Side
from zephyr.custom import store


def fake_world(shape: str, rng: np.random.Generator, noise: float = 0.004,
               scale: float = 1.0, flip: bool = False) -> np.ndarray:
    """Rough 3D hand in metres. shape picks which fingers are extended."""
    up = {"rock_on": {1, 4}, "call_me": {0, 4}, "three": {1, 2, 3}, "ok": {2, 3, 4}}[shape]
    pts = np.zeros((21, 3), np.float32)
    bases_x = [-0.03, -0.02, 0.0, 0.02, 0.035]
    for f in range(5):
        base = 1 + f * 4
        x = bases_x[f]
        for j in range(4):
            if f in up:
                pts[base + j] = (x * (1 + 0.15 * j), -0.04 - 0.025 * (j + 1), 0.0)
            else:
                pts[base + j] = (x, -0.05 - 0.008 * j, 0.012 * (j + 1))
    if shape == "ok":  # thumb tip meets index tip
        pts[4] = pts[8] = (-0.015, -0.06, 0.01)
    # real palms are about 9 cm from wrist to middle knuckle
    pts = pts * 2.0 * scale + rng.normal(0, noise, pts.shape).astype(np.float32)
    if flip:
        pts[:, 0] *= -1
    return pts


def fake_hand(world: np.ndarray, side: Side = Side.RIGHT, pose: Pose = Pose.UNKNOWN,
              role: Role = Role.OWNER, hand_id: int = 1) -> HandObs:
    lm = np.zeros((21, 3), np.float32)
    lm[:, :2] = 0.5 + world[:, :2] * 3
    return HandObs(hand_id=hand_id, side=side, landmarks=lm, world=world, scale=0.1,
                   pose=pose, pinch=1.0, score=0.95, role=role)


def main() -> None:
    rng = np.random.default_rng(7)
    tmp = Path(tempfile.mkdtemp(prefix="zephyr_custom_test_"))
    store.DATA_DIR = tmp
    store.BINDINGS_PATH = tmp / "bindings.json"
    store.GESTURES_DIR = tmp / "gestures"

    from zephyr.custom.capture import CaptureSession
    from zephyr.custom.matcher import Matcher

    def record(shape: str) -> np.ndarray:
        s = CaptureSession(samples=30, default_threshold=0.16, countdown_ms=0)
        s.start(0)
        t = 0
        for i in range(40):
            frame = HandsFrame(i, t, (fake_hand(fake_world(shape, rng)),))
            s.tick(t, frame)
            t += 33
            if s.state == "done":
                break
        assert s.result and s.result.ok, s.result
        return s.result.built

    m = Matcher()
    for shape in ("rock_on", "call_me", "three"):
        built = record(shape)
        near = m.nearest(built.template)
        if near:
            print(f"  {shape}: nearest existing is {near[1]} at {near[2]:.3f}")
        store.add_binding(shape.replace("_", " ").title(), built.template, built.threshold,
                          {"type": "url", "value": "https://example.com", "label": shape})
        m.reload()
    ids = [b["id"] for b in store.load_bindings()["bindings"]]
    assert ids == ["rock_on", "call_me", "three"], ids

    # positives at different sizes and the left hand (mirrored) should match
    hits = 0
    for shape in ("rock_on", "call_me", "three"):
        for k in range(10):
            side = Side.LEFT if k % 2 else Side.RIGHT
            w = fake_world(shape, rng, scale=rng.uniform(0.8, 1.25), flip=(side is Side.LEFT))
            r = m.match(fake_hand(w, side=side))
            hits += int(r is not None and r[0] == shape)
    print(f"positives: {hits}/30 matched")
    assert hits >= 27

    # a shape that was never recorded should not match
    false = sum(m.match(fake_hand(fake_world("ok", rng))) is not None for _ in range(20))
    print(f"negatives: {false}/20 false matches")
    assert false <= 1

    # built-in pose frames get rejected by the recorder
    s = CaptureSession(samples=30, default_threshold=0.16, countdown_ms=0)
    s.start(0)
    for i in range(40):
        s.tick(i * 33, HandsFrame(i, i * 33, (fake_hand(fake_world("three", rng), pose=Pose.OPEN),)))
    assert s.result and not s.result.ok and "built-in" in s.result.message, s.result
    print("built-in rejection:", s.result.message)

    # stranger hands are ignored, so the recording fails for "hand not in view"
    s = CaptureSession(samples=30, default_threshold=0.16, countdown_ms=0)
    s.start(0)
    for i in range(40):
        s.tick(i * 33, HandsFrame(i, i * 33, (fake_hand(fake_world("three", rng), role=Role.STRANGER),)))
    assert s.result and not s.result.ok
    print("stranger rejection:", s.result.message)

    # speed of match()
    hand = fake_hand(fake_world("rock_on", rng))
    n = 2000
    t0 = time.perf_counter()
    for _ in range(n):
        m.match(hand)
    per = (time.perf_counter() - t0) / n * 1000
    print(f"match(): {per:.3f} ms per call with {len(ids)} templates")
    assert per < 1.0

    assert store.delete_binding("call_me")
    m.reload()
    assert m.match(fake_hand(fake_world("call_me", rng))) is None
    print("delete: ok")
    print("ALL CUSTOM GESTURE CHECKS PASSED")


if __name__ == "__main__":
    main()
