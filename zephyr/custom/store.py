"""zephyr/custom/store.py  Reads and writes data/bindings.json and data/gestures/*.npy. Owner: Swayam.

File format is master section 6.7. Every write is atomic (temp file, then rename)
so Rohan's reader never sees a half-written file.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any, Optional

import numpy as np

from zephyr.custom.conf import REPO_ROOT

DATA_DIR = REPO_ROOT / "data"
BINDINGS_PATH = DATA_DIR / "bindings.json"
GESTURES_DIR = DATA_DIR / "gestures"

_write_lock = threading.Lock()


def _empty() -> dict[str, Any]:
    return {"version": 1, "bindings": []}


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_bindings() -> dict[str, Any]:
    """The whole bindings file. Returns an empty file if it is missing or broken."""
    try:
        with open(BINDINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get("bindings"), list):
            raise ValueError("bad shape")
        return data
    except FileNotFoundError:
        return _empty()
    except Exception as exc:
        print(f"[custom.store] could not read bindings.json, treating as empty: {exc!r}")
        return _empty()


def save_bindings(data: dict[str, Any]) -> None:
    payload = json.dumps(data, indent=2).encode("utf-8") + b"\n"
    _atomic_write_bytes(BINDINGS_PATH, payload)


def load_template(binding: dict[str, Any]) -> Optional[np.ndarray]:
    rel = binding.get("template")
    if not rel:
        return None
    try:
        arr = np.load(DATA_DIR / rel).astype(np.float32).reshape(-1)
        return arr if arr.shape == (63,) else None
    except Exception as exc:
        print(f"[custom.store] could not load template {rel}: {exc!r}")
        return None


def make_id(name: str, existing: set[str]) -> str:
    """Lower case with underscores, made from the name, unique among existing ids."""
    base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "gesture"
    if not base[0].isalpha():
        base = f"g_{base}"
    gid, n = base, 2
    while gid in existing:
        gid = f"{base}_{n}"
        n += 1
    return gid


def next_default_name() -> str:
    """"Gesture 3" style name that is not taken yet."""
    names = {b.get("name", "") for b in load_bindings()["bindings"]}
    n = 1
    while f"Gesture {n}" in names:
        n += 1
    return f"Gesture {n}"


def add_binding(name: str, template: np.ndarray, threshold: float, action: dict[str, Any],
                scope: Optional[list[str]] = None, hand: str = "any") -> dict[str, Any]:
    """Saves the template and appends the binding. Returns the new binding."""
    with _write_lock:
        data = load_bindings()
        gid = make_id(name, {b.get("id", "") for b in data["bindings"]})
        rel = f"gestures/{gid}.npy"

        # np.save to a buffer first so the .npy write is atomic too
        import io
        buf = io.BytesIO()
        np.save(buf, np.asarray(template, dtype=np.float32).reshape(63))
        _atomic_write_bytes(DATA_DIR / rel, buf.getvalue())

        binding = {
            "id": gid,
            "name": name,
            "template": rel,
            "threshold": round(float(threshold), 4),
            "hand": hand,
            "scope": scope or ["*"],
            "action": action,
        }
        data["bindings"].append(binding)
        save_bindings(data)
        return binding


def delete_binding(gid: str) -> bool:
    with _write_lock:
        data = load_bindings()
        keep = [b for b in data["bindings"] if b.get("id") != gid]
        if len(keep) == len(data["bindings"]):
            return False
        removed = [b for b in data["bindings"] if b.get("id") == gid]
        data["bindings"] = keep
        save_bindings(data)
        for b in removed:
            rel = b.get("template")
            if rel:
                try:
                    (DATA_DIR / rel).unlink()
                except OSError:
                    pass
        return True
