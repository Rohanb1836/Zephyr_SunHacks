"""zephyr/custom/conf.py  Swayam's config values (custom and ui sections). Owner: Swayam.

Reads through zephyr.config.cfg at the moment a value is needed, so a config
reload takes effect without a restart. Any key missing from config.yaml falls
back to the defaults below, so these optional keys work without editing it:
    custom.max_threshold   cap on a recorded gesture's match threshold
    custom.countdown_ms    recorder countdown before capture
    ui.open_settings       open the settings window when the app starts
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULTS: dict[str, dict[str, Any]] = {
    "custom": {"samples": 30, "default_threshold": 0.16, "margin": 0.03,
               "max_threshold": 0.30, "countdown_ms": 3000},
    "ui": {"hud_corner": "bottom_right", "hud_size": [300, 225], "show_video": False,
           "open_settings": False},
}

_warned = False


def _live(name: str):
    global _warned
    try:
        from zephyr.config import cfg
        return cfg.get(name)
    except Exception as exc:  # config missing or broken: run on defaults, say so once
        if not _warned:
            _warned = True
            print(f"[custom.conf] using built-in defaults: {exc!r}")
        return None


def value(name: str, key: str) -> Any:
    """One value, read live. Cheap enough for the vision thread."""
    sec = _live(name)
    if sec is not None:
        v = sec.get(key)
        if v is not None:
            return v
    return DEFAULTS.get(name, {}).get(key)


def section(name: str) -> dict[str, Any]:
    """A whole section as a plain dict, defaults filled in."""
    merged = dict(DEFAULTS.get(name, {}))
    sec = _live(name)
    if sec is not None:
        merged.update({k: v for k, v in vars(sec).items() if v is not None})
    return merged
