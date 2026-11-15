"""zephyr/custom/matcher.py  Matcher implementing MatcherProtocol. Owner: Swayam.

Matcher() takes no arguments and loads data/bindings.json. match() runs on the
vision thread for every UNKNOWN hand, so it stays vectorized and well under 1 ms.
It answers for one frame only; Aadharsh's engine handles the 500 ms hold and
calls reload() on the vision thread when T.CUSTOM_RELOAD is published.
"""
from __future__ import annotations

import threading
from typing import Optional

import numpy as np

from zephyr.contracts import HandObs, Side
from zephyr.custom import store
from zephyr.custom.conf import value
from zephyr.custom.templates import distances_to_many, normalize


class _Set:
    """One immutable snapshot of the loaded templates."""

    def __init__(self, ids: list[str], names: list[str], templates: np.ndarray,
                 thresholds: np.ndarray, left_ok: np.ndarray, right_ok: np.ndarray) -> None:
        self.ids = ids
        self.names = names
        self.templates = templates                    # (N, 63)
        self.thresholds = thresholds                  # (N,)
        self.left_ok = left_ok                        # (N,) bool
        self.right_ok = right_ok                      # (N,) bool
        n = len(ids)
        # scratch buffers so the common case does not allocate big arrays
        self.diff = np.empty((n, 63), dtype=np.float32)
        self.sq = np.empty((n, 21), dtype=np.float32)
        self.fingers = np.empty((n, 5), dtype=np.float32)
        self.dist = np.empty(n, dtype=np.float32)

    @property
    def size(self) -> int:
        return len(self.ids)


class Matcher:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._set = _Set([], [], np.empty((0, 63), np.float32), np.empty(0, np.float32),
                         np.empty(0, bool), np.empty(0, bool))
        self.reload()
        # the first Matcher of a run (run.py makes it at startup) starts the one-time cloud
        # pull on the sync thread; a newer cloud copy arrives as T.CUSTOM_RELOAD
        try:
            from zephyr.custom import sync
            sync.pull_at_startup()
        except Exception as exc:
            print(f"[custom] cloud sync not started: {exc!r}")

    # ---- MatcherProtocol ----

    def match(self, hand: HandObs) -> Optional[tuple[str, float]]:
        with self._lock:
            s = self._set
        if s.size == 0:
            return None
        vec = normalize(hand.world, hand.side is Side.LEFT)
        d = self._distances(s, vec)
        allowed = s.left_ok if hand.side is Side.LEFT else s.right_ok
        d[~allowed] = np.inf

        best = int(np.argmin(d))
        best_d = float(d[best])
        if not np.isfinite(best_d) or best_d >= s.thresholds[best]:
            return None
        if s.size > 1:
            d[best] = np.inf
            second = float(d.min())
            if second - best_d < float(value("custom", "margin")):
                return None  # too close between two gestures, so say nothing
        return s.ids[best], best_d

    def reload(self) -> None:
        data = store.load_bindings()
        ids, names, temps, ths, lok, rok = [], [], [], [], [], []
        for b in data["bindings"]:
            tmpl = store.load_template(b)
            if tmpl is None or not b.get("id"):
                continue
            hand = str(b.get("hand", "any")).lower()
            ids.append(b["id"])
            names.append(b.get("name", b["id"]))
            temps.append(tmpl)
            ths.append(float(b.get("threshold", value("custom", "default_threshold"))))
            lok.append(hand in ("any", "left"))
            rok.append(hand in ("any", "right"))
        new = _Set(ids, names,
                   np.stack(temps).astype(np.float32) if temps else np.empty((0, 63), np.float32),
                   np.asarray(ths, np.float32), np.asarray(lok, bool), np.asarray(rok, bool))
        with self._lock:
            self._set = new
        print(f"[custom] loaded {new.size} custom gesture(s)")

    # ---- used by the recorder ----

    def nearest(self, vec: np.ndarray, exclude: Optional[str] = None) -> Optional[tuple[str, str, float]]:
        """Closest existing template to a new one: (id, name, distance)."""
        with self._lock:
            s = self._set
        if s.size == 0:
            return None
        # own arrays here: the scratch buffers belong to the vision thread
        d = distances_to_many(np.asarray(vec, np.float32).reshape(63), s.templates)
        if exclude is not None and exclude in s.ids:
            d[s.ids.index(exclude)] = np.inf
        i = int(np.argmin(d))
        if not np.isfinite(d[i]):
            return None
        return s.ids[i], s.names[i], float(d[i])

    @staticmethod
    def _distances(s: _Set, vec: np.ndarray) -> np.ndarray:
        """Same worst-finger distance as templates.distances_to_many, into scratch buffers."""
        np.subtract(s.templates, vec, out=s.diff)
        np.multiply(s.diff, s.diff, out=s.diff)
        np.sum(s.diff.reshape(s.size, 21, 3), axis=2, out=s.sq)
        np.sqrt(s.sq, out=s.sq)
        np.mean(s.sq[:, 1:].reshape(s.size, 5, 4), axis=2, out=s.fingers)
        np.max(s.fingers, axis=1, out=s.dist)
        return s.dist
