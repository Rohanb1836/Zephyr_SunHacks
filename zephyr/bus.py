"""zephyr/bus.py     Tiny thread-safe publish/subscribe bus. Owner: Aadharsh."""
import threading
from collections import defaultdict
from typing import Any, Callable


class Bus:
    def __init__(self) -> None:
        self._subs: dict[str, list[Callable[[Any], None]]] = defaultdict(list)
        self._latest: dict[str, Any] = {}
        self._lock = threading.Lock()

    def subscribe(self, topic: str, fn: Callable[[Any], None]) -> None:
        with self._lock:
            self._subs[topic].append(fn)

    def publish(self, topic: str, msg: Any) -> None:
        with self._lock:
            self._latest[topic] = msg
            subs = list(self._subs[topic])
        for fn in subs:           # runs in the PUBLISHER's thread
            try:
                fn(msg)
            except Exception as exc:    # one bad handler must never kill the vision loop
                print(f"[bus] handler error on '{topic}': {exc!r}")

    def latest(self, topic: str, default: Any = None) -> Any:
        with self._lock:
            return self._latest.get(topic, default)


bus = Bus()    # the one shared instance: from zephyr.bus import bus
