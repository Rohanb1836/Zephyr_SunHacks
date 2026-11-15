"""zephyr/custom/store.py  Reads and writes data/bindings.json and data/gestures/*.npy. Owner: Swayam.

File format is master section 6.7, plus one optional top-level key, "updated_ms":
when Zephyr last saved or deleted a gesture (epoch ms). Cloud sync uses it to
decide which copy is newer; readers that only look at "bindings" can ignore it.
Every write is atomic (temp file, then rename) so Rohan's reader never sees a
half-written file. After every save or delete, sync.push() uploads in the
background (it does nothing when cloud sync is off).
"""
from __future__ import annotations

import io
import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Optional

import numpy as np

from zephyr.custom.conf import REPO_ROOT

# the double-click app sets ZEPHYR_DATA to ~/Library/Application Support/Zephyr/data,
# because an app cannot write inside itself; from the repo it is data/ as before
DATA_DIR = Path(os.environ["ZEPHYR_DATA"]) if os.environ.get("ZEPHYR_DATA") else REPO_ROOT / "data"
BINDINGS_PATH = DATA_DIR / "bindings.json"
GESTURES_DIR = DATA_DIR / "gestures"

# held while files are written or read as a set (saves, deletes, cloud pull and push)
write_lock = threading.Lock()


def _empty() -> dict[str, Any]:
    return {"version": 1, "bindings": []}


def atomic_write_bytes(path: Path, data: bytes) -> None:
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


def parse_bindings(raw: bytes) -> dict[str, Any]:
    """bindings.json bytes -> dict. Raises ValueError if the shape is wrong."""
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("bindings"), list):
        raise ValueError("bad shape")
    return data


def load_bindings() -> dict[str, Any]:
    """The whole bindings file. Returns an empty file if it is missing or broken."""
    try:
        return parse_bindings(BINDINGS_PATH.read_bytes())
    except FileNotFoundError:
        return _empty()
    except Exception as exc:
        print(f"[custom.store] could not read bindings.json, treating as empty: {exc!r}")
        return _empty()


def save_bindings(data: dict[str, Any]) -> None:
    payload = json.dumps(data, indent=2).encode("utf-8") + b"\n"
    atomic_write_bytes(BINDINGS_PATH, payload)


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


_by_id_lock = threading.Lock()
_by_id_key: Optional[tuple[int, int]] = None
_by_id: dict[str, dict[str, Any]] = {}


def _bindings_by_id() -> dict[str, dict[str, Any]]:
    """Bindings keyed by id, re-read only when bindings.json changes on disk."""
    global _by_id_key, _by_id
    try:
        st = BINDINGS_PATH.stat()
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        key = None
    with _by_id_lock:
        if key != _by_id_key:
            _by_id = {b.get("id"): b for b in load_bindings()["bindings"] if b.get("id")}
            _by_id_key = key
        return _by_id


def custom_action(key: str, profile: str = "default") -> Optional[dict[str, Any]]:
    """Action spec for a "custom.<id>" gesture key in the given app profile, or None.

    For the action router (master doc 6.6): a custom binding counts when its scope
    holds this profile id or "*". Cheap enough to call for every gesture.
        spec = custom_action("custom.rock_on", "spotify")
    """
    if not key.startswith("custom."):
        return None
    binding = _bindings_by_id().get(key[len("custom."):])
    if binding is None:
        return None
    scope = binding.get("scope") or ["*"]
    if "*" not in scope and profile not in scope:
        return None
    action = binding.get("action")
    return dict(action) if isinstance(action, dict) else None


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


def _stamp(data: dict[str, Any]) -> None:
    data["updated_ms"] = int(time.time() * 1000)


def _changed() -> None:
    """Upload in the background after a local change. Never lets sync break a save."""
    try:
        from zephyr.custom import sync
        sync.push()
    except Exception as exc:
        print(f"[custom.store] could not start cloud sync: {exc!r}")


def add_binding(name: str, template: np.ndarray, threshold: float, action: dict[str, Any],
                scope: Optional[list[str]] = None, hand: str = "any") -> dict[str, Any]:
    """Saves the template and appends the binding. Returns the new binding."""
    with write_lock:
        data = load_bindings()
        gid = make_id(name, {b.get("id", "") for b in data["bindings"]})
        rel = f"gestures/{gid}.npy"

        # np.save to a buffer first so the .npy write is atomic too
        buf = io.BytesIO()
        np.save(buf, np.asarray(template, dtype=np.float32).reshape(63))
        atomic_write_bytes(DATA_DIR / rel, buf.getvalue())

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
        _stamp(data)
        save_bindings(data)
    _changed()
    return binding


def delete_binding(gid: str) -> bool:
    with write_lock:
        data = load_bindings()
        keep = [b for b in data["bindings"] if b.get("id") != gid]
        if len(keep) == len(data["bindings"]):
            return False
        removed = [b for b in data["bindings"] if b.get("id") == gid]
        data["bindings"] = keep
        _stamp(data)
        save_bindings(data)
        for b in removed:
            rel = b.get("template")
            if rel:
                try:
                    (DATA_DIR / rel).unlink()
                except OSError:
                    pass
    _changed()
    return True
