"""zephyr/custom/sync.py  Optional cloud sync for custom gestures (AWS S3). Owner: Swayam.

Lets a user's gestures follow them to any computer. Keeps data/bindings.json and
data/gestures/*.npy in  s3://<aws.bucket>/users/<aws.user>/

    pull()      startup: if the cloud copy is newer, replace the local files (same atomic
                write as store.py) and publish T.CUSTOM_RELOAD
    push()      after every save or delete (store.py calls it): upload the local files
    sync_now()  the Settings "Sync now" button
    status()    ("synced" | "offline" | "syncing" | "off", short text) for the Settings label

All of them only queue work and return at once. One "zephyr-sync" thread makes the
network calls, never the Qt main thread or the vision thread.

Newest copy wins, whichever call asked: a push that finds the cloud newer pulls
instead, and a pull that finds the local copy newer pushes it. "Newer" is the
updated_ms that store.py writes into bindings.json on every save or delete. A local
file Zephyr never saved (a fresh git checkout) counts as oldest, so starting Zephyr
on a new computer can never wipe the cloud copy.

Offline, no credentials, no bucket or no boto3: logged once, and Zephyr keeps working
locally. The next save or Sync now tries again.

Config, in Aadharsh's aws section:  aws: {region: us-west-2, bucket: <bucket>, user: owner}
Optional aws.profile picks a named AWS profile. Credentials come from the environment
(run.py loads .env) or ~/.aws, never from this repo.
"""
from __future__ import annotations

import io
import re
import threading
import time
from typing import Any, Callable, Optional

import numpy as np

from zephyr.bus import bus
from zephyr.contracts import T
from zephyr.custom import store
from zephyr.custom.conf import value

META_KEY = "zephyr-updated"                     # S3 metadata on bindings.json: its updated_ms
SAFE_REL = re.compile(r"^gestures/[a-z0-9_]+\.npy$")

_lock = threading.Lock()
_wake = threading.Condition(_lock)
_queued = False          # one pending sync covers any number of requests
_running = False
_worker: Optional[threading.Thread] = None
_status: tuple[str, str] = ("off", "Sync off")
_logged: set[str] = set()
_client: Any = None
_disabled = False
_pulled_at_start = False

# set by sync_check.py: a fake S3 client, and (bucket, user) in place of config.yaml
client_factory: Optional[Callable[[], Any]] = None
config_override: Optional[tuple[str, str]] = None


# ---------------------------------------------------------------- public API

def pull() -> None:
    """Background: bring the cloud copy down if it is newer."""
    _request()


def push() -> None:
    """Background: send the local copy up (after a save or delete)."""
    _request()


def sync_now() -> None:
    """Background: whichever copy is newer wins."""
    _request()


def pull_at_startup() -> None:
    """pull() once per run. The first Matcher() calls this; run.py makes it at startup."""
    global _pulled_at_start
    with _lock:
        if _pulled_at_start:
            return
        _pulled_at_start = True
    pull()


def status() -> tuple[str, str]:
    with _lock:
        return _status


def disable() -> None:
    """Turn sync off for this process (the selftests use it so they never touch the cloud)."""
    global _disabled
    _disabled = True
    _set_status("off", "Sync off")


def wait_idle(timeout: float = 10.0) -> bool:
    """Block until queued sync work is done. For tests and scripts, never the UI."""
    end = time.monotonic() + timeout
    with _lock:
        while _queued or _running:
            left = end - time.monotonic()
            if left <= 0:
                return False
            _wake.wait(left)
    return True


# ---------------------------------------------------------------- worker

def _request() -> None:
    global _queued, _worker
    if _disabled:
        return
    if _settings() is None:
        _set_status("off", "Sync off")
        _log_once("off", "[sync] cloud sync is off. Add bucket and user to the aws section of "
                         "config.yaml to turn it on.")
        return
    with _lock:
        _queued = True
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_loop, name="zephyr-sync", daemon=True)
            _worker.start()
        _wake.notify_all()


