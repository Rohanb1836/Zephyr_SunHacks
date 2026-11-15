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

## Setup on Windows

Windows 10 or 11, Python 3.11 or 3.12, from the repository root in PowerShell:

```powershell
py -3.11 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python tools/get_models.py
python run.py
```

No permission prompts are needed on Windows. Allow camera access for desktop apps in Settings > Privacy & security > Camera. Differences from the Mac:

- Five-finger swipes switch **virtual desktops** (Win+Ctrl+Left/Right). Create them in Task View (Win+Tab) and put one app on each.
- Palm hold outside Chrome and Spotify opens **Windows Search** (Win+S) instead of Spotlight.
- Volume uses the Windows audio endpoint; brightness works on laptop panels and on monitors with DDC/CI.
- Media gestures send the global media keys, so they control Spotify, browsers and other players.
- The camera uses Media Foundation (30 fps). Set `camera.backend: dshow` in `config.yaml` if a webcam only works with DirectShow.
- If `pip` reports `CERTIFICATE_VERIFY_FAILED` (a network or antivirus proxy), upgrade pip first; recent pip versions use the Windows certificate store.

## Gestures

### Works in every app

| Gesture | Action |
| --- | --- |
| Both palms up, held 1 second | Turn Zephyr on, or pause it |
| Open-hand swipe left / right | Next / previous desktop |
| Quick fist, then open | Play / pause |
| Right fist held, then twisted | Volume (twist right raises, left lowers) |
| Left fist held, then twisted | Brightness (same directions) |
| One finger up | Pointer appears and follows your hand |
| Pinch while pointing | Click |
| Open palm held still, about 1 second | Search |

### Two-finger swipe left / right, by app

Chrome: next / previous tab. Spotify: next / previous track. PowerPoint: next / previous slide. Anywhere else: nothing.

### Air writing

Where: in Word, or after a palm hold opens search. Write: pinch and move to draw; release to end a stroke. Accept: pinch on the preview. Cancel: any swipe. Turning strokes into text needs `ANTHROPIC_API_KEY` in a `.env` file.

### Access

Stranger hands are ignored. Looking away pauses everything (a hand passing in front of your face does not). A handshake gives a guest control; a guest can only use two-finger swipes, play/pause and click. The owner takes control back with both palms.

### Getting reliable gestures

These come from recorded test sessions on a laptop webcam, and apply on any computer:

- **Light on your face, not behind you.** A bright window behind you makes the camera use a slow shutter; fast swipes then blur and the hand tracker loses them.
- **Sit 60 to 80 cm back** and gesture at chest to face height, where the camera can see your hands.
- **Swipes:** a firm sideways movement of the arm with the hand facing the camera and fingers up, then a slower return.
- **Both palms:** raise them together; one palm held up alone for about a second opens Search first.
- **Point up, not at the camera:** hold the pointing finger toward the ceiling. A finger pointed straight at the camera is foreshortened, and about 15% of such hands read as a starting pinch, which freezes the pointer.

### Windows: tuned engine

On Windows, `run.py` uses a copy of the engine tuned on Windows laptop webcams (`zephyr/core_win`, `zephyr/presence_win`, settings in `config_windows.yaml`). The Mac runs `zephyr/core` and `zephyr/presence` with the settings in `config.yaml`, and never reads `config_windows.yaml`. On Windows:

- **Click:** a pinch while pointing counts if released within about 1 second, and not while the hand is moving fast.
- **Play / pause:** a fist held up to about 2 seconds without twisting, then opened.
- **Search:** the palm must face the camera flat; an edge-on hand never opens Search.
- **Writing:** a pinch made while pointing stays a click until the hand moves; pinch and move writes.
- **Volume / brightness:** the knob needs a 15 degree twist, and the left/right hand is judged by which side of your body it is on.
- **Presence:** a hand passing in front of your face does not pause Zephyr; a hand must be within arm's reach of you to count as yours. Looking away pauses only after a clear head turn (about 40 degrees to the side, 35 up or down) held for about 1.2 seconds.

The gesture-to-gesture rules are the same as on the Mac, with the same keys (set in `config.yaml`, override them in `config_windows.yaml`; 0 turns a rule off):

- **Resting hands:** a hand hanging with its fingers toward the floor (more than 120 degrees from upright, `hands.rest_tilt_deg`) starts no fist, swipe or Search, so an arm swinging as you walk past does nothing. Pointing and pinching are not held back.
- **Search needs the palm up:** the palm must lean less than 75 degrees from upright (`holds.palm_max_tilt_deg`); a hand lying sideways never opens it.
- **After a gesture:** for 1.5 seconds after a fist, a knob turn, a click or a pen stroke lets go, the open hand left up does not open Search (`holds.after_release_ms`). Change the hand's shape first.
- **Resting fist:** a fist that grabbed the knob and never turned it lets go after 2.5 seconds (`level.idle_park_ms`), so a chin resting on a fist does not lock out the other hand.
- **Swipe and return:** a swipe also ends when the hand turns back, so swiping again about a second later works without a pause.
- **Pointer after a click:** stays on when the pinch briefly reads as a fist.
- **Knob wobble:** turning the knob back the other way needs 4 points more than carrying on (`level.knob_reverse_step`), so a resting fist wobbling at the edge of the dead zone no longer flicks the level up and down. A deliberate turn back still works.
- **Right click:** pinch twice quickly while pointing (`pinch.double_ms`, 320 ms). A single pinch left-clicks once that time has passed. Works in every app.

