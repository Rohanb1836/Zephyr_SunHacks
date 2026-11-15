"""zephyr/custom/router_check.py  Custom gestures through Rohan's action router. Owner: Swayam.

Run:  python -m zephyr.custom.router_check

Uses the real actions.router.ActionRouter with a fake OS backend that only records
calls. Checks that a custom.<id> gesture runs its bound action and shows a toast,
that app scope and the guest rule hold, and what the Settings Test button does.

If the router does not resolve custom gestures yet, this runs it with the lookup
Rohan needs in ActionRouter._resolve (master doc 6.6), and says so:

    def _resolve(self, key):
        if key.startswith('custom.'):
            from zephyr.custom.store import custom_action
            spec = custom_action(key, self.profile.get('id', 'default'))
            if spec:
                return spec
        return self.profile.get('map', {}).get(key) or self.profiles['default'].get('map', {}).get(key)
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np

from zephyr.bus import bus
from zephyr.contracts import (ActionRequest, GestureEvent, GestureKind, Role, Side, SystemMode,
                              SystemState, T)
from zephyr.custom import store, sync
from zephyr.custom.store import custom_action


class FakeBackend:
    """Records what would have happened on the Mac."""
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def __getattr__(self, name):
        def record(*args):
            self.calls.append((name, args[0] if len(args) == 1 else args))
        return record


def main() -> int:
    sync.disable()  # local only
    tmp = Path(tempfile.mkdtemp(prefix="zephyr_router_check_"))
    store.DATA_DIR = tmp
    store.BINDINGS_PATH = tmp / "bindings.json"
    store.GESTURES_DIR = tmp / "gestures"
    tmpl = np.zeros(63, np.float32)
    store.add_binding("Rock on", tmpl, 0.2, {"type": "url", "value": "https://sunhacks-2026.devpost.com",
                                             "label": "Open Devpost"})
    store.add_binding("Call me", tmpl, 0.2, {"type": "app", "value": "Spotify", "label": "Open Spotify"},
                      scope=["spotify"])

    from zephyr.actions.router import ActionRouter

    probe = ActionRouter(bus, FakeBackend())
    native = bool(probe._resolve("custom.rock_on"))
    router_cls = ActionRouter
    if not native:
        print("NOTE router does not resolve custom gestures yet; using the _resolve in this file's docstring")

        class WithCustom(ActionRouter):
            def _resolve(self, key):
                if key.startswith("custom."):
                    spec = custom_action(key, self.profile.get("id", "default"))
                    if spec:
                        return spec
                return super()._resolve(key)
        router_cls = WithCustom

    backend = FakeBackend()
    router = router_cls(bus, backend)
    router._update_app = lambda: None   # no front-app detection here; tests set the profile
    router.start()
    toasts: list = []
    bus.subscribe(T.ACTION, toasts.append)

    fails: list[str] = []

    def report(name: str, ok: bool, detail="") -> None:
        print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  {detail}"))
        if not ok:
            fails.append(name)

    def custom(name: str, role: Role = Role.OWNER) -> None:
        bus.publish(T.GESTURE, GestureEvent(GestureKind.CUSTOM, 0, 1, Side.RIGHT, role, name=name))
        router.queue.join()

    bus.publish(T.SYSTEM, SystemState(SystemMode.ARMED))

    router.profile = router.profiles["chrome"]
    custom("rock_on")
    report("custom.rock_on opens its website", backend.calls == [("open_url", "https://sunhacks-2026.devpost.com")],
           backend.calls)
    report("a toast reads 'Open Devpost'", bool(toasts) and toasts[-1].label == "Open Devpost" and toasts[-1].ok,
           toasts[-1:])

    backend.calls.clear()
    custom("call_me")
    report("a gesture scoped to Spotify does nothing in Chrome", backend.calls == [], backend.calls)
    router.profile = router.profiles["spotify"]
    custom("call_me")
    report("the same gesture works in Spotify", backend.calls == [("open_app", "Spotify")], backend.calls)

    backend.calls.clear()
    custom("rock_on", role=Role.GUEST)
    custom("rock_on", role=Role.STRANGER)
    report("guests and strangers cannot fire custom gestures", backend.calls == [], backend.calls)

    backend.calls.clear()
    spec = store.load_bindings()["bindings"][0]["action"]
    bus.publish(T.ACTION_REQUEST, ActionRequest(spec=spec, source="settings_test"))
    router.queue.join()
    report("Settings Test runs the action while armed", backend.calls == [("open_url", spec["value"])],
           backend.calls)

    backend.calls.clear()
    bus.publish(T.SYSTEM, SystemState(SystemMode.DISARMED))
    bus.publish(T.ACTION_REQUEST, ActionRequest(spec=spec, source="settings_test"))
    router.queue.join()
    print("NOTE Settings Test while disarmed:", "runs" if backend.calls else "is dropped by the router")

    print(("all passed" if not fails else f"{len(fails)} failed")
          + ("" if native else "  (with the suggested _resolve)"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
