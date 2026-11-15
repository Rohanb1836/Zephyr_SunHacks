"""zephyr/config.py    Loads config.yaml into a simple object. Owner: Aadharsh.

    from zephyr.config import cfg
    cfg.swipe.max_ms          # attribute access, nested
    cfg.get("memory", {})     # safe access to a section that may not exist yet
    reload()                  # re-read the file now (any thread)
    maybe_reload()            # cheap: re-reads only if the file changed (core calls this once a second)

Always read values through cfg at the moment you need them (cfg.swipe.max_ms), not into a
variable at import time, so a reload takes effect without a restart.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(os.environ.get("ZEPHYR_CONFIG", ROOT / "config.yaml"))


class Section:
    """Read-only attribute view over a dict. Nested dicts become Sections."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._set(data)

    def _set(self, data: dict[str, Any]) -> None:
        self.__dict__.clear()
        for k, v in (data or {}).items():
            self.__dict__[k] = Section(v) if isinstance(v, dict) else v

    def get(self, key: str, default: Any = None) -> Any:
        return self.__dict__.get(key, default)

    def __getattr__(self, name: str) -> Any:   # only called when the key is missing
        raise AttributeError(f"config key '{name}' not found in {CONFIG_PATH.name}")

    def __contains__(self, key: str) -> bool:
        return key in self.__dict__

    def __repr__(self) -> str:
        return f"Section({self.__dict__!r})"


_lock = threading.Lock()
_mtime = 0.0


def _read() -> dict[str, Any]:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


cfg = Section({})


def reload() -> bool:
    """Re-read config.yaml into the shared cfg object. Returns False (and keeps the old values)
    if the file is broken, so a typo while tuning never crashes the app."""
    global _mtime
    with _lock:
        try:
            data = _read()
            _mtime = CONFIG_PATH.stat().st_mtime
        except Exception as exc:
            print(f"[config] reload failed, keeping old values: {exc!r}")
            return False
        cfg._set(data)
    return True


def maybe_reload() -> bool:
    """Reload only if the file changed on disk. Returns True when it reloaded."""
    try:
        m = CONFIG_PATH.stat().st_mtime
    except OSError:
        return False
    if m != _mtime:
        ok = reload()
        if ok:
            print("[config] reloaded config.yaml")
        return ok
    return False


reload()
