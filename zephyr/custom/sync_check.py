"""zephyr/custom/sync_check.py  Cloud sync between two computers, simulated. Owner: Swayam.

Run:  python -m zephyr.custom.sync_check

No AWS account needed: an in-memory S3 stands in for the bucket, and two temporary
data folders stand in for two Macs. Checks that a gesture saved on Mac A reaches the
cloud, that Mac B (a fresh checkout) pulls it at startup and matches it, that a delete
on B reaches A, that a fresh checkout never overwrites the cloud, that offline and
missing credentials fail soft and log once, that an unsafe cloud path is refused, and
that every S3 call runs on the zephyr-sync thread.
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np

from zephyr.bus import bus
from zephyr.contracts import HandsFrame, T
from zephyr.custom import store, sync
from zephyr.custom.selftest import fake_hand, fake_world

PREFIX = "users/owner/"


class FakeClientError(Exception):
    """Shaped like botocore's ClientError: the code lives in .response."""
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class EndpointConnectionError(Exception):
    pass


class NoCredentialsError(Exception):
    pass


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, dict]] = {}
        self.threads: set[str] = set()

    def _seen(self) -> None:
        self.threads.add(threading.current_thread().name)

    def head_object(self, Bucket, Key):
        self._seen()
        if Key not in self.objects:
            raise FakeClientError("404")
        return {"Metadata": dict(self.objects[Key][1])}

    def get_object(self, Bucket, Key):
        self._seen()
        if Key not in self.objects:
            raise FakeClientError("NoSuchKey")
        return {"Body": io.BytesIO(self.objects[Key][0])}

    def put_object(self, Bucket, Key, Body, Metadata=None, ContentType=None):
        self._seen()
        self.objects[Key] = (bytes(Body), dict(Metadata or {}))

    def list_objects_v2(self, Bucket, Prefix):
        self._seen()
        return {"Contents": [{"Key": k} for k in sorted(self.objects) if k.startswith(Prefix)]}

    def delete_objects(self, Bucket, Delete):
        self._seen()
        for o in Delete["Objects"]:
            self.objects.pop(o["Key"], None)


class Broken:
    """A client whose every call fails the same way (no network, no credentials)."""
    def __init__(self, exc_type) -> None:
        self.exc_type = exc_type

    def __getattr__(self, name):
        def fail(*_a, **_k):
            raise self.exc_type("simulated")
        return fail


def use_machine(folder: Path) -> None:
    """Point the store at one computer's data folder."""
    store.DATA_DIR = folder
    store.BINDINGS_PATH = folder / "bindings.json"
    store.GESTURES_DIR = folder / "gestures"


def fresh_checkout(folder: Path) -> None:
    """What git gives a new clone: an empty bindings file Zephyr never saved."""
    (folder / "gestures").mkdir(parents=True, exist_ok=True)
    (folder / "bindings.json").write_text('{\n  "version": 1,\n  "bindings": []\n}\n')


def new_process() -> None:
    """Forget per-run state, as if Zephyr was started again."""
    sync._client = None
    sync._pulled_at_start = False


def record(shape: str, rng):
    from zephyr.custom.capture import CaptureSession
    s = CaptureSession(samples=30, default_threshold=0.16, countdown_ms=0)
    s.start(0)
    for i in range(45):
        s.tick(i * 33, HandsFrame(i, i * 33, (fake_hand(fake_world(shape, rng)),)))
        if s.state == "done":
            break
    assert s.result and s.result.ok, s.result
    return s.result.built


