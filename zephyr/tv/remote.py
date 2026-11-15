"""zephyr/tv/remote.py  TV mode: gestures drive a Roku TV instead of the Mac. Owner: Swayam.

While TV mode is on, the action router (zephyr/actions/router.py) hands gestures, the
pointer, the volume knob and air-written words here instead of to the Mac:

    Point and move          arrow keys: the TV's focus moves one tile per short hand move
    Quick pinch             OK
    Quick fist              Play / Pause
    Two-finger swipe        Fwd (left) or Rev (right): next page of apps, or skip in a video
    Open-hand swipe         Back (left) or Home (right)
    Fist twist (the knob)   volume up or down
    Palm held               air writing; the words are typed into the keyboard open on the TV
    Your own gesture        any TV action: a key ("Home"), "launch YouTube", "type cats", "mode off"

Both palms still arm and pause Zephyr. TV mode turns on from the menu bar (Roku TV), or
with a gesture bound to the TV action "mode on". The TV's address is kept in
data/tv.json; ZEPHYR_TV_HOST overrides it.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import Callable, Optional

from zephyr.bus import bus as default_bus
from zephyr.contracts import ActionFired, T
from zephyr.tv.ecp import RokuClient, find_tvs, key_name

GESTURE_KEYS = {
    "pinch_tap": ("Select", "OK"),
    "fist_tap": ("Play", "Play / Pause"),
    "swipe2.left": ("Fwd", "Forward"),
    "swipe2.right": ("Rev", "Rewind"),
    "swipe5.left": ("Back", "Back"),
    "swipe5.right": ("Home", "Home"),
}
STEP = 0.12               # pointer travel (share of the pointer box) for one arrow press
VOLUME_STEP = 1.0         # knob percent per volume press
MAX_VOLUME_PRESSES = 3    # per knob tick, so a fast twist can't flood the TV


def _tv_file():
    from zephyr.custom import store
    return store.DATA_DIR / "tv.json"


def load_saved() -> dict[str, str]:
    env = os.environ.get("ZEPHYR_TV_HOST", "").strip()
    if env:
        return {"host": env, "name": ""}
    try:
        data = json.loads(_tv_file().read_text(encoding="utf-8"))
        return {"host": str(data.get("host", "")), "name": str(data.get("name", ""))}
    except (OSError, ValueError):
        return {"host": "", "name": ""}


def save(host: str, name: str = "") -> None:
    from zephyr.custom import store
    try:
        store.atomic_write_bytes(_tv_file(), json.dumps({"host": host, "name": name}, indent=2).encode())
    except Exception as exc:
        print(f"[tv] could not save the TV address: {exc!r}")


class TvRemote:
    def __init__(self, event_bus=None) -> None:
        self.bus = event_bus or default_bus
        self.lock = threading.Lock()
        saved = load_saved()
        self.active = False
        self.searching = False         # palm held: air writing goes to the TV's keyboard
        self.name = saved["name"]
        self.status = "ready" if saved["host"] else "no_tv"
        self.detail = ""
        self.client = RokuClient(saved["host"], on_result=self._result)
        self.on_search: Optional[Callable[[], None]] = None   # set by the router: start air writing
        self._anchor: Optional[list[float]] = None
        self._volume_rest = 0.0
        self._finding = False

    @property
    def label(self) -> str:
        return self.name or "Roku TV"

    @property
    def host(self) -> str:
        return self.client.host

    # ---------- turning TV mode on and off ----------
    def set_mode(self, value) -> bool:
        """'tv', 'on' or True turn TV mode on; 'normal', 'off' or False turn it off; 'toggle' flips it."""
        v = str(value).strip().lower()
        on = (not self.active) if v == "toggle" else v in ("tv", "on", "true", "1")
        with self.lock:
            changed = on != self.active
            self.active = on
            self._anchor = None
            if not on:
                self.searching = False
        if changed:
            self._announce("TV mode on" if on else "TV mode off", True)
        if on and not self.host:
            self.find()
        elif on and changed and not self.name:
            self._fetch_name()
        return on

    # ---------- events from the action router, only while TV mode is on ----------
    def gesture(self, event) -> bool:
        """True when the TV took the gesture, so the Mac does nothing with it."""
        key = event.key
        if key == "palm_hold":
            with self.lock:
                self.searching = True
            if self.on_search:
                self.on_search()
            self._announce("Write in the air", True)
            return True
        if key in GESTURE_KEYS:
            roku_key, label = GESTURE_KEYS[key]
            self.client.key(roku_key, label)
            return True
        if key.startswith("custom."):
            try:
                from zephyr.custom.store import custom_action
                spec = custom_action(key, "tv")
            except Exception:
                spec = None
            if spec and spec.get("type") == "tv":
                try:
                    self.run(spec.get("value"), announce=True, label=spec.get("label"))
                except ValueError as exc:
                    self._announce(str(exc), False)
                return True
            if spec and spec.get("type") == "mode":
                self.set_mode(spec.get("value"))
                return True
        return False          # anything else stays with the Mac: the router runs it as usual

    def pointer(self, event) -> None:
        """The air D-pad: each STEP of pointer travel presses one arrow key."""
        key = None
        with self.lock:
            if not event.visible:
                self._anchor = None
                return
            if event.frozen:              # a pinch is closing: hold still so OK lands where you aimed
                return
            if self._anchor is None:
                self._anchor = [event.x, event.y]
                return
            dx, dy = event.x - self._anchor[0], event.y - self._anchor[1]
            if abs(dx) >= STEP and abs(dx) >= abs(dy):
                key = "Right" if dx > 0 else "Left"
                self._anchor = [self._anchor[0] + (STEP if dx > 0 else -STEP), event.y]
            elif abs(dy) >= STEP:
                key = "Down" if dy > 0 else "Up"
                self._anchor = [event.x, self._anchor[1] + (STEP if dy > 0 else -STEP)]
        if key:
            self.client.key(key)

    def level(self, kind: str, delta: float) -> None:
        """The fist knob turns the TV's volume. Brightness has no TV key, so it does nothing."""
        if kind != "volume" or not delta:
            return
        with self.lock:
            self._volume_rest += delta / VOLUME_STEP
            presses = int(self._volume_rest)
            self._volume_rest -= presses
        presses = max(-MAX_VOLUME_PRESSES, min(MAX_VOLUME_PRESSES, presses))
        for _ in range(abs(presses)):
            if presses > 0:
                self.client.key("VolumeUp", "Volume up")
            else:
                self.client.key("VolumeDown", "Volume down")

    def typed(self, text: str, mode: str = "search") -> None:
        """Air-written words, typed into the on-screen keyboard that is open on the TV."""
        with self.lock:
            self.searching = False
        text = text.strip()
        if text:
            self.client.type_text(text)

    # ---------- the "tv" action type (runner, custom gestures, Settings > Test) ----------
    def run(self, value, announce: bool = False, label: Optional[str] = None) -> None:
        """A key ("Home", "OK", "VolumeUp"), "launch YouTube", "type cats" or "mode on/off/toggle".
        Raises ValueError for anything else, so a Test in Settings shows it failed."""
        text = re.sub(r"^(\w+):\s*", r"\1 ", str(value or "").strip())
        word, _, rest = text.partition(" ")
        word = word.lower()
        if word == "mode" or text.lower().startswith("tv mode"):
            self.set_mode(rest.split()[-1] if rest.split() else "toggle")
            return
        if word in ("launch", "open"):
            if not rest.strip():
                raise ValueError("Say which app, like: launch YouTube")
            self.client.launch(rest.strip(), announce=announce)
            return
        if word == "type":
            self.client.type_text(rest, announce=announce)
            return
        key = key_name(text)
        if key is None:
            raise ValueError(f"Not a TV action: {text}. Try Home, Play, launch YouTube or type cats.")
        self.client.key(key, label or key, announce=announce)

    # ---------- which TV ----------
    def find(self, done: Optional[Callable[[bool], None]] = None) -> None:
        """Look for a Roku on this Wi-Fi in the background, then use and save the first one."""
        with self.lock:
            if self._finding:
                return
            self._finding = True
            self.status, self.detail = "finding", ""

        def work() -> None:
            try:
                tvs = find_tvs()
            except OSError:
                tvs = []
            if tvs:
                self.use(tvs[0]["host"])
            else:
                with self.lock:
                    self.status = "no_tv"
                    self.detail = ("No Roku TV answered on this Wi-Fi. Check the TV is on and on the "
                                   "same network as this Mac, or type its address.")
                self._announce("No Roku TV found", False)
            with self.lock:
                self._finding = False
            if done:
                done(bool(tvs))

        threading.Thread(target=work, name="zephyr-tv-find", daemon=True).start()

    def use(self, host: str, name: str = "") -> None:
        """Talk to the TV at host from now on: "192.168.1.23", or "127.0.0.1" for the simulator."""
        self.client.set_host(host)
        with self.lock:
            self.name = name
            self.status, self.detail = "ready", ""
        save(host, name)
        if not name:
            self._fetch_name()

    def _fetch_name(self) -> None:
        def ask() -> None:
            info = self.client.device_info()
            name = info.get("user-device-name") or info.get("friendly-device-name") or info.get("model-name") or ""
            if name:
                with self.lock:
                    self.name = name
                save(self.host, name)
        self.client.call("Connect to the TV", ask, announce=False)

    def status_text(self) -> str:
        with self.lock:
            status, host = self.status, self.client.host
        if status == "finding":
            return "Looking for a Roku TV…"
        if status == "no_tv":
            return "No TV yet"
        if status == "blocked":
            return "TV refused: turn on Control by mobile apps"
        if status == "error":
            return f"Can't reach the TV at {host}"
        return f"{self.label} at {host}"

    # ---------- feedback on the overlay ----------
    def _result(self, label: str, ok: bool, detail: str) -> None:
        with self.lock:
            if ok:
                self.status, self.detail = "ready", ""
            else:
                self.status = "blocked" if detail.startswith("The TV refused") else "error"
                self.detail = detail
        if not ok:
            print(f"[tv] {label}: {detail}")
        self._announce(label if ok else f"{label} failed", ok)

    def _announce(self, label: str, ok: bool) -> None:
        try:
            self.bus.publish(T.ACTION, ActionFired(label, self.label, ok, int(time.monotonic() * 1000)))
        except Exception:
            pass


_remote: Optional[TvRemote] = None
_remote_lock = threading.Lock()


def tv_remote(event_bus=None) -> TvRemote:
    """The one TvRemote, shared by the action router and the menu bar."""
    global _remote
    with _remote_lock:
        if _remote is None:
            _remote = TvRemote(event_bus)
        return _remote
