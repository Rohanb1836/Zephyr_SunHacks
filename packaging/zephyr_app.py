"""packaging/zephyr_app.py  Entry point of the double-click Zephyr.app (macOS) and
Zephyr.exe (Windows). Owner: Swayam.

A downloaded app cannot write inside itself and has no terminal, so before
run.py starts this gives Zephyr a home folder:

    Mac:      ~/Library/Application Support/Zephyr/
    Windows:  %APPDATA%\\Zephyr\\
        data/         custom gestures (copied from the app on first launch)
        .env          API keys for air writing and cloud sync, if you use them
        config.yaml   optional: put a full copy of config.yaml here to tune it live
        config_windows.yaml   Windows only, the same for the Windows settings
        zephyr.log    everything Zephyr prints

The menu bar (or tray) icon has "Open Zephyr folder" to get there. Everything
else is the normal run.py. From the repo, `python packaging/zephyr_app.py` runs
the same setup without building, which is a quick check before a build.
"""
from __future__ import annotations

import faulthandler
import json
import multiprocessing
import os
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

WINDOWS = sys.platform.startswith("win")
if WINDOWS:
    HOME = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "Zephyr"
else:
    HOME = Path.home() / "Library" / "Application Support" / "Zephyr"
FROZEN = getattr(sys, "frozen", False)


def bundle_dir() -> Path:
    """Where the app's own files are: inside Zephyr.app, or the repo when run from source."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))


def _log_to_file() -> None:
    """No terminal in an app, so print(), errors and native library output go to zephyr.log."""
    path = HOME / "zephyr.log"
    if path.exists() and path.stat().st_size > 5_000_000:
        path.replace(HOME / "zephyr.old.log")     # keep one older log
    log = open(path, "a", buffering=1, encoding="utf-8")
    os.dup2(log.fileno(), 1)                      # MediaPipe and OpenCV write to the raw outputs
    os.dup2(log.fileno(), 2)
    sys.stdout = sys.stderr = log
    faulthandler.enable(log)                      # a native crash still leaves a trace
    print(f"\n----- Zephyr started {time.strftime('%Y-%m-%d %H:%M:%S')} -----")


def _add_shipped_gestures(data: Path) -> None:
    """The app keeps its own gestures, so ones recorded from the repo after the first launch
    never showed up. Each launch adds any gesture the app was built with that this folder has
    not had yet, once: a gesture you delete stays deleted."""
    seed = bundle_dir() / "data"
    try:
        shipped = json.loads((seed / "bindings.json").read_text(encoding="utf-8")).get("bindings", [])
    except Exception:
        return
    path, seen_path = data / "bindings.json", data / ".shipped.json"
    try:
        mine = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"version": 1, "bindings": []}
        have = {b.get("id") for b in mine.get("bindings", [])}
        seen = set(json.loads(seen_path.read_text(encoding="utf-8"))) if seen_path.exists() else set(have)
        added = []
        for b in shipped:
            if b.get("id") in seen or b.get("id") in have:
                continue
            template = b.get("template")
            if template and (seed / template).exists():
                (data / template).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(seed / template, data / template)
            mine.setdefault("bindings", []).append(b)
            added.append(b.get("id"))
        if added:
            mine["updated_ms"] = int(time.time() * 1000)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(mine, indent=2), encoding="utf-8")
            tmp.replace(path)
            print(f"[app] added gestures from this build: {', '.join(map(str, added))}")
        seen_path.write_text(json.dumps(sorted(seen | {b.get("id") for b in shipped})), encoding="utf-8")
    except Exception as exc:
        print(f"[app] could not add this build's gestures: {exc!r}")


def prepare() -> Path:
    """Home folder, gestures, config and keys. Safe to run on every launch."""
    HOME.mkdir(parents=True, exist_ok=True)
    if FROZEN:
        _log_to_file()

    data = HOME / "data"
    if not (data / "bindings.json").exists():
        seed = bundle_dir() / "data"
        if seed.is_dir():
            shutil.copytree(seed, data, dirs_exist_ok=True)   # gestures shipped with the app
    (data / "gestures").mkdir(parents=True, exist_ok=True)
    _add_shipped_gestures(data)
    os.environ["ZEPHYR_DATA"] = str(data)
    os.environ["ZEPHYR_HOME"] = str(HOME)

    # the app's config, unless a full copy was put in the home folder to tune
    user_cfg = HOME / "config.yaml"
    os.environ["ZEPHYR_CONFIG"] = str(user_cfg if user_cfg.exists() else bundle_dir() / "config.yaml")
    if WINDOWS:
        user_win = HOME / "config_windows.yaml"
        os.environ["ZEPHYR_WINDOWS_CONFIG"] = str(user_win if user_win.exists() else bundle_dir() / "config_windows.yaml")

    # keys live next to the user's files, never inside the app. An app opened from
    # Finder does not see variables exported in Terminal, so they go in this .env
    try:
        from dotenv import load_dotenv
        load_dotenv(HOME / ".env")
    except Exception as exc:
        print(f"[app] could not read {HOME / '.env'}: {exc!r}")

    # run.py's own load_dotenv() looks in the current folder when frozen
    os.chdir(HOME)
    # Finder can add launch arguments run.py does not know (and Explorer passes none we need)
    sys.argv = sys.argv[:1]
    return HOME


def _alert(title: str, message: str) -> None:
    """An alert that works before (or without) Qt."""
    if WINDOWS:
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)   # MB_ICONERROR
        except Exception:
            pass
        return
    # json.dumps gives a double-quoted string with backslash escapes, which AppleScript reads
    script = (f"display alert {json.dumps(title, ensure_ascii=False)} "
              f"message {json.dumps(message, ensure_ascii=False)} as critical")
    try:
        subprocess.run(["osascript", "-e", script], timeout=300, check=False)
    except Exception:
        pass


def main() -> int:
    multiprocessing.freeze_support()   # PyInstaller needs this if anything starts a child process
    prepare()
    if not FROZEN:
        sys.path.insert(0, str(bundle_dir()))   # run.py sits at the repo root
    try:
        import run
        return int(run.main() or 0)
    except SystemExit as exc:
        if isinstance(exc.code, str):
            print(exc.code)
            _alert("Zephyr stopped", exc.code)
            return 1
        return int(exc.code or 0)
    except Exception:
        traceback.print_exc()
        _alert("Zephyr could not start", f"Details are in {HOME / 'zephyr.log'}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