def _loop() -> None:
    global _queued, _running
    while True:
        with _lock:
            while not _queued:
                _wake.wait()
            _queued = False
            _running = True
        try:
            _sync_once()
        finally:
            with _lock:
                _running = False
                _wake.notify_all()


def _sync_once() -> None:
    global _client
    cfg = _settings()
    if cfg is None:
        _set_status("off", "Sync off")
        return
    bucket, prefix = cfg
    where = f"s3://{bucket}/{prefix}"
    _set_status("syncing", "Syncing…")
    try:
        c = _get_client()
        cloud = _cloud_stamp(c, bucket, prefix)     # None: nothing in the cloud yet
        local = _local_stamp()                      # 0: a file Zephyr never saved
        if cloud is not None and cloud > local:
            n = _pull(c, bucket, prefix)
            print(f"[sync] pulled {n} gesture(s) from {where}")
        elif local > 0 and (cloud is None or local > cloud):
            n = _push(c, bucket, prefix)
            print(f"[sync] pushed {n} gesture(s) to {where}")
        _set_status("synced", f"Synced {_clock()}")
        _forget_offline()
    except Exception as exc:
        _client = None  # build a fresh client next time (new credentials or network)
        reason = _reason(exc)
        _set_status("offline", f"Offline: {reason}")
        _log_once(f"offline:{reason}",
                  f"[sync] offline ({reason}). Gestures keep working on this computer. {exc!r}")


# ---------------------------------------------------------------- S3 work

def _cloud_stamp(c, bucket: str, prefix: str) -> Optional[int]:
    try:
        head = c.head_object(Bucket=bucket, Key=prefix + "bindings.json")
    except Exception as exc:
        if _code(exc) in ("404", "NoSuchKey", "NotFound"):
            return None
        raise
    meta = {str(k).lower(): v for k, v in (head.get("Metadata") or {}).items()}
    try:
        return int(meta[META_KEY])
    except (KeyError, ValueError):
        pass
    # uploaded some other way: read the stamp from the file itself
    raw = c.get_object(Bucket=bucket, Key=prefix + "bindings.json")["Body"].read()
    try:
        return int(store.parse_bindings(raw).get("updated_ms") or 0)
    except Exception:
        return 0


def _local_stamp() -> int:
    """updated_ms of the local bindings.json, 0 if Zephyr never saved it."""
    try:
        return int(store.parse_bindings(store.BINDINGS_PATH.read_bytes()).get("updated_ms") or 0)
    except Exception:
        return 0


def _pull(c, bucket: str, prefix: str) -> int:
    # download and check everything first, then write locally in one short locked step
    raw = c.get_object(Bucket=bucket, Key=prefix + "bindings.json")["Body"].read()
    data = store.parse_bindings(raw)
    files: dict[str, bytes] = {}
    for b in data["bindings"]:
        rel = str(b.get("template", ""))
        if not SAFE_REL.match(rel):
            raise ValueError(f"cloud copy has an unexpected file path: {rel!r}")
        body = c.get_object(Bucket=bucket, Key=prefix + rel)["Body"].read()
        if np.load(io.BytesIO(body), allow_pickle=False).size != 63:
            raise ValueError(f"cloud template {rel} has the wrong size")
        files[rel] = body

    with store.write_lock:
        for rel, body in files.items():
            store.atomic_write_bytes(store.DATA_DIR / rel, body)
        store.atomic_write_bytes(store.BINDINGS_PATH, raw)
        # templates of gestures deleted on the other computer
        for f in (store.DATA_DIR / "gestures").glob("*.npy"):
            if f"gestures/{f.name}" not in files:
                try:
                    f.unlink()
                except OSError:
                    pass
    bus.publish(T.CUSTOM_RELOAD, None)
    return len(data["bindings"])


