# Zephyr

Zephyr is a desktop app for controlling a computer with hand gestures captured by a webcam. It is designed for hands-free use, including accessibility and presentation scenarios. The project combines an owner lock, handshake-based guest access, app-aware gestures, custom gestures, and air writing.

The application is under development. Features and accuracy targets below must be verified on the demo Mac; no accuracy measurements are claimed here yet. The owner lock is an approximate camera-based interaction control, not an identity or security system.

## Setup on macOS

Use Python 3.11 or 3.12 and run from the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python tools/get_models.py
python run.py
```

Grant Camera, Accessibility, and Automation permissions to the terminal app used to launch Zephyr in System Settings > Privacy & Security. Launch from that same terminal. The first camera permission prompt may require opening the camera from the main thread.

Useful options: `python run.py --no-ui`, `--no-presence`, `--log-actions`, or `--clip clips/example.mp4`. The app falls back to stubs when components are not yet available; a running app does not by itself prove the owner lock is active.

## Intended gestures

| Gesture | Action |
| --- | --- |
| Both palms held | Arm, pause, or take back control |
| Five-finger swipe | Move between desktops |
| Two-finger swipe | Move within the current app |
| One palm held | Open search and writing |
| Left or right fist held | Adjust brightness or volume |
| Quick fist | Play or pause media |
| Point and pinch | Move the pointer and click |
| Pinch and hold in writing context | Write in the air |
| Recorded custom pose | Run its configured action |
| Handshake | Give a guest control |

## Test clips

Record a mirrored clip on the Mac, then press a key in the preview to stop:

```bash
python tools/record_clip.py swipe5_left_x10_ryan.mp4
python tools/replay.py --width 1920 --height 1080
```

Use the demo display's actual pixel dimensions. Clips go in `clips/` and are excluded from Git. The replay report is written to `tools/report.md`. The engine starts disarmed, so gesture clips must include the arm gesture before the repetitions; otherwise the gestures will not be evaluated. Presence-aware replay is not yet implemented, and timing measurements are unavailable where clip and publication timestamps use different clocks.

## Accuracy and speed

Measurements are pending testing on the demo Mac. Targets include at least 19/20 detections per built-in gesture, at most one false trigger per two minutes, and median/p95 capture-to-event latency of 30/50 ms. Actual results and any gaps should be added after testing.

## Credits and tools

Team: Aadharsh, Rohan, Swayam, and Ryan. Tools include MediaPipe, OpenCV, PySide6, NumPy, PyAutoGUI, PyObjC, and the Anthropic API. Codex was used as an AI coding assistant. Each teammate's additional AI tools, libraries, and tutorials still need to be collected and credited before submission.
