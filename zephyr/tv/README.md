# TV mode: Zephyr on a Roku TV

Zephyr sends the TV the same commands as its remote, over the home Wi-Fi, using Roku's
External Control Protocol (HTTP on port 8060). It drives the Roku OS itself: the home
screen, opening apps, playback, volume, and typing into any on-screen keyboard.

## Gestures while TV mode is on

| Gesture | On the TV |
|---|---|
| Point and move your hand | Arrow keys: the focus moves one tile per short move |
| Quick pinch | OK |
| Quick fist | Play / Pause |
| Two-finger swipe left / right | Forward / Rewind (next page of apps, or skip in a video) |
| Open-hand swipe left / right | Back / Home |
| Fist twist (the knob) | Volume up / down |
| Palm held, then write in the air | The words are typed into the keyboard open on the TV |
| Both palms | Arm or pause Zephyr, as always |
| Your own gesture | Any TV action: `Home`, `launch YouTube`, `type cats`, `mode on`, `mode off` |

Turn TV mode on from the menu bar icon: **Roku TV > Control the TV**. Or record a
gesture in Settings with the action type Roku TV and the value `mode toggle`.

## A real Roku TV

1. The TV and the Mac on the same Wi-Fi.
2. On the TV: Settings > System > Advanced system settings > Control by mobile apps >
   Network access: **Enabled**. Roku OS 14.1 and later refuse key presses otherwise.
3. Menu bar > Roku TV > **Find TV on this Wi-Fi**. If nothing is found (guest or campus
   Wi-Fi often blocks this), use **Type TV address…** with the address from the TV's
   Settings > Network > About.
4. Roku TV > **Control the TV**. The overlay's app chip shows the TV's name.

Quick check from Terminal: `curl -d '' http://<tv address>:8060/keypress/Home`.

Turning the TV on (`PowerOn`) is not in Roku's documented keys, but Roku TVs take it while
Settings > System > Power > **Fast TV Start** is on, which keeps the TV on the Wi-Fi when off.
`PowerOff` always works.

**Campus or guest Wi-Fi** keeps devices from reaching each other, so the TV won't answer
there. Use a phone hotspot (on an iPhone, turn on Maximize Compatibility) and join both the
TV and the Mac to it. The phone sharing the hotspot can't control the TV itself, but the
Mac on that hotspot can.

## The demo without a TV

Menu bar > Roku TV > **Start TV simulator**, then **Control the TV**. A Roku-style TV opens
in the browser at http://127.0.0.1:8060. Put that window on the projector, full screen.
It answers the same protocol on the same port as the real TV, so Zephyr runs the same
code, and the strip along the bottom shows every request it receives.

From the repo: `python -m zephyr.tv.simulator` (`--lan` to run it on a second laptop,
`--limited` to show what a TV in Limited mode says).

Tests: `python -m zephyr.tv.check`
