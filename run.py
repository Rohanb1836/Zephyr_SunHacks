"""run.py    Entry point. Owner: Aadharsh.

    python run.py                    full app
    python run.py --no-ui            no Qt window (vision + actions only)
    python run.py --no-presence      skip face/body tracking (NullGate: every hand is the owner)
    python run.py --log-actions      print gestures instead of doing OS actions (Windows testing)
    python run.py --clip x.mp4       use a recorded clip instead of the camera

Every module fails soft: if it is missing or crashes on start, a stub takes its place.
"""
import argparse
import os
import sys
import threading

from dotenv import load_dotenv

from zephyr.stubs import NullGate, NullMatcher, log_actions


def _drop_opencv_qt_env() -> None:
    """opencv-python ships its own Qt and, on import, points QT_QPA_PLATFORM_PLUGIN_PATH (and the
    font dir) at it. PySide6 then loads the wrong platform plugin ("could not find the Qt platform
    plugin cocoa"). Remove those settings before Qt starts, but only when they point inside cv2."""
    for key in ("QT_QPA_PLATFORM_PLUGIN_PATH", "QT_QPA_FONTDIR", "QT_PLUGIN_PATH"):
        val = os.environ.get(key, "")
        if "cv2" in val.replace("\\", "/").split("/"):
            os.environ.pop(key, None)


def _unhide_qt_plugins() -> None:
    """macOS: Qt skips plugin files that carry the hidden flag (UF_HIDDEN), then fails with
    'could not find the Qt platform plugin "cocoa" in ""'. Clear the flag on PySide6's plugins."""
    if sys.platform != "darwin":
        return
    try:
        import stat
        from pathlib import Path

        import PySide6
        root = Path(PySide6.__file__).resolve().parent / "Qt" / "plugins"
        fixed = 0
        for path in [root, *root.rglob("*")]:
            st = os.lstat(path)
            if st.st_flags & stat.UF_HIDDEN:
                os.chflags(path, st.st_flags & ~stat.UF_HIDDEN, follow_symlinks=False)
                fixed += 1
        if fixed:
            print(f"[run] cleared the macOS hidden flag on {fixed} Qt plugin file(s)")
    except Exception as exc:
        print(f"[run] could not check Qt plugin flags: {exc!r}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Zephyr: hands-free computer control")
    ap.add_argument("--no-ui", action="store_true", help="run without the Qt overlay")
    ap.add_argument("--no-presence", action="store_true", help="skip face/body tracking (NullGate)")
    ap.add_argument("--log-actions", action="store_true", help="print gestures instead of OS actions")
    ap.add_argument("--clip", default=None, help="recorded clip to use instead of the camera")
    args = ap.parse_args()

    # Windows consoles default to cp1252: one non-ASCII character in a log line ("…", "×")
    # would raise UnicodeEncodeError inside whichever thread printed it.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    load_dotenv()   # ANTHROPIC_API_KEY, AWS keys, SUPERMEMORY_API_KEY from .env (git-ignored)

    from zephyr.core.camera import FrameHub
    from zephyr.core.engine import Engine

    hub = FrameHub(clip=args.clip)
    hub.start()    # opens the camera here, on the main thread, before Qt starts

    gate, matcher = NullGate(), NullMatcher()
    if not args.no_presence:
        try:
            from zephyr.presence.thread import start_presence
            gate = start_presence(hub)
        except Exception as exc:
            print("[run] presence not available, using NullGate:", repr(exc))
    try:
        from zephyr.custom.matcher import Matcher
        matcher = Matcher()
    except Exception as exc:
        print("[run] custom matcher not available, using NullMatcher:", repr(exc))

    if args.log_actions:
        log_actions()
    else:
        try:
            from zephyr.actions.router import start_actions
            start_actions()
        except Exception as exc:
            print("[run] actions not available, logging instead:", repr(exc))
            log_actions()

    Engine(hub, gate, matcher).start()

    if not args.no_ui:
        _drop_opencv_qt_env()
        _unhide_qt_plugins()
        try:
            from zephyr.ui.app import run_ui
        except Exception as exc:
            print("[run] ui not available, running headless:", repr(exc))
        else:
            return run_ui()

    print("[run] running headless. Ctrl+C to quit.")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
