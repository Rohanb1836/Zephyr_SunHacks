"""Identify the foreground app and load its matching profile."""
import json
import sys
import threading
from pathlib import Path

PROFILE_DIR = Path(__file__).with_name('profiles')


def load_profiles():
    return {p.stem: json.loads(p.read_text(encoding='utf-8')) for p in PROFILE_DIR.glob('*.json')}


def front_app():
    if sys.platform != 'darwin':
        raise NotImplementedError('Foreground app detection currently supports macOS only')
    from AppKit import NSRunningApplication, NSWorkspace
    if threading.current_thread() is threading.main_thread():
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is not None:
            return str(app.bundleIdentifier() or ''), str(app.localizedName() or '')
    # NSWorkspace can return stale information on a thread without a Cocoa run loop.
    # The window server returns on-screen windows in front-to-back order.
    import Quartz
    options = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
    windows = Quartz.CGWindowListCopyWindowInfo(options, Quartz.kCGNullWindowID) or []
    for window in windows:
        if window.get('kCGWindowLayer', 1) != 0:
            continue
        pid = window.get('kCGWindowOwnerPID')
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid) if pid else None
        if app is None:
            continue
        app_id = str(app.bundleIdentifier() or '')
        name = str(app.localizedName() or '')
        if app_id and name.lower() not in ('python', 'python3', 'zephyr'):
            return app_id, name
    # Keep the original method as a fallback when window information is unavailable.
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    if app is not None:
        return str(app.bundleIdentifier() or ''), str(app.localizedName() or '')
    return '', ''


def find_profile(app_id, profiles, platform=None):
    platform = platform or ('mac' if sys.platform == 'darwin' else 'win')
    for profile in profiles.values():
        candidates = profile.get('match', {}).get(platform, [])
        if app_id.casefold() in {item.casefold() for item in candidates}:
            return profile
    return profiles['default']
