"""Guided live accuracy check against master doc section 7.1. Owner: Ryan.

    python tools/live_check.py --name ryan                 # every round
    python tools/live_check.py --name ryan --only swipe5_left fist_tap
    python tools/live_check.py --list

A window shows what to do and counts down. The real camera, hand tracker and gesture engine run
(armed, NullGate, no OS actions), and every gesture event is counted. Each round is also saved as
clips/<round>_<name>.mp4 at the camera's real frame rate, so thresholds can be tuned afterwards with
tools/replay.py against exactly what happened. A report is printed and written to clips/live_<name>.md.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import threading
import time
from collections import Counter
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from zephyr.bus import bus  # noqa: E402
from zephyr.contracts import SystemMode, T  # noqa: E402

# name, instruction, expected key, expected count (None: negative round), seconds
ROUNDS = [
    ("swipe5_left", "Open hand: swipe LEFT, 10 times. Rest a moment between swipes.", "swipe5.left", 10, 30),
    ("swipe5_right", "Open hand: swipe RIGHT, 10 times.", "swipe5.right", 10, 30),
    ("swipe2_left", "Two fingers up: swipe LEFT, 10 times.", "swipe2.left", 10, 30),
    ("swipe2_right", "Two fingers up: swipe RIGHT, 10 times.", "swipe2.right", 10, 30),
    ("fist_tap", "Quick fist: close and open, 10 times. Pause between taps.", "fist_tap", 10, 25),
    ("pinch_tap", "Point with one finger, then a quick thumb-index pinch. 10 times.", "pinch_tap", 10, 30),
    ("palm_hold", "One open palm, still, until it fires. Drop the hand. 5 times.", "palm_hold", 5, 30),
    ("pointer_still", "Point with one finger and hold it STILL. (pointer jitter)", None, None, 8),
    ("negative", "Act normally: talk with your hands, touch your face, drink, type. Do NOT gesture.",
     None, None, 60),
    ("arm", "Both palms up, held until the ring fills. Drop both hands. 5 times.", "arm_toggle", 5, 30),
    ("knob", "Right fist, hold, twist RIGHT then back LEFT, open. 5 times.", None, None, 35),
]
GESTURE_SECONDS = {r[0]: r[4] for r in ROUNDS}


class ClipRecorder:
    """Writes every new camera frame on its own thread. The preview loop (imshow) runs slower than
    the camera, so writing from it saved 19 fps clips of a 30 fps camera."""
    def __init__(self, hub, path: Path) -> None:
        self.hub, self.path = hub, path
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        fps = round(self.hub.fps) or 30
        writer, last = None, -1
        while self.running:
            got = self.hub.latest()
            if got is None or got[2] == last:
                time.sleep(0.002)
                continue
            frame, _ts, last = got
            if writer is None:
                h, w = frame.shape[:2]
                writer = cv2.VideoWriter(str(self.path), cv2.VideoWriter_fourcc(*"mp4v"), float(fps), (w, h))
            writer.write(frame)
        if writer is not None:
            writer.release()

    def stop(self) -> None:
        self.running = False
        self.thread.join(timeout=2)


class ArmedGate:
    """NullGate that keeps the engine armed: this check measures the core, not presence."""
    def role_of(self, hand):
        from zephyr.contracts import Role
        return Role.OWNER
    def can_act(self): return True, "ok"
    def on_arm(self, hands): return True
    def on_disarm(self): pass
    def guest_active(self): return False
    def revoke_guest(self): pass


def run(name: str, only: list[str], screen_w: int, screen_h: int) -> int:
    from zephyr.core.camera import FrameHub
    from zephyr.core.engine import Engine

    events: list[tuple[int, str]] = []
    pointer: list[tuple[int, float, float]] = []
    latency: list[int] = []
    lock = threading.Lock()

    def on_gesture(e):
        with lock:
            events.append((int(time.monotonic() * 1000), e.key))

    def on_pointer(p):
        if p.visible and not p.frozen:
            with lock:
                pointer.append((int(time.monotonic() * 1000), p.x, p.y))

    bus.subscribe(T.GESTURE, on_gesture)
    bus.subscribe(T.POINTER, on_pointer)
    levels: list[tuple[int, str, float]] = []
    bus.subscribe(T.LEVEL, lambda e: e.delta and levels.append((int(time.monotonic() * 1000), e.kind, e.delta)))
    bus.subscribe(T.LATENCY, lambda d: latency.append(d["publish_ms"] - d["capture_ms"]))

    hub = FrameHub()
    hub.start()
    engine = Engine(hub, gate=ArmedGate())
    engine.start()
    engine._set_mode(SystemMode.ARMED, "live_check")

    clips = ROOT / "clips"
    clips.mkdir(exist_ok=True)
    rounds = [r for r in ROUNDS if not only or r[0] in only]
    results = []
    window = "Zephyr live check"
    try:
        for key, text, expect_key, expect_n, seconds in rounds:
            engine._set_mode(SystemMode.ARMED, "live_check")   # the arm round may have paused it
            # 4 s get-ready, then the round
            ready_until = time.monotonic() + 4
            writer = None
            last_fid = -1
            t_start = t_end = None
            while True:
                got = hub.latest()
                now = time.monotonic()
                if got is None or got[2] == last_fid:
                    if cv2.waitKey(1) & 0xFF == 27:
                        raise KeyboardInterrupt
                    continue
                frame, _ts, last_fid = got
                h, w = frame.shape[:2]
                if t_start is None and now >= ready_until:
                    t_start = now
                    t_end = now + seconds
                    with lock:
                        events_before = len(events)
                        pointer_before = len(pointer)
                    clip = f"{key}_x{expect_n}_{name}.mp4" if expect_n else f"{key}_{name}.mp4"
                    writer = ClipRecorder(hub, clips / clip)
                view = frame.copy()
                with lock:
                    got_now = Counter(k for _, k in events[events_before:]) if t_start else Counter()
                if t_start is None:
                    banner = f"GET READY {ready_until - now:.0f}"
                else:
                    banner = f"GO  {t_end - now:4.1f} s"
                cv2.rectangle(view, (0, 0), (w, 92), (20, 20, 20), -1)
                cv2.putText(view, f"{key}: {banner}", (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                            (0, 200, 255) if t_start is None else (0, 255, 0), 2, cv2.LINE_AA)
                cv2.putText(view, text[:70], (12, 58), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
                if expect_key:
                    cv2.putText(view, f"detected {got_now.get(expect_key, 0)} / {expect_n}   other: "
                                f"{sum(got_now.values()) - got_now.get(expect_key, 0)}",
                                (12, 82), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 0), 1, cv2.LINE_AA)
                elif t_start is not None:
                    cv2.putText(view, f"events so far: {sum(got_now.values())}", (12, 82),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 0), 1, cv2.LINE_AA)
                cv2.imshow(window, view)
                if cv2.waitKey(1) & 0xFF == 27:
                    raise KeyboardInterrupt
                if t_end is not None and now >= t_end:
                    break
            if writer is not None:
                writer.stop()
            with lock:
                got = Counter(k for _, k in events[events_before:])
                pts = pointer[pointer_before:]
            row = {"round": key, "expect": expect_key, "n": expect_n, "got": dict(got)}
            lv = [x for x in levels if x[0] >= int(t_start * 1000)] if t_start else []
            if lv:
                row["levels"] = {k: round(sum(d for _, kk, d in lv if kk == k), 1) for k in {x[1] for x in lv}}
                row["level_steps"] = len(lv)
            if key == "pointer_still" and len(pts) > 10:
                xs = [p[1] * screen_w for p in pts[len(pts) // 4:]]   # skip the settling quarter
                ys = [p[2] * screen_h for p in pts[len(pts) // 4:]]
                row["jitter_px"] = round(statistics.pstdev(xs) + statistics.pstdev(ys), 1) / 2
                row["pointer_samples"] = len(pts)
            results.append(row)
            print(f"[live] {key}: {dict(got)}" + (f"  jitter {row.get('jitter_px')} px" if "jitter_px" in row else ""))
    except KeyboardInterrupt:
        print("[live] stopped early (Esc)")
    finally:
        engine.stop()
        hub.stop()
        cv2.destroyAllWindows()

    lines = [f"# Live check: {name}", "", f"Camera {hub.fps:.0f} fps (last second measured).", ""]
    if latency:
        lat = sorted(latency)
        lines.append(f"Frame to event: median {lat[len(lat) // 2]} ms, p95 {lat[int(len(lat) * .95)]} ms "
                     f"(target 30 / 50).")
        lines.append("")
    lines += ["| Round | Expected | Detected | Other events | Target |", "|---|---|---|---|---|"]
    for r in results:
        other = {k: v for k, v in r["got"].items() if k != r["expect"]}
        if r["expect"]:
            hit = r["got"].get(r["expect"], 0)
            target = "19/20 → 10 of 10 (9 ok)" if r["n"] == 10 else f"{r['n']} of {r['n']}"
            lines.append(f"| {r['round']} | {r['n']} × {r['expect']} | {hit} | {other or '-'} | {target} |")
        elif r["round"] == "knob":
            lines.append(f"| knob | volume up then down | {r.get('level_steps', 0)} level steps, net "
                         f"{r.get('levels', {})} | {r['got'] or '-'} | moves both ways, no taps |")
        elif r["round"] == "pointer_still":
            lines.append(f"| pointer_still | jitter | {r.get('jitter_px', 'n/a')} px σ | - | ≤ 4 px |")
        else:
            lines.append(f"| {r['round']} | 0 events | {sum(r['got'].values())} | {r['got'] or '-'} | "
                         f"≤ 1 per 2 min |")
    report = "\n".join(lines) + "\n"
    print("\n" + report)
    (clips / f"live_{name}.md").write_text(report, encoding="utf-8")
    return 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):   # Windows consoles default to cp1252
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Guided live accuracy check")
    ap.add_argument("--name", default="me", help="your name, used in clip file names")
    ap.add_argument("--only", nargs="*", default=[], help="run only these rounds")
    ap.add_argument("--list", action="store_true", help="list the rounds and exit")
    args = ap.parse_args()
    if args.list:
        for r in ROUNDS:
            print(f"{r[0]:14s} {r[4]:3d} s  {r[1]}")
        return 0
    sw, sh = 1440, 900   # pointer jitter is reported in pixels of the main display
    if sys.platform.startswith("win"):
        import ctypes
        sw, sh = ctypes.windll.user32.GetSystemMetrics(0), ctypes.windll.user32.GetSystemMetrics(1)
    return run(args.name, args.only, sw, sh)


if __name__ == "__main__":
    raise SystemExit(main())
