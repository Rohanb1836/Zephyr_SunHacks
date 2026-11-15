"""Windows action backend. Same behaviour as mac.py, using Win32 calls through ctypes.

Slow calls (audio endpoint, WMI brightness, launching apps) only ever run on the action worker,
never on the vision thread. Every method either works or raises, so the router can show a
failed toast instead of silently doing nothing.
"""
import ctypes
import glob
import logging
import os
import sys
import threading
import time
import webbrowser
from ctypes import wintypes

from .base import ActionBackend

LOG = logging.getLogger(__name__)

# virtual key codes
VK = {'ctrl': 0x11, 'shift': 0x10, 'alt': 0x12, 'win': 0x5B, 'left': 0x25, 'right': 0x27,
      'volumeup': 0xAF, 'volumedown': 0xAE, 'volumemute': 0xAD,
      'playpause': 0xB3, 'next': 0xB0, 'previous': 0xB1}
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP = 0x0001, 0x0002
EXTENDED = {0x25, 0x27, 0x5B, 0xAD, 0xAE, 0xAF, 0xB0, 0xB1, 0xB3}
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
SW_RESTORE = 9
VOLUME_KEY_STEP = 2.0            # Windows moves the volume 2 points per volume key press
BRIGHTNESS_CACHE_S = 2.0         # WMI brightness reads are slow; trust our own last write this long

# friendly names (from the settings window or bindings) to what "start" understands
APP_ALIASES = {
    'microsoft word': 'winword', 'word': 'winword',
    'microsoft powerpoint': 'powerpnt', 'powerpoint': 'powerpnt',
    'microsoft excel': 'excel', 'excel': 'excel',
    'google chrome': 'chrome', 'chrome': 'chrome',
    'microsoft edge': 'msedge', 'edge': 'msedge',
    'spotify': 'spotify', 'notepad': 'notepad', 'calculator': 'calc',
    'file explorer': 'explorer', 'finder': 'explorer',
}
# process names, to bring a running app to the front instead of starting a second copy
APP_PROCESSES = {
    'winword': 'winword.exe', 'powerpnt': 'powerpnt.exe', 'excel': 'excel.exe',
    'chrome': 'chrome.exe', 'msedge': 'msedge.exe', 'spotify': 'spotify.exe', 'notepad': 'notepad.exe',
}


def _percent(value):
    return max(0.0, min(100.0, float(value)))


