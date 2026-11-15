"""Tune swipe thresholds against recorded clips. Owner: Ryan.

    python tools/tune.py                     # clips/*_x10_*.mp4 positives, clips/negative_*.mp4 negatives
    python tools/tune.py --show              # only score the current config.yaml values

Each clip runs through MediaPipe once; the hand detections are cached in clips/.cache/. Then the real
gesture engine (armed, every hand the owner) replays those detections for every combination in the
grid, which takes well under a second per combination. A positive clip named
swipe2_left_x10_ryan.mp4 must give 10 x swipe2.left. Negative clips must give no swipe at all.

Scoring: a false swipe costs as much as three missed swipes (master doc 1: a gesture that fires when
it should not is worse than one that is missing). The best rows are printed; nothing is written to
config.yaml.
"""
from __future__ import annotations

import argparse
import itertools
import pickle
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from zephyr.bus import bus  # noqa: E402
from zephyr.config import cfg  # noqa: E402
from zephyr.contracts import SystemMode, T  # noqa: E402

CLIPS = ROOT / "clips"
CACHE = CLIPS / ".cache"
POSITIVE = re.compile(r"^(swipe[25])_(left|right)_x(\d+)_")

GRID = {
    "min_peak_speed": [3.0, 4.0, 5.0],
    "min_travel_scales": [1.0, 1.2, 1.5],
    "max_ms": [600, 800],
    "open_ratio": [0.4, 0.5],
    "max_slope": [0.7, 0.9],
    "bridge_max_ms": [350, 450, 550],
}


def detections(clip: Path):
    """[(ts_ms, [RawHand])] for every frame, cached next to the clip."""
    import cv2
    from zephyr.core.hands import HandDetector
    CACHE.mkdir(exist_ok=True)
    cached = CACHE / (clip.stem + ".pkl")
    if cached.exists() and cached.stat().st_mtime >= clip.stat().st_mtime:
        return pickle.loads(cached.read_bytes())
    cap = cv2.VideoCapture(str(clip))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    det = HandDetector()
    frames, i = [], 0
    from zephyr import contracts
    while True:
        ok, frame = cap.read()
        if not ok or frame is None:
            break
        h, w = frame.shape[:2]
        contracts.ASPECT = w / h
        ts = int(round(i * 1000.0 / fps))
        frames.append((ts, det.detect(frame, ts)))
        i += 1
    cap.release()
    cached.write_bytes(pickle.dumps(frames))
    print(f"[tune] {clip.name}: {len(frames)} frames at {fps:.0f} fps")
    return frames


class _Owner:
    def role_of(self, hand):
        from zephyr.contracts import Role
        return Role.OWNER
    def can_act(self): return True, "ok"
    def on_arm(self, hands): return True
    def on_disarm(self): pass
    def guest_active(self): return False
    def revoke_guest(self): pass


_events: list[str] = []
bus.subscribe(T.GESTURE, lambda e: _events.append(e.key))


def replay(frames) -> Counter:
    from zephyr.core.engine import Engine
    from zephyr.core import swipes
    _events.clear()
    eng = Engine(hub=None, gate=_Owner(), detector=None)
    eng.swipes = swipes.SwipeDetector()
    eng.mode = SystemMode.ARMED
    for fid, (ts, raws) in enumerate(frames, 1):
        eng.process_raws(raws, ts, fid)
    return Counter(k for k in _events if k.startswith("swipe"))


def score(results, clips) -> tuple[float, int, int, int]:
    hits = misses = false = 0
    for clip, got in zip(clips, results):
        m = POSITIVE.match(clip.name)
        if m:
            want_key, n = f"{m.group(1)}.{m.group(2)}", int(m.group(3))
            hit = min(got.get(want_key, 0), n)
            hits += hit
            misses += n - hit
            false += sum(v for k, v in got.items() if k != want_key) + max(0, got.get(want_key, 0) - n)
        else:
            false += sum(got.values())
    return hits - 3 * false - 0 * misses, hits, misses, false


def main() -> int:
    ap = argparse.ArgumentParser(description="Tune swipe thresholds against clips")
    ap.add_argument("--show", action="store_true", help="score the current config only")
    ap.add_argument("--top", type=int, default=12)
    args = ap.parse_args()
    clips = sorted(p for p in CLIPS.glob("*.mp4") if POSITIVE.match(p.name) or p.name.startswith("negative"))
    if not clips:
        print("No clips. Record them with tools/live_check.py")
        return 1
    data = [detections(c) for c in clips]
    current = {k: cfg.swipe.get(k) for k in GRID}

    dropout = cfg.tracker.dropout_ms

    def run(params):
        cfg.swipe.__dict__.update(params)
        # the tracker must keep a hand's id at least as long as the longest bridged gap
        cfg.tracker.__dict__["dropout_ms"] = max(dropout, params.get("bridge_max_ms", 0))
        try:
            return [replay(d) for d in data]
        finally:
            cfg.swipe.__dict__.update(current)
            cfg.tracker.__dict__["dropout_ms"] = dropout

    base = run(current)
    s, hits, misses, false = score(base, clips)
    print(f"\ncurrent {current}\n  hits {hits}  missed {misses}  false {false}")
    for c, r in zip(clips, base):
        print(f"    {c.name:34s} {dict(r)}")
    if args.show:
        return 0

    rows = []
    keys = list(GRID)
    for combo in itertools.product(*(GRID[k] for k in keys)):
        params = dict(zip(keys, combo))
        res = run(params)
        rows.append((score(res, clips), params, res))
    # best score; among equals prefer the strictest settings (higher speed and travel)
    rows.sort(key=lambda r: (r[0][0], r[1]["min_peak_speed"], r[1]["min_travel_scales"]), reverse=True)
    print(f"\nbest of {len(rows)} combinations (score = hits - 3 x false):")
    for (s, hits, misses, false), params, _ in rows[:args.top]:
        print(f"  score {s:4.0f}  hits {hits:3d}  missed {misses:3d}  false {false:2d}  {params}")
    (s, *_), params, res = rows[0]
    print("\nbest per clip:")
    for c, r in zip(clips, res):
        print(f"    {c.name:34s} {dict(r)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