def _push(c, bucket: str, prefix: str) -> int:
    with store.write_lock:
        raw = store.BINDINGS_PATH.read_bytes()
        data = store.parse_bindings(raw)
        files: dict[str, bytes] = {}
        for b in data["bindings"]:
            rel = str(b.get("template", ""))
            path = store.DATA_DIR / rel
            if SAFE_REL.match(rel) and path.exists():
                files[rel] = path.read_bytes()
    stamp = str(int(data.get("updated_ms") or 0))

    # templates first, bindings.json last, so the cloud never lists a gesture without its template
    for rel, body in files.items():
        c.put_object(Bucket=bucket, Key=prefix + rel, Body=body)
    c.put_object(Bucket=bucket, Key=prefix + "bindings.json", Body=raw,
                 ContentType="application/json", Metadata={META_KEY: stamp})
    _delete_stale(c, bucket, prefix, set(files))
    return len(data["bindings"])


def _delete_stale(c, bucket: str, prefix: str, keep: set[str]) -> None:
    """Remove cloud templates of deleted gestures. Best effort: needs list and delete rights."""
    try:
        resp = c.list_objects_v2(Bucket=bucket, Prefix=prefix + "gestures/")
        stale = [o["Key"] for o in resp.get("Contents", []) if o["Key"][len(prefix):] not in keep]
        if stale:
            c.delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": k} for k in stale]})
    except Exception as exc:
        _log_once("stale", f"[sync] could not tidy old cloud templates (harmless): {exc!r}")


# ---------------------------------------------------------------- helpers

def _settings() -> Optional[tuple[str, str]]:
    """(bucket, "users/<user>/"), or None when cloud sync is off."""
    if config_override is not None:
        bucket, user = config_override
    else:
        bucket, user = value("aws", "bucket"), value("aws", "user")
    if not bucket:
        return None
    user = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(user or "owner")).strip("._") or "owner"
    return str(bucket), f"users/{user}/"


def _get_client():
    global _client
    if _client is None:
        if client_factory is not None:
            _client = client_factory()
        else:
            import boto3
            from botocore.config import Config
            timeout = float(value("aws", "timeout_s") or 4)
            session = boto3.Session(profile_name=value("aws", "profile") or None,
                                    region_name=value("aws", "region") or None)
            _client = session.client("s3", config=Config(connect_timeout=timeout, read_timeout=timeout,
                                                         retries={"max_attempts": 2}))
    return _client


def _code(exc: BaseException) -> str:
    resp = getattr(exc, "response", None)
    if isinstance(resp, dict):
        return str(resp.get("Error", {}).get("Code", ""))
    return ""


def _reason(exc: BaseException) -> str:
    """Short, human reason for the Settings label and the log."""
    name = type(exc).__name__
    if isinstance(exc, ImportError):
        return "boto3 not installed"
    if name in ("NoCredentialsError", "PartialCredentialsError", "CredentialRetrievalError"):
        return "no AWS credentials"
    if name == "ProfileNotFound":
        return "AWS profile not found"
    if name == "NoRegionError":
        return "no AWS region"
    if name in ("EndpointConnectionError", "ConnectTimeoutError", "ReadTimeoutError",
                "ConnectionClosedError") or isinstance(exc, (ConnectionError, TimeoutError)):
        return "no connection"
    code = _code(exc)
    if code == "NoSuchBucket":
        return "bucket not found"
    if code in ("InvalidAccessKeyId", "SignatureDoesNotMatch", "ExpiredToken"):
        return "AWS credentials rejected"
    if code in ("AccessDenied", "403"):
        return "access denied"
    if isinstance(exc, ValueError):
        return "cloud copy unreadable"
    return code or name


def _set_status(state: str, text: str) -> None:
    global _status
    with _lock:
        _status = (state, text)


def _log_once(key: str, msg: str) -> None:
    with _lock:
        if key in _logged:
            return
        _logged.add(key)
    print(msg)


def _forget_offline() -> None:
    """Back online: a later outage gets logged again."""
    with _lock:
        for k in [k for k in _logged if k.startswith("offline:")]:
            _logged.discard(k)


def _clock() -> str:
    return time.strftime("%I:%M %p").lstrip("0")
