"""zephyr/tv/ecp.py  Talks to a Roku TV with Roku's External Control Protocol. Owner: Swayam.

ECP is plain HTTP on port 8060 of the TV. POST /keypress/<Key> presses a remote button,
POST /launch/<channel id> opens an app, GET /query/... reads the TV. A Roku answers an
SSDP M-SEARCH for "roku:ecp" on 239.255.255.250:1900, which is how find_tvs() works.

Since Roku OS 14.1 the TV only takes key presses when Settings > System > Advanced system
settings > Control by mobile apps > Network access is Enabled (Limited is the new default).
A refused request comes back as EcpError(BLOCKED).

RokuClient sends one request at a time on its own thread (zephyr-tv), so a slow or
missing TV never holds up the action router. Results come back through on_result.
"""
from __future__ import annotations

import queue
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Callable, Optional

PORT = 8060

# Roku's keypress names (ECP docs, "Keypress key values"). PowerOn is not in the list.
KEYS = ("Home", "Rev", "Fwd", "Play", "Select", "Left", "Right", "Down", "Up", "Back",
        "InstantReplay", "Info", "Backspace", "Search", "Enter", "FindRemote",
        "VolumeDown", "VolumeMute", "VolumeUp", "PowerOff", "ChannelUp", "ChannelDown",
        "InputTuner", "InputHDMI1", "InputHDMI2", "InputHDMI3", "InputHDMI4", "InputAV1")
ALIASES = {"ok": "Select", "playpause": "Play", "pause": "Play", "mute": "VolumeMute",
           "volup": "VolumeUp", "voldown": "VolumeDown", "power": "PowerOff", "off": "PowerOff",
           "replay": "InstantReplay", "forward": "Fwd", "rewind": "Rev", "delete": "Backspace"}
_BY_LOWER = {k.lower(): k for k in KEYS}

BLOCKED = ("The TV refused the command. On the TV, open Settings > System > Advanced system "
           "settings > Control by mobile apps > Network access and choose Enabled.")


class EcpError(Exception):
    """A request the TV refused or never answered. The message says what to do."""


def key_name(text: str) -> Optional[str]:
    """Roku's spelling of a key, from any case or a friendly alias. None if it is not a key."""
    t = re.sub(r"[\s_-]+", "", str(text or "")).lower()
    return _BY_LOWER.get(t) or ALIASES.get(t)


# LAN requests must not go through an http proxy from the environment
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _base(host: str) -> str:
    host = host.strip()
    return f"http://{host}" if ":" in host else f"http://{host}:{PORT}"


def request(host: str, path: str, method: str = "POST", timeout: float = 2.5) -> bytes:
    """One ECP request. Raises EcpError with a sentence for the user."""
    if not host:
        raise EcpError("No TV yet. Use Find TV in the menu bar, or start the TV simulator.")
    req = urllib.request.Request(_base(host) + path, data=b"" if method == "POST" else None, method=method)
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise EcpError(BLOCKED) from None
        if exc.code == 404 and path.startswith("/launch/"):
            raise EcpError("That app isn't installed on the TV.") from None
        raise EcpError(f"The TV answered {exc.code} to {path}.") from None
    except (urllib.error.URLError, OSError) as exc:
        raise EcpError(f"Can't reach the TV at {host}. Check that the TV is on and on the same Wi-Fi as this Mac.") from exc


def parse_apps(xml_bytes: bytes) -> dict[str, tuple[str, str]]:
    """query/apps -> {lower-case name: (channel id, name)}.
    The TV answers <apps><app id="12" type="appl" version="5.1">Netflix</app>...</apps>."""
    out: dict[str, tuple[str, str]] = {}
    for app in ET.fromstring(xml_bytes).iter("app"):
        name = (app.text or "").strip()
        if name and app.get("id"):
            out[name.lower()] = (app.get("id"), name)
    return out


def parse_fields(xml_bytes: bytes) -> dict[str, str]:
    """query/device-info and query/active-app -> {tag: text} for the top-level fields."""
    root = ET.fromstring(xml_bytes)
    return {child.tag: (child.text or "").strip() for child in root}


