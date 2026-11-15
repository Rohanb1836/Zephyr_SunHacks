"""zephyr/tv/check.py  Tests for TV mode, against the TV simulator. Owner: Swayam.

    python -m zephyr.tv.check

1. The ECP client against the simulator: keys, launch by name, typing, queries, refusals.
2. Find TV: the simulator answers the SSDP search on this machine (skipped if it can't).
3. TvRemote: the pointer D-pad, gestures, the knob, "tv" action values, custom gestures.
4. The real action router in TV mode: everything goes to the TV and nothing touches the
   Mac; back in normal mode the Mac gets the click and the pointer again.
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np

from zephyr.bus import bus
from zephyr.contracts import (ActionRequest, GestureEvent, GestureKind, LevelEvent, PointerEvent, Role, Side,
                              SystemMode, SystemState, T)
from zephyr.custom import store, sync
from zephyr.tv import ecp, simulator
from zephyr.tv.remote import TvRemote

FAILS: list[str] = []


def check(name: str, ok, detail: str = "") -> None:
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  {detail}"))
    if not ok:
        FAILS.append(name)


def settle(client: ecp.RokuClient, timeout: float = 5.0) -> bool:
    """Wait until every request queued so far has been sent (the sender works in order)."""
    done = threading.Event()
    client.call("settle", done.set, announce=False)
    return done.wait(timeout)


def wait_for(fn, timeout: float = 4.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = fn()
        if value:
            return value
        time.sleep(0.02)
    return fn()


def gesture(kind: GestureKind, **kw) -> GestureEvent:
    return GestureEvent(kind, int(time.monotonic() * 1000), 1, Side.RIGHT, Role.OWNER, **kw)


def host_of(server) -> str:
    return f"127.0.0.1:{server.server_address[1]}"


class FakeBackend:
    """Records what would have happened on the Mac."""
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def screen_size_points(self):
        return (1440.0, 900.0)

    def get_volume(self):
        return 50.0

    def get_brightness(self):
        return 50.0

    def __getattr__(self, name):
        def record(*args):
            self.calls.append((name, args))
        return record


def client_tests() -> None:
    srv = simulator.serve(port=0, find_tv=False)
    tv = srv.state
    c = ecp.RokuClient(host_of(srv))
    c.press("Right")
    check("Right moves the focus", tv.focus == 1)
    c.press("Down")
    check("Down moves a row", tv.focus == 5)
    check("query/apps lists YouTube as 837", c.apps().get("youtube", ("",))[0] == "837")
    check("launch by name opens it", c.launch_now("youtube") == "YouTube" and tv.screen == "app")
    c.press("Play")
    check("Play pauses", tv.playing is False)
    c.press("Up")
    c.press("Select")
    check("the app's Search opens the keyboard", tv.screen == "search")
    c.type_now("lofi beats")
    check("typing fills the search box", tv.query == "lofi beats", tv.query)
    c.press("Backspace")
    check("Backspace deletes", tv.query == "lofi beat")
    c.press("Home")
    check("Home goes home", tv.screen == "home")
    check("device-info has the TV's name", c.device_info().get("user-device-name") == simulator.NAME)
    for _ in range(3):
        c.press("VolumeUp")
    check("volume up three steps", tv.volume == 21)
    try:
        c.press("Dance")
        msg = ""
    except ecp.EcpError as exc:
        msg = str(exc)
    check("an unknown key is refused", "400" in msg, msg)
    limited = simulator.serve(port=0, limited=True, find_tv=False)
    try:
        ecp.request(host_of(limited), "/keypress/Home")
        msg = ""
    except ecp.EcpError as exc:
        msg = str(exc)
    check("Limited mode says which TV setting to change", "Control by mobile apps" in msg, msg)
    try:
        ecp.request("127.0.0.1:9", "/keypress/Home", timeout=0.5)
        msg = ""
    except ecp.EcpError as exc:
        msg = str(exc)
    check("no TV gives a clear message", "Can't reach the TV" in msg, msg)
    check("key names and aliases", ecp.key_name("ok") == "Select" and ecp.key_name("volume up") == "VolumeUp"
          and ecp.key_name("HOME") == "Home" and ecp.key_name("dance") is None and ecp.key_name("power") is None)
    check("power on and off", ecp.key_name("PowerOn") == "PowerOn" and ecp.key_name("turn on") == "PowerOn"
          and ecp.key_name("turn off") == "PowerOff")
    c.press("PowerOff")
    off = tv.screen == "off"
    c.press("PowerOn")
    check("the TV turns off and back on", off and tv.screen == "home")
    try:
        ecp.request("127.0.0.1:9", "/keypress/PowerOn", timeout=0.5)
        msg = ""
    except ecp.EcpError as exc:
        msg = str(exc)
    check("turning on an unreachable TV points at Fast TV Start", "Fast TV Start" in msg, msg)


def find_tests() -> None:
    srv = simulator.serve(port=0, find_tv=True)
    tvs = ecp.find_tvs(timeout=1.5)
    if not tvs:
        print("SKIP Find TV: this machine doesn't pass SSDP to itself")
        return
    check("Find TV finds the simulator", any(t["host"] == host_of(srv) for t in tvs), str(tvs))


def remote_tests() -> None:
    srv = simulator.serve(port=0, find_tv=False)
    tv = srv.state
    toasts: list = []
    bus.subscribe(T.ACTION, toasts.append)
    r = TvRemote(bus)
    r.use(host_of(srv))
    settle(r.client)
    check("the TV's name comes from device-info", r.label == simulator.NAME, r.label)
    saved = json.loads((store.DATA_DIR / "tv.json").read_text(encoding="utf-8"))
    check("the TV address is saved", saved["host"] == host_of(srv), str(saved))
    r.set_mode("tv")
    check("TV mode turns on", r.active)

    for x, y in ((0.5, 0.5), (0.63, 0.5), (0.76, 0.5)):
        r.pointer(PointerEvent(True, x, y))
    settle(r.client)
    check("pointer two steps right: focus two tiles right", tv.focus == 2, str(tv.focus))
    r.pointer(PointerEvent(True, 0.76, 0.63))
    settle(r.client)
    check("pointer one step down: one row down", tv.focus == 6, str(tv.focus))
    r.pointer(PointerEvent(True, 0.4, 0.63, frozen=True))
    settle(r.client)
    check("a frozen pointer (pinch closing) sends nothing", tv.focus == 6)
    r.pointer(PointerEvent(False))
    r.pointer(PointerEvent(True, 0.1, 0.1))
    settle(r.client)
    check("after the pointer hides, pointing again starts fresh", tv.focus == 6)

    r.gesture(gesture(GestureKind.PINCH_TAP, pos=(0.5, 0.5)))
    settle(r.client)
    check("quick pinch = OK: opens the focused app", tv.screen == "app" and tv.app == simulator.APPS[6])
    r.gesture(gesture(GestureKind.FIST_TAP))
    settle(r.client)
    check("quick fist pauses", tv.playing is False)
    before = tv._pos(time.monotonic())
    r.gesture(gesture(GestureKind.SWIPE, direction="left", fingers=2))
    settle(r.client)
    check("two-finger swipe left skips forward", tv._pos(time.monotonic()) >= before + 9)
    r.gesture(gesture(GestureKind.SWIPE, direction="right", fingers=5))
    settle(r.client)
    check("open-hand swipe right goes home", tv.screen == "home")
    vol = tv.volume
    r.level("volume", 2.0)
    r.level("brightness", 5.0)
    settle(r.client)
    check("the knob turns the TV volume, brightness does nothing", tv.volume == vol + 2, str(tv.volume))
    r.level("volume", -0.6)
    r.level("volume", -0.6)
    settle(r.client)
    check("small knob turns add up", tv.volume == vol + 1, str(tv.volume))

    r.run("launch Netflix")
    settle(r.client)
    check("tv action: launch by name", tv.screen == "app" and tv.app[1] == "Netflix")
    for value in ("Home", "Left", "Down", "OK"):
        r.run(value)
    settle(r.client)
    check("tv actions: menu > Search opens the keyboard", tv.screen == "search")
    r.run("type: cats")
    settle(r.client)
    check("tv action: type text", tv.query == "cats", tv.query)
    try:
        r.run("dance")
        refused = False
    except ValueError:
        refused = True
    check("an unknown TV action is refused", refused)
    r.run("mode off")
    check("tv action: mode off", not r.active)
    r.run("mode toggle")
    check("tv action: mode toggle", r.active)

    tmpl = np.zeros(63, np.float32)
    tv_binding = store.add_binding("Shaka", tmpl, 0.2, {"type": "tv", "value": "launch YouTube", "label": "YouTube on TV"},
                                   scope=["tv"])
    mac_binding = store.add_binding("Peace", tmpl, 0.2, {"type": "url", "value": "https://devpost.com", "label": "Devpost"})
    took = r.gesture(gesture(GestureKind.CUSTOM, name=tv_binding["id"]))
    settle(r.client)
    check("a gesture bound to a TV action drives the TV", took and tv.screen == "app" and tv.app[1] == "YouTube")
    check("a gesture bound to a Mac action is left for the Mac", r.gesture(gesture(GestureKind.CUSTOM, name=mac_binding["id"])) is False)

    r.run("Up")
    r.run("OK")
    settle(r.client)
    started: list = []
    r.on_search = lambda: started.append(True)
    took = r.gesture(gesture(GestureKind.PALM_HOLD))
    check("a held palm starts air writing for the TV", took and started and r.searching)
    r.typed("lofi", "search")
    settle(r.client)
    check("air-written words are typed on the TV", tv.screen == "search" and tv.query == "lofi" and not r.searching, tv.query)
    check("the overlay gets TV feedback", any(getattr(t, "app_label", "") == simulator.NAME for t in toasts))
    r.set_mode("off")


def router_tests() -> None:
    from zephyr.actions.router import ActionRouter
    srv = simulator.serve(port=0, find_tv=False)
    tv = srv.state
    backend = FakeBackend()
    router = ActionRouter(bus, backend).start()
    router.tv.use(host_of(srv), simulator.NAME)
    bus.publish(T.SYSTEM, SystemState(SystemMode.ARMED))

    bus.publish(T.ACTION_REQUEST, ActionRequest({"type": "mode", "value": "tv"}, "settings_test"))
    check("the mode action turns TV mode on", wait_for(lambda: router.tv.active))
    ctx = wait_for(lambda: (c := bus.latest(T.CONTEXT)) is not None and c.mode == "tv" and c)
    check("the overlay chip shows the TV in TV mode", bool(ctx) and ctx.app_label == simulator.NAME and ctx.profile == "tv", str(ctx))

    backend.calls.clear()
    bus.publish(T.POINTER, PointerEvent(True, 0.5, 0.5))
    time.sleep(0.06)
    bus.publish(T.POINTER, PointerEvent(True, 0.63, 0.5))
    time.sleep(0.06)
    bus.publish(T.GESTURE, gesture(GestureKind.PINCH_TAP, pos=(0.5, 0.5)))
    wait_for(lambda: tv.screen == "app")
    check("TV mode: point, move and pinch open an app on the TV", tv.screen == "app" and tv.app == simulator.APPS[1])
    bus.publish(T.LEVEL, LevelEvent("volume", 3.0, True))
    check("TV mode: the knob sets the TV volume", wait_for(lambda: tv.volume == 21), str(tv.volume))
    bus.publish(T.ACTION_REQUEST, ActionRequest({"type": "tv", "value": "Home"}, "settings_test"))
    check("the tv action type runs through the runner", wait_for(lambda: tv.screen == "home"))
    mac = [name for name, _ in backend.calls if name in ("move_cursor", "click", "set_volume", "hotkey", "media", "paste_text")]
    check("TV mode: nothing happened on the Mac", not mac, str(mac))

    for value in ("Left", "Down", "OK"):
        router.tv.run(value)
    settle(router.tv.client)
    bus.publish(T.GESTURE, gesture(GestureKind.PALM_HOLD))
    check("TV mode: a held palm readies air writing", wait_for(lambda: router.writing.phase == "ready" and router.tv.searching))
    ctx = wait_for(lambda: (c := bus.latest(T.CONTEXT)) is not None and c.ink_enabled and c)
    check("TV mode: the pen draws while writing for the TV", bool(ctx) and ctx.mode == "search")
    router._enqueue_paste("lofi", "search")          # what the writing flow sends once the words are accepted
    check("TV mode: accepted words are typed on the TV", wait_for(lambda: tv.query == "lofi"), tv.query)

    bus.publish(T.ACTION_REQUEST, ActionRequest({"type": "mode", "value": "normal"}, "settings_test"))
    check("the mode action turns TV mode off", wait_for(lambda: not router.tv.active))
    backend.calls.clear()
    bus.publish(T.GESTURE, gesture(GestureKind.PINCH_TAP, pos=(0.5, 0.5)))
    bus.publish(T.POINTER, PointerEvent(True, 0.3, 0.3))
    names = lambda: {name for name, _ in backend.calls}
    check("normal mode: the Mac gets the click and the pointer again",
          wait_for(lambda: {"click", "move_cursor"} <= names()), str(sorted(names())))


def main() -> int:
    sync.disable()
    tmp = Path(tempfile.mkdtemp(prefix="zephyr_tv_check_"))
    store.DATA_DIR = tmp
    store.BINDINGS_PATH = tmp / "bindings.json"
    store.GESTURES_DIR = tmp / "gestures"
    for part in (client_tests, find_tests, remote_tests, router_tests):
        print(f"--- {part.__name__.replace('_', ' ')}")
        part()
    print("all passed" if not FAILS else f"{len(FAILS)} failed: {', '.join(FAILS)}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
