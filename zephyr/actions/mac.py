"""macOS action backend. Call slow operations from the action worker."""
import ctypes
import os
import subprocess
import sys
import time
import webbrowser

from .base import ActionBackend


def _run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=5).stdout.strip()


def _percent(value):
    return max(0.0, min(100.0, float(value)))


class MacBackend(ActionBackend):
    def __init__(self):
        if sys.platform != "darwin":
            raise RuntimeError("MacBackend requires macOS")
        import Quartz
        import pyautogui
        self.quartz = Quartz
        self.pyautogui = pyautogui
        pyautogui.PAUSE = 0
        pyautogui.FAILSAFE = False  # Zephyr deliberately parks the pointer in a corner.
        self._brightness_frameworks = None

    def screen_size_points(self):
        q = self.quartz
        bounds = q.CGDisplayBounds(q.CGMainDisplayID())
        return float(bounds.size.width), float(bounds.size.height)

    def move_cursor(self, x, y):
        q = self.quartz
        event = q.CGEventCreateMouseEvent(None, q.kCGEventMouseMoved, (x, y), q.kCGMouseButtonLeft)
        q.CGEventPost(q.kCGHIDEventTap, event)

    def click(self, x, y):
        q = self.quartz
        for kind in (q.kCGEventLeftMouseDown, q.kCGEventLeftMouseUp):
            event = q.CGEventCreateMouseEvent(None, kind, (x, y), q.kCGMouseButtonLeft)
            q.CGEventPost(q.kCGHIDEventTap, event)

    def hotkey(self, *keys):
        self.pyautogui.hotkey(*(('command' if key.lower() == 'mod' else key) for key in keys))

    def paste_text(self, text):
        import pyperclip
        previous = pyperclip.paste()
        try:
            pyperclip.copy(text)
            self.hotkey('mod', 'v')
            time.sleep(0.15)
        finally:
            pyperclip.copy(previous)

    def open_url(self, url):
        if not webbrowser.open(url):
            raise RuntimeError('Could not open URL')

    def open_app(self, name):
        _run('open', '-a', name)

    def open_file(self, path):
        _run('open', os.path.expanduser(path))

    def get_volume(self):
        return float(_run('osascript', '-e', 'output volume of (get volume settings)'))

    def set_volume(self, value):
        _run('osascript', '-e', f'set volume output volume {round(_percent(value))}')

    def _display_services(self):
        if self._brightness_frameworks is None:
            cg = ctypes.CDLL('/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics')
            ds = ctypes.CDLL('/System/Library/PrivateFrameworks/DisplayServices.framework/DisplayServices')
            cg.CGMainDisplayID.restype = ctypes.c_uint32
            ds.DisplayServicesGetBrightness.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_float)]
            ds.DisplayServicesGetBrightness.restype = ctypes.c_int
            ds.DisplayServicesSetBrightness.argtypes = [ctypes.c_uint32, ctypes.c_float]
            ds.DisplayServicesSetBrightness.restype = ctypes.c_int
            self._brightness_frameworks = cg, ds
        return self._brightness_frameworks

    def get_brightness(self):
        cg, ds = self._display_services()
        value = ctypes.c_float()
        if ds.DisplayServicesGetBrightness(cg.CGMainDisplayID(), ctypes.byref(value)) != 0:
            raise RuntimeError('Unable to read display brightness')
        return value.value * 100

    def set_brightness(self, value):
        cg, ds = self._display_services()
        if ds.DisplayServicesSetBrightness(cg.CGMainDisplayID(), ctypes.c_float(_percent(value) / 100)) != 0:
            raise RuntimeError('Unable to change display brightness')

    def media(self, action):
        commands = {'playpause': 'playpause', 'next': 'next track', 'previous': 'previous track'}
        if action not in commands:
            raise ValueError(f'Unknown media action: {action}')
        _run('osascript', '-e', f'tell application "Spotify" to {commands[action]}')

    def desktop(self, direction):
        keys = {'next': 124, 'prev': 123}
        if direction not in keys:
            raise ValueError(f'Unknown desktop direction: {direction}')
        _run('osascript', '-e', f'tell application "System Events" to key code {keys[direction]} using control down')
