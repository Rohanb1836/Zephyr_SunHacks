"""Record mirrored test clips: python tools/record_clip.py swipe5_left_x10_ryan.mp4 [--seconds 20]."""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from zephyr.config import cfg
from zephyr.core_win.camera import _backends


def open_camera() -> cv2.VideoCapture | None:
    """Same backend order as the live app, so clips have the live frame rate."""
    for api in _backends():
        camera = cv2.VideoCapture(int(cfg.camera.index), api)
        if camera.isOpened():
            return camera
        camera.release()
    camera = cv2.VideoCapture(int(cfg.camera.index))
    return camera if camera.isOpened() else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Record a mirrored Zephyr test clip")
    parser.add_argument("name", help="Clip filename, e.g. swipe5_left_x10_ryan.mp4")
    parser.add_argument("--seconds", type=float, default=0,
                        help="stop on its own after this many seconds (0: press a key to stop)")
    parser.add_argument("--countdown", type=float, default=3.0, help="seconds before recording starts")
    args = parser.parse_args()
    name = Path(args.name)
    if name.name != args.name or name.suffix.lower() != ".mp4":
        parser.error("name must be an .mp4 filename without a directory")
    output = ROOT / "clips" / name
    if output.exists():
        parser.error(f"clip already exists: {output}")

    camera = open_camera()
    if camera is None:
        print("Could not open the camera. Check camera permissions and camera.index.")
        return 1
    writer = None
    try:
        camera.set(cv2.CAP_PROP_FRAME_WIDTH, int(cfg.camera.width))
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, int(cfg.camera.height))
        camera.set(cv2.CAP_PROP_FPS, int(cfg.camera.fps))
        start = time.monotonic() + max(1.0, args.countdown)
        stop_at = start + args.seconds if args.seconds > 0 else None
        frames = 0
        # The camera's real rate decides the clip's rate. Writing a 15 fps camera into a 30 fps
        # file would replay every movement twice as fast and make the swipe numbers meaningless.
        countdown_frames, countdown_t0 = 0, time.monotonic()
        print("Recording starts after the countdown. Press any key in the preview to stop.")
        while True:
            ok, frame = camera.read()
            if not ok or frame is None:
                print("Camera stopped delivering frames.")
                return 1
            frame = cv2.flip(frame, 1)
            now = time.monotonic()
            remaining = start - now
            if remaining > 0:
                countdown_frames += 1
            else:
                if writer is None:
                    fps = countdown_frames / max(1e-3, now - countdown_t0) if countdown_frames > 10 \
                        else float(cfg.camera.fps)
                    fps = round(fps)
                    output.parent.mkdir(parents=True, exist_ok=True)
                    height, width = frame.shape[:2]
                    writer = cv2.VideoWriter(str(output), cv2.VideoWriter_fourcc(*"mp4v"),
                                             float(fps), (width, height))
                    if not writer.isOpened():
                        print(f"Could not create video: {output}")
                        return 1
                    print(f"Recording at {fps} fps ({width}x{height})")
                writer.write(frame)
                frames += 1
                if stop_at is not None and now >= stop_at:
                    break
            preview = frame.copy()
            if remaining > 0:
                label = str(int(remaining) + 1)
            elif stop_at is not None:
                label = f"Recording - {max(0, stop_at - now):.0f} s left"
            else:
                label = "Recording - press any key to stop"
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
