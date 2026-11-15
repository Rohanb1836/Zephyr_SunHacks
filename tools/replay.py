"""Replay clips through the core engine and write a measured accuracy report."""
from __future__ import annotations

import argparse
import collections
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from zephyr.bus import bus
from zephyr.contracts import T
from zephyr.core.engine import run_clip


def run_presence_clip(path):
    """Run every video frame through presence and the real engine in clip time."""
    import cv2
    from zephyr import contracts
    from zephyr.contracts import SystemState
    from zephyr.config import cfg
    from zephyr.core.engine import Engine
    from zephyr.presence.gate import Gate
    from zephyr.presence.models import Models
    from zephyr.presence.thread import PresenceProcessor

    if cfg.presence.mode == "off":
        raise ValueError("presence.mode is off; presence replay requires lenient or strict mode")
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        cap.release()
        raise FileNotFoundError(path)
    models = None
    engine = None
    try:
        fps = cap.get(cv2.CAP_PROP_FPS)
        if not fps or fps <= 0 or fps != fps:
            raise ValueError("clip FPS is unavailable; cannot derive reliable replay timestamps")
        clock = [1000]
        gate = Gate(clock=lambda: clock[0])
        models = Models()
        processor = PresenceProcessor(gate, models)
        engine = Engine(hub=None, gate=gate)
        if engine.detector is None:
            raise RuntimeError("hand detector unavailable; install dependencies and download models")
        last_presence = None
        previous_hands = None
        frame_id = 0
        bus.publish(T.SYSTEM, SystemState(engine.mode, "clip"))
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            frame_id += 1
            # VideoWriter clips are constant-rate. Use frame number rather than unreliable
            # decoder position metadata, and never mirror the already-mirrored clip.
            clock[0] = 1000 + round((frame_id - 1) * 1000 / fps)
            height, width = frame.shape[:2]
            contracts.ASPECT = width / height
            if last_presence is None or clock[0] - last_presence >= 1000 / float(cfg.presence.fps):
                processor.process(frame, clock[0], clock[0], previous_hands)
                last_presence = clock[0]
            engine.process(frame, clock[0], frame_id)
            previous_hands = bus.latest(T.HANDS)
        if not frame_id:
            raise ValueError("clip has no readable frames")
    finally:
        cap.release()
        if models is not None:
            models.close()
        if engine is not None and engine.detector is not None:
            engine.detector.close()


def percentile(values, fraction):
    values = sorted(values)
    if not values:
        return None
    position = (len(values) - 1) * fraction
    low = int(position)
    high = min(low + 1, len(values) - 1)
    return values[low] + (values[high] - values[low]) * (position - low)


def main():
    parser = argparse.ArgumentParser(description="Replay Zephyr test clips")
    parser.add_argument("clips", nargs="*", type=Path, help="MP4 clips (default: clips/*.mp4)")
    parser.add_argument("--width", type=int, default=0, help="demo display width in pixels")
    parser.add_argument("--height", type=int, default=0, help="demo display height in pixels")
    parser.add_argument("--presence", action="store_true", help="replay face/body tracking and the real owner gate")
    parser.add_argument("--report", type=Path, default=ROOT / "tools/report.md")
    args = parser.parse_args()
    if bool(args.width) != bool(args.height) or args.width < 0 or args.height < 0:
        parser.error("supply both positive --width and --height")
    clips = args.clips or sorted((ROOT / "clips").glob("*.mp4"))
    if not clips:
        print("No clips found.")
        return 1
    captured = collections.defaultdict(list)
    for topic in (T.GESTURE, T.LEVEL, T.POINTER, T.LATENCY, T.PRESENCE, T.SYSTEM, T.HANDS):
        bus.subscribe(topic, lambda message, topic=topic: captured[topic].append(message))
    rows = []
    failures = 0
    for path in clips:
        captured.clear()
        try:
            if args.presence:
                run_presence_clip(path)
            else:
                run_clip(str(path))
        except Exception as exc:
            print(f"{path.name}: error: {exc}")
            rows.append(f"| {path.name} | error | {exc} |")
            failures += 1
            continue
        counts = collections.Counter(e.key for e in captured[T.GESTURE])
        match = re.match(r"(.+)_x(\d+)_[^_]+$", path.stem)
        expected = match.group(1).replace("swipe5_left", "swipe5.left").replace("swipe5_right", "swipe5.right").replace("swipe2_left", "swipe2.left").replace("swipe2_right", "swipe2.right") if match else None
        expected_text = f"{expected} x{match.group(2)}" if match else "negative" if path.stem.startswith("negative_") else "unlabelled"
        # Clip timestamps may start at zero while publication uses monotonic time.
        # Those clocks cannot be subtracted to measure capture-to-publication latency.
        latency = []  # Clip time and wall-clock publication are different time domains.
        duration_ms = max((e.get("capture_ms", 0) for e in captured[T.LATENCY]), default=0) - min((e.get("capture_ms", 0) for e in captured[T.LATENCY]), default=0)
        rates = {key: round(value * 60000 / duration_ms, 2) for key, value in counts.items()} if duration_ms > 0 else {}
        details = f"events={dict(counts)}, level events={len(captured[T.LEVEL])}"
        if args.presence:
            from zephyr.contracts import Role, SystemMode
            states = captured[T.PRESENCE]
            grants = sum(state.controller_role == Role.GUEST and
                         (i == 0 or states[i - 1].controller_role != Role.GUEST)
                         for i, state in enumerate(states))
            stranger_hands = sum(h.role == Role.STRANGER for frame in captured[T.HANDS] for h in frame.hands)
            stranger_events = sum(e.role == Role.STRANGER for e in captured[T.GESTURE])
            guest_events = sum(e.role == Role.GUEST for e in captured[T.GESTURE])
            suspensions = sum(s.mode == SystemMode.SUSPENDED for s in captured[T.SYSTEM])
            details += (f", guest grants={grants}, guest gestures={guest_events}, "
                        f"stranger hand observations={stranger_hands}, stranger gestures={stranger_events}, "
                        f"suspensions={suspensions}")
        if path.stem.startswith("negative_"):
            details += f", events/min={rates}"
        if path.stem.startswith("point_still"):
            points = [p for p in captured[T.POINTER] if p.visible]
            if len(points) >= 2 and args.width and args.height:
                details += f", jitter x/y={statistics.pstdev(p.x for p in points)*args.width:.2f}/{statistics.pstdev(p.y for p in points)*args.height:.2f} px"
            else:
                details += ", jitter unavailable (need visible samples and display dimensions)"
        if latency:
            details += f", latency median/p95={statistics.median(latency):.1f}/{percentile(latency, 0.95):.1f} ms"
        else:
            details += ", latency unavailable"
        rows.append(f"| {path.name} | {expected_text} | {details} |")
        print(f"{path.name}: {expected_text}: {details}")
    report = ["# Zephyr replay report", "", "Targets: gesture detection at least 19/20; false triggers at most 1 per 2 minutes; latency median <= 30 ms and p95 <= 50 ms; pointer jitter <= 4 px.", "", "The engine starts disarmed; clips must include the arm gesture. Replay latency is unavailable because clip and wall-clock timestamps differ. " + ("Presence mode runs the actual face/body models and gate. Stranger gesture counts describe attributed roles; inspect labeled clips to verify hand attribution." if args.presence else "Presence mode was not enabled."), "", "| Clip | Expected | Measured |", "| --- | --- | --- |", *rows, ""]
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text("\n".join(report), encoding="utf-8")
    print(f"Report: {args.report}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
