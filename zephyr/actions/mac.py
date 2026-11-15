"""macOS action backend. Call slow operations from the action worker."""
import ctypes
import logging
import os
import subprocess
import sys
import time
import webbrowser

from .base import ActionBackend

LOG = logging.getLogger(__name__)
KEY_BRIGHTNESS_UP, KEY_BRIGHTNESS_DOWN = 2, 3      # the keyboard's brightness keys
KEY_STEP = 100 / 16                                # one key press is one of 16 steps


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
        self._brightness_keys = False     # True once DisplayServices has failed: use the brightness keys
        self._ds_unmoved = 0.0            # change asked for through DisplayServices that never showed up
        self._key_level = None            # estimated level while using the keys
        self._key_acc = 0.0

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

    # Brightness. First choice: DisplayServices on the BUILT-IN display (the main display can be a
    # projector or monitor, which has no brightness control). If that errors, or reports success
    # while the level does not move, fall back to pressing the keyboard's brightness keys.
    def _brightness_display(self):
        q = self.quartz
        try:
            err, ids, count = q.CGGetActiveDisplayList(16, None, None)
            if err == 0:
                for display in list(ids)[:count]:
                    if q.CGDisplayIsBuiltin(display):
                        return int(display)
        except Exception:
            pass
        return int(q.CGMainDisplayID())

    def _ds_get(self):
        _, ds = self._display_services()
        value = ctypes.c_float()
        if ds.DisplayServicesGetBrightness(self._brightness_display(), ctypes.byref(value)) != 0:
            raise RuntimeError('Unable to read display brightness')
        return value.value * 100

    def _ds_set(self, value):
        _, ds = self._display_services()
        if ds.DisplayServicesSetBrightness(self._brightness_display(), ctypes.c_float(value / 100)) != 0:
            raise RuntimeError('Unable to change display brightness')

    def _use_brightness_keys(self, reason, level=None):
        if not self._brightness_keys:
            LOG.warning('Brightness: switching to the brightness keys (%s)', reason)
        self._brightness_keys = True
        if self._key_level is None:
            self._key_level = 50.0 if level is None else level

    def _aux_key(self, key):
        from AppKit import NSEvent
        q = self.quartz
        for down in (True, False):
            event = NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
                14, (0, 0), 0xA00 if down else 0xB00, 0, 0, None, 8,
                (key << 16) | ((0xA if down else 0xB) << 8), -1)
            q.CGEventPost(q.kCGHIDEventTap, event.CGEvent())

    def get_brightness(self):
        if not self._brightness_keys:
            try:
                return self._ds_get()
            except Exception as exc:
                self._use_brightness_keys(repr(exc))
        return self._key_level

    def set_brightness(self, value):
        target = _percent(value)
        if not self._brightness_keys:
            before = None
            try:
                before = self._ds_get()
                self._ds_set(target)
                moved = abs(self._ds_get() - before)
                self._ds_unmoved = 0.0 if moved >= 0.2 else self._ds_unmoved + abs(target - before)
                if self._ds_unmoved < 6.0:
                    return
                self._use_brightness_keys('the level did not move', before)
            except Exception as exc:
                self._use_brightness_keys(repr(exc), before)
        # keys: press once for every full step asked for; smaller changes add up until they make a step
        self._key_acc += target - self._key_level
        presses = int(self._key_acc / KEY_STEP)
        self._key_acc -= presses * KEY_STEP
        for _ in range(abs(presses)):
            self._aux_key(KEY_BRIGHTNESS_UP if presses > 0 else KEY_BRIGHTNESS_DOWN)
        # keep the estimate off the ends, so a wrong first guess can never block further presses
        self._key_level = max(KEY_STEP, min(100.0 - KEY_STEP, target))

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