class WindowsBackend(ActionBackend):
    # palm_hold outside Chrome and Spotify: Windows Search (type a name, Enter launches it)
    launcher_keys = ('win', 's')

    def __init__(self):
        if not sys.platform.startswith('win'):
            raise RuntimeError('WindowsBackend requires Windows')
        import pyautogui
        self.pyautogui = pyautogui
        pyautogui.PAUSE = 0
        pyautogui.FAILSAFE = False  # Zephyr deliberately parks the pointer in a corner.
        self.user32 = ctypes.WinDLL('user32', use_last_error=True)
        self.kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        self.user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
        self.user32.GetSystemMetrics.argtypes = [ctypes.c_int]
        self.user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t]
        self.user32.mouse_event.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                            wintypes.DWORD, ctypes.c_size_t]
        self._com = threading.local()
        self._volume_ep = None
        self._volume_keys = False
        self._volume_estimate = 50.0
        self._brightness = None           # (value, monotonic time) of our last write
        self._brightness_ok = True

    # ---- pointer -------------------------------------------------------------------------
    # Coordinates come from the router already in the same units this returns. Both calls use the
    # process DPI awareness at call time (Qt makes the process per-monitor aware), so they agree.
    def screen_size_points(self):
        return float(self.user32.GetSystemMetrics(0)), float(self.user32.GetSystemMetrics(1))

    def move_cursor(self, x, y):
        self.user32.SetCursorPos(int(round(x)), int(round(y)))

    def click(self, x, y):
        self.move_cursor(x, y)
        self.user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        self.user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)

    # ---- keys ----------------------------------------------------------------------------
    def _key(self, vk, up=False):
        flags = (KEYEVENTF_EXTENDEDKEY if vk in EXTENDED else 0) | (KEYEVENTF_KEYUP if up else 0)
        self.user32.keybd_event(vk, 0, flags, 0)

    def _tap(self, *vks):
        """Press vks in order, release in reverse. Releases even if something fails half way."""
        pressed = []
        try:
            for vk in vks:
                self._key(vk)
                pressed.append(vk)
        finally:
            for vk in reversed(pressed):
                self._key(vk, up=True)

    def hotkey(self, *keys):
        names = []
        for key in keys:
            k = key.lower()
            names.append({'mod': 'ctrl', 'cmd': 'ctrl', 'command': 'ctrl', 'option': 'alt',
                          'escape': 'esc', 'return': 'enter'}.get(k, k))
        self.pyautogui.hotkey(*names)

    def paste_text(self, text):
        import pyperclip
        try:
            previous = pyperclip.paste()
        except Exception:
            previous = None
        try:
            pyperclip.copy(text)
            self.hotkey('ctrl', 'v')
            time.sleep(0.15)
        finally:
            if previous is not None:
                try:
                    pyperclip.copy(previous)
                except Exception:
                    LOG.debug('Could not restore the clipboard', exc_info=True)

    def lock(self):
        if not self.user32.LockWorkStation():
            raise ctypes.WinError(ctypes.get_last_error())

    # ---- apps, files, links --------------------------------------------------------------
    def open_url(self, url):
        if not webbrowser.open(url):
            raise RuntimeError('Could not open URL')

    def open_file(self, path):
        path = os.path.expandvars(os.path.expanduser(path))
        if not os.path.exists(path):
            raise FileNotFoundError(path)
        os.startfile(path)  # noqa: S606 - opens with the user's default app

    def open_app(self, name):
        raw = str(name).strip()
        if not raw:
            raise ValueError('Empty app name')
        alias = APP_ALIASES.get(raw.casefold(), raw)
        exe = APP_PROCESSES.get(alias.casefold())
        if exe and self._activate_process(exe):
            return
        exact, partial = self._find_shortcut(raw)
        if exact:
            os.startfile(exact)  # noqa: S606
            return
        if alias.casefold() == 'spotify' and self._start('spotify:'):
            return
        if self._start(alias):
            return
        if partial:
            os.startfile(partial)  # noqa: S606
            return
        raise RuntimeError(f'Could not find the app "{raw}"')

    @staticmethod
    def _start(target):
        """ShellExecuteEx resolves App Paths names (winword, chrome) and URI schemes (spotify:).
        SEE_MASK_FLAG_NO_UI: an unknown name returns False instead of popping an error dialog."""
        class SHELLEXECUTEINFOW(ctypes.Structure):
            _fields_ = [('cbSize', wintypes.DWORD), ('fMask', wintypes.ULONG), ('hwnd', wintypes.HWND),
                        ('lpVerb', wintypes.LPCWSTR), ('lpFile', wintypes.LPCWSTR),
                        ('lpParameters', wintypes.LPCWSTR), ('lpDirectory', wintypes.LPCWSTR),
                        ('nShow', ctypes.c_int), ('hInstApp', wintypes.HINSTANCE),
                        ('lpIDList', ctypes.c_void_p), ('lpClass', wintypes.LPCWSTR),
                        ('hkeyClass', wintypes.HKEY), ('dwHotKey', wintypes.DWORD),
                        ('hIcon', wintypes.HANDLE), ('hProcess', wintypes.HANDLE)]
        info = SHELLEXECUTEINFOW()
        info.cbSize = ctypes.sizeof(info)
        info.fMask = 0x00000400  # SEE_MASK_FLAG_NO_UI
        info.lpVerb, info.lpFile, info.nShow = 'open', target, 1
        try:
            return bool(ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)))
        except Exception:
            return False

    @staticmethod
    def _find_shortcut(name):
        """(exact, partial) Start menu shortcuts for name, e.g. 'Word.lnk'. The shortest partial
        match wins, so 'Chrome' prefers 'Google Chrome' over 'Chrome Remote Desktop'."""
        roots = [os.path.expandvars(r'%ProgramData%\Microsoft\Windows\Start Menu\Programs'),
                 os.path.expandvars(r'%AppData%\Microsoft\Windows\Start Menu\Programs')]
        want = name.casefold()
        partial = []
        for root in roots:
            for path in glob.glob(os.path.join(root, '**', '*.lnk'), recursive=True):
                stem = os.path.splitext(os.path.basename(path))[0].casefold()
                if 'uninstall' in stem:
                    continue
                if stem == want:
                    return path, None
                if want in stem:
                    partial.append((len(stem), path))
        return None, (min(partial)[1] if partial else None)

    def _activate_process(self, exe_name):
        """Bring the first visible top-level window of exe_name to the front. True if found."""
        from zephyr.actions.frontapp import process_path
        user32 = self.user32
        found = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def visit(hwnd, _):
            if not user32.IsWindowVisible(hwnd) or user32.GetWindowTextLengthW(hwnd) == 0:
                return True
            if user32.GetWindow(hwnd, 4):  # GW_OWNER: skip dialogs and tool windows
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            path = process_path(pid.value)
            if path and os.path.basename(path).casefold() == exe_name.casefold():
                found.append(hwnd)
                return False
            return True

        user32.EnumWindows(visit, 0)
        if not found:
            return False
        hwnd = found[0]
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        # Windows only lets the foreground process change the foreground window. A synthetic
        # Alt press counts as user input and lifts that lock for this call.
        self._tap(VK['alt'])
        user32.SetForegroundWindow(hwnd)
        return True

    # ---- volume --------------------------------------------------------------------------
    def _endpoint(self):
        """IAudioEndpointVolume for the default output. COM is set up once per thread, and the
        endpoint is fetched again after any failure (headphones plugged in, device changed)."""
        if not getattr(self._com, 'ready', False):
            import comtypes
            comtypes.CoInitialize()
            self._com.ready = True
        if self._volume_ep is None:
            from pycaw.pycaw import AudioUtilities
            speakers = AudioUtilities.GetSpeakers()
            ep = getattr(speakers, 'EndpointVolume', None)
            if ep is None:  # older pycaw
                from comtypes import CLSCTX_ALL
                from pycaw.pycaw import IAudioEndpointVolume
                iface = speakers.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                ep = ctypes.cast(iface, ctypes.POINTER(IAudioEndpointVolume))
            self._volume_ep = ep
        return self._volume_ep

    def get_volume(self):
        if not self._volume_keys:
            for attempt in range(2):
                try:
                    return float(self._endpoint().GetMasterVolumeLevelScalar()) * 100
                except Exception as exc:
                    self._volume_ep = None
                    if attempt:
                        LOG.warning('Volume: switching to the volume keys (%r)', exc)
                        self._volume_keys = True
        return self._volume_estimate

    def set_volume(self, value):
        target = _percent(value)
        if not self._volume_keys:
            for attempt in range(2):
                try:
                    ep = self._endpoint()
                    if target > 0 and ep.GetMute():
                        ep.SetMute(0, None)
                    ep.SetMasterVolumeLevelScalar(target / 100, None)
                    return
                except Exception as exc:
                    self._volume_ep = None
                    if attempt:
                        LOG.warning('Volume: switching to the volume keys (%r)', exc)
                        self._volume_keys = True
        presses = int(round((target - self._volume_estimate) / VOLUME_KEY_STEP))
        for _ in range(abs(presses)):
            self._tap(VK['volumeup'] if presses > 0 else VK['volumedown'])
        self._volume_estimate = max(0.0, min(100.0, self._volume_estimate + presses * VOLUME_KEY_STEP))

    # ---- brightness ----------------------------------------------------------------------
    # screen_brightness_control talks to the laptop panel through WMI and to monitors through
    # DDC/CI. Both can take 50 to 300 ms, and the router reads, writes and reads again on every
    # 100 ms tick, so reads come from our own last write while it is fresh.
    def get_brightness(self):
        if self._brightness is not None and time.monotonic() - self._brightness[1] < BRIGHTNESS_CACHE_S:
            return self._brightness[0]
        import screen_brightness_control as sbc
        values = sbc.get_brightness(display=0)
        value = float(values[0] if isinstance(values, (list, tuple)) else values)
        self._brightness = (value, time.monotonic())
        return value

    def set_brightness(self, value):
        import screen_brightness_control as sbc
        target = int(round(_percent(value)))
        if self._brightness is not None and int(round(self._brightness[0])) == target:
            self._brightness = (float(target), time.monotonic())
            return
        sbc.set_brightness(target, display=0)
        self._brightness = (float(target), time.monotonic())

    # ---- media and desktops --------------------------------------------------------------
    def media(self, action):
        if action not in ('playpause', 'next', 'previous'):
            raise ValueError(f'Unknown media action: {action}')
        # global media keys: Spotify, browsers and most players listen to these
        self._tap(VK[action])

    def desktop(self, direction):
        if direction not in ('next', 'prev'):
            raise ValueError(f'Unknown desktop direction: {direction}')
        # Win+Ctrl+Right/Left: next/previous virtual desktop (Task View)
        self._tap(VK['win'], VK['ctrl'], VK['right' if direction == 'next' else 'left'])