def main() -> int:
    rng = np.random.default_rng(5)
    root = Path(tempfile.mkdtemp(prefix="zephyr_sync_check_"))
    mac_a, mac_b = root / "mac_a", root / "mac_b"
    fresh_checkout(mac_a)
    fresh_checkout(mac_b)

    s3 = FakeS3()
    sync.client_factory = lambda: s3
    sync.config_override = ("zephyr-test-bucket", "owner")

    reload_threads: list[str] = []
    bus.subscribe(T.CUSTOM_RELOAD, lambda _m: reload_threads.append(threading.current_thread().name))

    fails: list[str] = []

    def report(name: str, ok: bool, detail="") -> None:
        print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  {detail}"))
        if not ok:
            fails.append(name)

    def ids() -> list[str]:
        return [b["id"] for b in store.load_bindings()["bindings"]]

    # ---- Mac A: record a gesture and save it
    use_machine(mac_a)
    built = record("rock_on", rng)
    t0 = time.perf_counter()
    store.add_binding("Rock on", built.template, built.threshold,
                      {"type": "url", "value": "https://sunhacks-2026.devpost.com", "label": "Open Devpost"})
    save_ms = (time.perf_counter() - t0) * 1000
    sync.wait_idle()
    report("save on A returns at once (sync runs in the background)", save_ms < 200, f"{save_ms:.0f} ms")
    report("A's save uploaded bindings.json and the template",
           PREFIX + "bindings.json" in s3.objects and PREFIX + "gestures/rock_on.npy" in s3.objects,
           sorted(s3.objects))
    report("status reads Synced", sync.status()[0] == "synced", sync.status())

    # ---- Mac B: a fresh checkout starts Zephyr; the first Matcher pulls at startup
    use_machine(mac_b)
    new_process()
    from zephyr.custom.matcher import Matcher
    m = Matcher()
    sync.wait_idle()
    report("B pulled the gesture at startup", ids() == ["rock_on"] and (mac_b / "gestures/rock_on.npy").exists(),
           ids())
    report("the pull published CUSTOM_RELOAD from the sync thread",
           bool(reload_threads) and reload_threads[-1] == "zephyr-sync", reload_threads)
    report("the fresh checkout did not overwrite the cloud",
           [b["id"] for b in store.parse_bindings(s3.objects[PREFIX + "bindings.json"][0])["bindings"]]
           == ["rock_on"])
    m.reload()  # what the engine does on CUSTOM_RELOAD
    hits = [m.match(fake_hand(fake_world("rock_on", rng))) for _ in range(10)]
    report("B matches the gesture recorded on A (10 of 10)",
           all(h is not None and h[0] == "rock_on" for h in hits), hits)

    # ---- newest wins: delete on B, then A syncs
    store.delete_binding("rock_on")
    sync.wait_idle()
    report("B's delete removed the gesture from the cloud",
           PREFIX + "gestures/rock_on.npy" not in s3.objects and
           store.parse_bindings(s3.objects[PREFIX + "bindings.json"][0])["bindings"] == [])
    use_machine(mac_a)
    new_process()
    sync.sync_now()
    sync.wait_idle()
    report("A pulled the delete and dropped the old template file",
           ids() == [] and not (mac_a / "gestures/rock_on.npy").exists(), ids())

    # ---- an unsafe path in the cloud copy is refused and nothing local changes
    bad = {"version": 1, "updated_ms": int(time.time() * 1000) + 10**9,
           "bindings": [{"id": "x", "name": "x", "template": "../../outside.npy", "threshold": 0.2,
                         "hand": "any", "scope": ["*"], "action": {"type": "url", "value": "x"}}]}
    good = s3.objects[PREFIX + "bindings.json"]
    s3.objects[PREFIX + "bindings.json"] = (json.dumps(bad).encode(), {"zephyr-updated": str(bad["updated_ms"])})
    before = store.BINDINGS_PATH.read_bytes()
    sync.sync_now()
    sync.wait_idle()
    report("an unsafe cloud path is refused and local files are untouched",
           store.BINDINGS_PATH.read_bytes() == before and not (root / "outside.npy").exists()
           and sync.status()[0] == "offline", sync.status())
    s3.objects[PREFIX + "bindings.json"] = good  # put the real cloud copy back

    # ---- offline: saving keeps working locally, and the problem is logged once
    sync.client_factory = lambda: Broken(EndpointConnectionError)
    new_process()
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        for shape, name in (("call_me", "Call me"), ("three", "Three")):
            b = record(shape, rng)
            store.add_binding(name, b.template, b.threshold, {"type": "app", "value": "Spotify"})
            sync.wait_idle()
    logged = out.getvalue().count("[sync] offline")
    report("offline: both gestures saved locally", ids() == ["call_me", "three"], ids())
    report("offline: logged once, not on every save", logged == 1, out.getvalue())
    report("offline: status reads Offline", sync.status() == ("offline", "Offline: no connection"), sync.status())

    # ---- missing credentials fail soft too
    sync.client_factory = lambda: Broken(NoCredentialsError)
    new_process()
    sync.sync_now()
    sync.wait_idle()
    report("missing credentials: status reads Offline, nothing breaks",
           sync.status() == ("offline", "Offline: no AWS credentials"), sync.status())

    # ---- back online: the offline saves go up, since they are now the newest copy
    sync.client_factory = lambda: s3
    new_process()
    sync.sync_now()
    sync.wait_idle()
    report("back online: the offline saves are pushed",
           [b["id"] for b in store.parse_bindings(s3.objects[PREFIX + "bindings.json"][0])["bindings"]]
           == ["call_me", "three"])

    report("every S3 call ran on the zephyr-sync thread", s3.threads == {"zephyr-sync"}, s3.threads)
    print("all passed" if not fails else f"{len(fails)} failed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