def find_tvs(timeout: float = 2.0) -> list[dict[str, str]]:
    """Roku TVs on this network: [{"host": "192.168.1.23", "usn": ...}], nearest first.
    Also asks this Mac directly, so the TV simulator is found when the Wi-Fi blocks multicast."""
    probe = ("M-SEARCH * HTTP/1.1\r\nHost: 239.255.255.250:1900\r\nMan: \"ssdp:discover\"\r\n"
             "MX: 1\r\nST: roku:ecp\r\n\r\n").encode()
    found: dict[str, dict[str, str]] = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.settimeout(0.2)
        for target in (("239.255.255.250", 1900), ("127.0.0.1", 1900)):
            try:
                sock.sendto(probe, target)
            except OSError:
                pass
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                data, _addr = sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            text = data.decode("latin-1")
            if "roku:ecp" not in text.lower():
                continue
            loc = re.search(r"^location:\s*(\S+)", text, re.I | re.M)
            if not loc:
                continue
            parts = urllib.parse.urlsplit(loc.group(1))
            host = parts.hostname or ""
            if parts.port and parts.port != PORT:
                host = f"{host}:{parts.port}"
            usn = re.search(r"^usn:\s*(\S+)", text, re.I | re.M)
            if host and host not in found:
                found[host] = {"host": host, "usn": usn.group(1) if usn else ""}
    finally:
        sock.close()
    return list(found.values())


class RokuClient:
    """Queued, one-at-a-time ECP requests for one TV.

        client = RokuClient("192.168.1.23", on_result=lambda label, ok, detail: ...)
        client.key("Right")            # returns at once; the press goes out on zephyr-tv
        client.launch("YouTube")       # by name (looked up in query/apps) or by channel id
        client.type_text("lofi")       # into the on-screen keyboard that is open on the TV
    """

    GAP_S = 0.06          # between presses, so the TV sees each one
    MAX_WAITING = 12      # older arrow presses are dropped when the TV falls behind

    def __init__(self, host: str = "", on_result: Optional[Callable[[str, bool, str], None]] = None) -> None:
        self.host = host
        self.on_result = on_result
        self._jobs: "queue.Queue[tuple[str, Callable[[], None], bool]]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._apps: Optional[dict[str, tuple[str, str]]] = None
        self._lock = threading.Lock()

    def set_host(self, host: str) -> None:
        with self._lock:
            self.host = host.strip()
            self._apps = None

    # ---------- blocking calls (run on zephyr-tv, or directly in tests) ----------
    def press(self, key: str) -> None:
        request(self.host, "/keypress/" + urllib.parse.quote(key, safe="_%"))

    def apps(self, refresh: bool = False) -> dict[str, tuple[str, str]]:
        with self._lock:
            cached = None if refresh else self._apps
        if cached is None:
            cached = parse_apps(request(self.host, "/query/apps", "GET"))
            with self._lock:
                self._apps = cached
        return cached

    def launch_now(self, app: str) -> str:
        """Open an app by channel id or by name. Returns the app's name."""
        app = app.strip()
        if app.isdigit():
            request(self.host, f"/launch/{app}")
            return app
        apps = self.apps()
        hit = apps.get(app.lower()) or next((v for k, v in apps.items() if app.lower() in k), None)
        if hit is None:
            raise EcpError(f"No app called {app} on the TV.")
        request(self.host, f"/launch/{hit[0]}")
        return hit[1]

    def type_now(self, text: str) -> None:
        for ch in text:
            self.press("Lit_" + urllib.parse.quote(ch, safe=""))
            time.sleep(self.GAP_S)

    def device_info(self) -> dict[str, str]:
        return parse_fields(request(self.host, "/query/device-info", "GET"))

    # ---------- queued calls (return at once) ----------
    def key(self, key: str, label: Optional[str] = None, announce: bool = True) -> None:
        self._submit(label or key, lambda: self.press(key), announce)

    def launch(self, app: str, announce: bool = True) -> None:
        self._submit(f"Open {app}", lambda: self.launch_now(app), announce)

    def type_text(self, text: str, announce: bool = True) -> None:
        self._submit(f"Type “{text}”", lambda: self.type_now(text), announce)

    def call(self, label: str, fn: Callable[[], None], announce: bool = True) -> None:
        self._submit(label, fn, announce)

    def pending(self) -> int:
        return self._jobs.qsize()

    def _submit(self, label: str, fn: Callable[[], None], announce: bool) -> None:
        if self._jobs.qsize() >= self.MAX_WAITING:
            try:
                self._jobs.get_nowait()          # the TV is behind: drop the oldest press
            except queue.Empty:
                pass
        self._jobs.put((label, fn, announce))
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, name="zephyr-tv", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        while True:
            label, fn, announce = self._jobs.get()
            try:
                fn()
                if announce and self.on_result:
                    self.on_result(label, True, "")
            except EcpError as exc:
                if self.on_result:
                    self.on_result(label, False, str(exc))
            except Exception as exc:          # never let one bad request stop the thread
                if self.on_result:
                    self.on_result(label, False, f"{exc!r}")
            time.sleep(self.GAP_S)
