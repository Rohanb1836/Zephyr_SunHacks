# Zephyr.app (macOS)

Zephyr as a normal Mac app: double-click it and it lives in the menu bar. No Terminal, no Dock icon. Mac only.
A Mac app has to be built on a Mac, so one person builds it once and shares the `.dmg`.

## Build (once, on a Mac)

1. In the repo, activate the project venv (Python 3.11 or 3.12).
2. Run `bash packaging/build_mac.sh`. It installs PyInstaller, downloads the models, builds, signs and makes the disk image. Takes a few minutes.
3. You get `packaging/dist/Zephyr.app` and `packaging/dist/Zephyr.dmg`, and Finder opens on them.

The build Mac decides where the app runs: built on Apple Silicon, it runs on Apple Silicon Macs (an Intel Mac needs its own build), on that macOS version and newer.

## Install

1. Open `Zephyr.dmg` and drag Zephyr onto Applications.
2. Open Zephyr from Applications or Spotlight. Its icon, a zigzag stroke ending in a dot, appears in the menu bar.
3. A copy downloaded from the internet is blocked the first time, because it is not notarized (that needs a paid Apple Developer account). Click Done, then go to System Settings > Privacy & Security, scroll down to Security, click Open Anyway next to Zephyr and enter your password. Only needed once. A copy built on the same Mac opens straight away.

To start it with the Mac: System Settings > General > Login Items > + > Zephyr.

## First launch: three permissions

| macOS asks for | Do this | Used for |
|---|---|---|
| Camera | Allow | hand tracking |
| Accessibility | Open System Settings, turn on Zephyr | pointer, clicks, keys |
| Control Spotify / System Events | OK, the first time a gesture uses them | media control, switching desktops |

If the pointer still does not move after allowing Accessibility, quit Zephyr from its menu bar icon and open it again.
Clicked Deny by mistake? The menu bar icon then has **Allow Accessibility…**.

## Where Zephyr keeps your files

Menu bar icon > **Open Zephyr folder** opens `~/Library/Application Support/Zephyr`:

- `data/` your custom gestures. The first launch starts with the gestures that were in the repo's `data/` at build time.
- `.env` keys for air writing (`ANTHROPIC_API_KEY`, or AWS keys for Bedrock, and `SUPERMEMORY_API_KEY`). The build script copies the repo's `.env` here on the build Mac. On another Mac, copy your `.env` into this folder (Cmd+Shift+. shows hidden files in Finder). An app opened from Finder does not see variables exported in Terminal.
- `config.yaml` optional: copy the repo's `config.yaml` here to tune the app. It reloads live, like the repo one.
- `zephyr.log` everything Zephyr prints. Look here first when something is off.

Gesture cloud sync uses your `~/.aws` profile, same as from the repo.
`python run.py` from the repo works as before and keeps using the repo's `data/`, separate from the app's.

## Rebuilding

Each build is a new app to macOS, so it asks for Camera and Accessibility again: in Privacy & Security > Accessibility, remove the old Zephyr with − and allow the new one. Build the demo copy early and leave it alone.

To keep the permissions across rebuilds, sign with your own certificate. Keychain Access > Certificate Assistant > Create a Certificate…, name `Zephyr Dev`, identity type Self Signed Root, certificate type Code Signing. Then build with

    ZEPHYR_SIGN_ID="Zephyr Dev" bash packaging/build_mac.sh

and click Always Allow when codesign asks for the key.

## Sharing

`Zephyr.dmg` is large, so attach it to a GitHub release of the repo and link that from Devpost.
Only the files listed in `Zephyr.spec` go into the app. Never add `.env`, `~/.aws` or clips there.

## Quick check without building

`python packaging/zephyr_app.py` from the repo runs the app's own setup (the Application Support folder, its `.env`, its gestures), so a broken setup shows up before a build.

## Troubleshooting

- **"Zephyr could not start"**: the alert names `zephyr.log`; the error is at the end of it.
- **Camera light never comes on**: System Settings > Privacy & Security > Camera > turn on Zephyr, then reopen it.
- **Pointer does not move**: Privacy & Security > Accessibility. After a rebuild, remove Zephyr with − and allow it again.
