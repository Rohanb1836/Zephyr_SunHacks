"""zephyr/stubs.py     Stand-ins used until the real modules are merged. Owner: Aadharsh."""
from zephyr.bus import bus
from zephyr.contracts import Role, T


class NullGate:
    """Accepts every hand as the owner. Replace with presence.gate.Gate."""
    def role_of(self, hand): return Role.OWNER
    def can_act(self): return True, "ok"
    def on_arm(self, hands): return True
    def on_disarm(self): pass
    def guest_active(self): return False
    def revoke_guest(self): pass


class NullMatcher:
    """Matches nothing. Replace with custom.matcher.Matcher."""
    def match(self, hand): return None
    def reload(self): pass


def log_actions() -> None:
    """Print every gesture so Windows developers can test without an OS backend."""
    bus.subscribe(T.GESTURE, lambda e: print("[gesture]", e.key, e.role.value))
    bus.subscribe(T.LEVEL, lambda e: print("[level]", e.kind, round(e.delta, 2)))
    bus.subscribe(T.SYSTEM, lambda e: print("[system]", e.mode.value, e.reason))