### Mac: forgiving limits

The Mac engine shares the fixes below with the Windows one; its limits are in `config.yaml`. They are covered by `python tools/test_gestures_mac.py` and `python tools/test_presence_mac.py` (synthetic hands, no camera) and still need a pass on the demo Mac's camera.

- **Looking away:** pauses only after a clear head turn (about 40 degrees to the side, 35 up or down) held for about 1.2 seconds. A glance at the keyboard, a hand over the face or a missed camera frame does not pause. `presence.mode: off` turns the owner lock off.
- **Click:** a pinch while pointing counts if released within about 0.9 seconds. Where writing is on, pinch and let go clicks; pinch and move writes.
- **Play / pause:** a fist held up to about 2.5 seconds without twisting, then opened (a relaxed open hand counts).
- **Swipes:** a little shorter and slower than before; two-finger swipes can be gentler than open-hand ones; a swipe that blurs out of the picture and comes back to the side still counts.
- **Search:** the palm must face the camera flat; an edge-on hand never opens Search.
- **Pointer and knob:** the pointer holds still under a resting hand; one glitched frame cannot move the volume or brightness.
- **Resting hands:** a hand hanging with its fingers toward the floor (more than 120 degrees from upright, `hands.rest_tilt_deg`) starts no fist, swipe or Search, so an arm swinging as you walk past does nothing. Pointing and pinching are not held back.
- **Search needs the palm up:** the palm must lean less than 75 degrees from upright (`holds.palm_max_tilt_deg`); a hand lying sideways never opens it.
- **After a gesture:** for 1.5 seconds after a fist, a knob turn, a click or a pen stroke lets go, the open hand left up does not open Search (`holds.after_release_ms`). Change the hand's shape first.
- **Resting fist:** a fist that grabbed the knob and never turned it lets go after 2.5 seconds (`level.idle_park_ms`), so a chin resting on a fist does not lock out the other hand.
- **Swipe and return:** a swipe also ends when the hand turns back, so swiping again about a second later works without a pause.
- **Pointer after a click:** stays on when the pinch briefly reads as a fist.
- **Knob wobble:** turning the knob back the other way needs 4 points more than carrying on (`level.knob_reverse_step`), so a resting fist wobbling at the edge of the dead zone no longer flicks the level up and down. A deliberate turn back still works.
- **Pinch with no pointer:** does nothing unless the writing preview is showing, where it accepts (`pinch.tap_needs_pointer`). Before, a resting hand's pinches counted as taps: harmless to the mouse, but in TV mode each one pressed Select.

Each of these rules is a key in `config.yaml`, and 0 turns it off. They are covered on both engines by the shared tests in `tools/flow_tests.py` (synthetic hands, no camera). The tilt limits were measured on the HaGRID v2 test annotations (`tools/hagrid`).

## Test clips

Record a mirrored clip, then press a key in the preview to stop (or pass `--seconds 20`):

```bash
python tools/record_clip.py swipe5_left_x10_ryan.mp4
python tools/replay.py --width 1920 --height 1080
python tools/replay.py --presence clips/two_person_stranger_ryan.mp4
```

For a guided session, `python tools/live_check.py --name ryan` walks through every built-in gesture with on-screen instructions, counts what the engine detects live, saves each round as a clip for replay, and writes `clips/live_ryan.md`.

Use the demo display's actual pixel dimensions. Clips go in `clips/` and are excluded from Git. The replay report is written to `tools/report.md`. The engine starts disarmed, so gesture clips must include the arm gesture before the repetitions; otherwise the gestures will not be evaluated. With `--presence`, replay runs the face and body models and the real gate in recorded time. The report lists guest grants, guest gestures, stranger observations, and suspensions; compare them with the clip's known actions, since an incorrect hand attribution cannot be detected from the reported role alone. Capture-to-event latency is unavailable during replay because video time and the computer's clock differ.

## Accuracy and speed

Measurements are pending testing on the demo Mac. Targets include at least 19/20 detections per built-in gesture, at most one false trigger per two minutes, and median/p95 capture-to-event latency of 30/50 ms. Actual results and any gaps should be added after testing.

Replay of real webcam clips recorded on a Windows laptop (26 clips: each gesture 5 to 10 times, plus 2 minutes of normal movement; `tools/live_check.py`), through both engines. The clips come from one Windows webcam, so the Mac numbers are a guide until the demo Mac is tested:

| Engine | Gestures detected | Events during 2 min of normal movement |
| --- | --- | --- |
| Mac, before the `gesture-robustness` branch | 98 | 125 |
| Mac, with it | 95 | 84 |
| Mac, with it and the knob and no-pointer pinch rules | 95 | 15 |
| Windows, with all of them | 100 | 9 |

Frame to event on the Windows laptop: median 15 to 16 ms, p95 16 to 46 ms (target 30 / 50). Two-finger swipes remain the weakest gesture (2 to 6 of 10 per clip).

## Credits and tools

Team: Aadharsh, Rohan, Swayam, and Ryan. Tools include MediaPipe, OpenCV, PySide6, NumPy, PyAutoGUI, PyObjC, and the Anthropic API. Codex was used as an AI coding assistant. Each teammate's additional AI tools, libraries, and tutorials still need to be collected and credited before submission.
