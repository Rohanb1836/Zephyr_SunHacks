"""Identify the foreground app and load its matching profile."""
import json
import os
import sys
import threading
from pathlib import Path

PROFILE_DIR = Path(__file__).with_name('profiles')


def load_profiles():
    return {p.stem: json.loads(p.read_text(encoding='utf-8')) for p in PROFILE_DIR.glob('*.json')}


def process_path(pid):
    """Windows: full exe path of a process id, or '' (also for protected processes)."""
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    handle = kernel32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ''
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
        return ''
    finally:
        kernel32.CloseHandle(handle)


_labels = {}


def _exe_label(path):
    """'Google Chrome' from chrome.exe's FileDescription, cached; the file name as a fallback."""
    if path in _labels:
        return _labels[path]
    label = os.path.splitext(os.path.basename(path))[0]
    try:
        import win32api
        lang, page = win32api.GetFileVersionInfo(path, r'\VarFileInfo\Translation')[0]
        desc = win32api.GetFileVersionInfo(path, rf'\StringFileInfo\{lang:04x}{page:04x}\FileDescription')
        if desc and desc.strip():
            label = desc.strip()
    except Exception:
        pass
    _labels[path] = label
    return label


def _front_app_windows():
    """(exe name, friendly label) of the foreground window, e.g. ('chrome.exe', 'Google Chrome').
    Store apps run inside ApplicationFrameHost.exe; their real process owns a child window."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return '', ''
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    path = process_path(pid.value)
    if os.path.basename(path).casefold() == 'applicationframehost.exe':
        host, found = pid.value, []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def visit(child, _):
            child_pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(child, ctypes.byref(child_pid))
            if child_pid.value != host:
                found.append(child_pid.value)
                return False
            return True

        user32.EnumChildWindows(hwnd, visit, 0)
        if found:
            path = process_path(found[0]) or path
    if not path:
        return '', ''
    return os.path.basename(path), _exe_label(path)


def front_app():
    if sys.platform.startswith('win'):
        return _front_app_windows()
    if sys.platform != 'darwin':
        raise NotImplementedError('Foreground app detection supports macOS and Windows only')
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
