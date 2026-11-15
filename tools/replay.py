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
    parser.add_argument("--report", type=Path, default=ROOT / "tools/report.md")
    args = parser.parse_args()
    if bool(args.width) != bool(args.height) or args.width < 0 or args.height < 0:
        parser.error("supply both positive --width and --height")
    clips = args.clips or sorted((ROOT / "clips").glob("*.mp4"))
    if not clips:
        print("No clips found.")
        return 1
    captured = collections.defaultdict(list)
    for topic in (T.GESTURE, T.LEVEL, T.POINTER, T.LATENCY):
        bus.subscribe(topic, lambda message, topic=topic: captured[topic].append(message))
    rows = []
    failures = 0
    for path in clips:
        captured.clear()
        try:
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
        latency = [e["publish_ms"] - e["capture_ms"] for e in captured[T.LATENCY]
                   if "publish_ms" in e and "capture_ms" in e
                   and 0 <= e["publish_ms"] - e["capture_ms"] < 60000]
        duration_ms = max((e.get("capture_ms", 0) for e in captured[T.LATENCY]), default=0) - min((e.get("capture_ms", 0) for e in captured[T.LATENCY]), default=0)
        rates = {key: round(value * 60000 / duration_ms, 2) for key, value in counts.items()} if duration_ms > 0 else {}
        details = f"events={dict(counts)}, level events={len(captured[T.LEVEL])}"
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
    report = ["# Zephyr replay report", "", "Targets: gesture detection at least 19/20; false triggers at most 1 per 2 minutes; latency median <= 30 ms and p95 <= 50 ms; pointer jitter <= 4 px.", "", "The engine starts disarmed; clips must include the arm gesture. Owner and handshake results require a presence-aware replay setup and are not inferred here.", "", "| Clip | Expected | Measured |", "| --- | --- | --- |", *rows, ""]
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text("\n".join(report), encoding="utf-8")
    print(f"Report: {args.report}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
