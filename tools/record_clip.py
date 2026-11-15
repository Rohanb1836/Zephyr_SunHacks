"""Record mirrored test clips: python tools/record_clip.py swipe5_left_x10_ryan.mp4."""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from zephyr.config import cfg


def main() -> int:
    parser = argparse.ArgumentParser(description="Record a mirrored Zephyr test clip")
    parser.add_argument("name", help="Clip filename, e.g. swipe5_left_x10_ryan.mp4")
    args = parser.parse_args()
    name = Path(args.name)
    if name.name != args.name or name.suffix.lower() != ".mp4":
        parser.error("name must be an .mp4 filename without a directory")
    output = ROOT / "clips" / name
    if output.exists():
        parser.error(f"clip already exists: {output}")

    camera = cv2.VideoCapture(int(cfg.camera.index))
    if not camera.isOpened():
        print("Could not open the camera. Check camera permissions and camera.index.")
        camera.release()
        return 1
    writer = None
    try:
        camera.set(cv2.CAP_PROP_FRAME_WIDTH, int(cfg.camera.width))
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, int(cfg.camera.height))
        camera.set(cv2.CAP_PROP_FPS, int(cfg.camera.fps))
        start = time.monotonic() + 3.0
        frames = 0
        print("Recording starts after the countdown. Press any key in the preview to stop.")
        while True:
            ok, frame = camera.read()
            if not ok or frame is None:
                print("Camera stopped delivering frames.")
                return 1
            frame = cv2.flip(frame, 1)
            remaining = start - time.monotonic()
            if remaining <= 0:
                if writer is None:
                    output.parent.mkdir(parents=True, exist_ok=True)
                    height, width = frame.shape[:2]
                    writer = cv2.VideoWriter(str(output), cv2.VideoWriter_fourcc(*"mp4v"),
                                             float(cfg.camera.fps), (width, height))
                    if not writer.isOpened():
                        print(f"Could not create video: {output}")
                        return 1
                writer.write(frame)
                frames += 1
            preview = frame.copy()
            label = str(int(remaining) + 1) if remaining > 0 else "Recording - press any key to stop"
            cv2.putText(preview, label, (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                        (0, 0, 255), 2, cv2.LINE_AA)
            cv2.imshow("Zephyr clip recorder", preview)
            if cv2.waitKey(1) & 0xFF != 255:
                break
        print(f"Saved {frames} frames to {output}" if frames else "Cancelled before recording.")
        return 0
    finally:
        if writer is not None:
            writer.release()
        camera.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    raise SystemExit(main())
