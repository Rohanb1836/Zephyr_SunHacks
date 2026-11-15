"""zephyr/tv/simulator.py  A stand-in Roku TV for demos. Owner: Swayam.

    python -m zephyr.tv.simulator                    opens the TV at http://127.0.0.1:8060
    menu bar icon > Roku TV > Start TV simulator     does the same from inside Zephyr

It answers the External Control Protocol requests a Roku TV answers on port 8060
(keypress, keydown, keyup, launch, query/apps, query/device-info, query/active-app), and
its browser page is the TV: a home screen of apps, a video player, an on-screen keyboard,
volume, and a log of every request it receives. Zephyr talks to it exactly as it talks to
the real TV, so the demo runs the same code. It also answers Find TV (SSDP) on this Mac.

    --lan       listen on the whole network, for a second laptop driving the projector
    --limited   refuse key presses like a Roku left in Limited mode, to show that message
"""
from __future__ import annotations

import argparse
import json
import re
import socket
import struct
import threading
import time
import urllib.parse
import webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from xml.sax.saxutils import escape

from zephyr.tv.ecp import KEYS, PORT

# real Roku channel ids, so a "launch 837" binding works on the real TV too
APPS = [("837", "YouTube"), ("12", "Netflix"), ("13", "Prime Video"), ("2285", "Hulu"),
        ("291097", "Disney+"), ("22297", "Spotify"), ("551012", "Apple TV"), ("151908", "The Roku Channel")]
COLS = 4
KEYBOARD = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890") + ["Space", "Delete"]
KB_COLS = 6
SPACE, DELETE = len(KEYBOARD) - 2, len(KEYBOARD) - 1
RESULT_APPS = ["YouTube", "Netflix", "Prime Video"]
NAME = "Zephyr demo TV"


class TvState:
    """What the TV is showing. Every ECP request changes it; the page draws it."""

    def __init__(self, limited: bool = False) -> None:
        self.lock = threading.Lock()
        self.limited = limited
        self.screen = "home"          # home, app, search, off
        self.zone = "grid"            # home: menu or grid; app: player or bar; search: keyboard or results
        self.menu = 0
        self.focus = 0
        self.app = APPS[0]
        self.title = ""
        self.playing = False
        self.position = 0.0
        self.since = time.monotonic()
        self.length = 24 * 60.0
        self.kb = 0
        self.query = ""
        self.result = 0
        self.back_to = "home"
        self.volume = 18
        self.muted = False
        self.volume_until = 0.0
        self.info_until = 0.0
        self.note = ""
        self.note_until = 0.0
        self.log: deque = deque(maxlen=4)
        self.count = 0

    # ---------- the request log the page shows ----------
    def record(self, line: str) -> None:
        with self.lock:
            self.count += 1
            self.log.appendleft({"n": self.count, "t": time.strftime("%H:%M:%S"), "line": line})

    # ---------- playback clock ----------
    def _pos(self, now: float) -> float:
        pos = self.position + (now - self.since if self.playing else 0.0)
        return max(0.0, min(self.length, pos))

    def _play(self, on: bool, now: float) -> None:
        self.position, self.since, self.playing = self._pos(now), now, on

    def _seek(self, seconds: float, now: float) -> None:
        self.position, self.since = max(0.0, min(self.length, self._pos(now) + seconds)), now

    def _say(self, text: str, now: float) -> None:
        self.note, self.note_until = text, now + 2.5

    # ---------- keys ----------
    def press(self, key: str) -> bool:
        """One remote key. False for a key Roku does not have (the request gets a 400)."""
        now = time.monotonic()
        with self.lock:
            if key.startswith("Lit_"):
                ch = urllib.parse.unquote(key[4:])
                if self.screen == "search" and ch:
                    self.query = (self.query + ch.lower())[:40]
                return True
            if key == "PowerOn" or (self.screen == "off" and key in ("Home", "Select", "Play")):
                if self.screen == "off":
                    self.screen, self.zone = "home", "grid"
                return True
            if key not in KEYS:
                return False
            if self.screen == "off":
                return True
            if key in ("VolumeUp", "VolumeDown"):
                self.volume = max(0, min(100, self.volume + (1 if key == "VolumeUp" else -1)))
                self.muted, self.volume_until = False, now + 2.5
            elif key == "VolumeMute":
                self.muted, self.volume_until = not self.muted, now + 2.5
            elif key == "PowerOff":
                self._play(False, now)
                self.screen = "off"
            elif key == "Home":
                self._home(now, to_menu=True)
            elif key == "Info":
                self.info_until = now + 3.0
            elif key == "Search":
                self._say("The Search key starts voice search, which the simulator doesn't do", now)
            elif key == "FindRemote":
                self._say("Remote found: it's your hand", now)
            elif key.startswith(("Input", "Channel")):
                self._say(f"{key}: TV inputs aren't simulated", now)
            elif self.screen == "home":
                self._home_key(key, now)
            elif self.screen == "app":
                self._app_key(key, now)
            elif self.screen == "search":
                self._search_key(key, now)
            return True

    def launch(self, app_id: str) -> bool:
        with self.lock:
            app = next((a for a in APPS if a[0] == app_id), None)
            if app is None:
                return False
            self._launch(app, time.monotonic())
            return True

    def _home(self, now: float, to_menu: bool = False) -> None:
        """Home puts the focus on the menu, like a Roku; Back returns to the app's tile."""
        self._play(False, now)
        self.screen = "home"
        self.zone, self.menu = ("menu", 0) if to_menu else ("grid", self.menu)

    def _launch(self, app, now: float, title: str = "") -> None:
        self.app, self.screen, self.zone = app, "app", "player"
        self.title = title or f"Featured on {app[1]}"
        self.position, self.since, self.playing = 0.0, now, True

    def _open_search(self, back_to: str, now: float) -> None:
        if back_to == "app":
            self._play(False, now)
        self.back_to, self.screen, self.zone = back_to, "search", "keyboard"
        self.kb, self.query, self.result = 0, "", 0

    def _home_key(self, key: str, now: float) -> None:
        n = len(APPS)
        if self.zone == "menu":
            if key == "Up":
                self.menu = max(0, self.menu - 1)
            elif key == "Down":
                self.menu = min(1, self.menu + 1)
            elif key == "Right":
                self.zone = "grid"
            elif key in ("Select", "Enter"):
                if self.menu == 1:
                    self._open_search("home", now)
                else:
                    self.zone = "grid"
            return
        col = self.focus % COLS
        if key == "Left":
            if col == 0:
                self.zone = "menu"
            else:
                self.focus -= 1
        elif key == "Right" and col < COLS - 1 and self.focus + 1 < n:
            self.focus += 1
        elif key == "Up" and self.focus >= COLS:
            self.focus -= COLS
        elif key == "Down" and self.focus + COLS < n:
            self.focus += COLS
        elif key == "Fwd":
            self.focus = min(n - 1, self.focus + COLS)
        elif key == "Rev":
            self.focus = max(0, self.focus - COLS)
        elif key in ("Select", "Enter", "Play"):
            self._launch(APPS[self.focus], now)

    def _app_key(self, key: str, now: float) -> None:
        if self.zone == "bar":
            if key in ("Down", "Back"):
                self.zone = "player"
            elif key in ("Select", "Enter"):
                self._open_search("app", now)
            return
        if key in ("Play", "Select", "Enter"):
            self._play(not self.playing, now)
        elif key in ("Fwd", "Right"):
            self._seek(10, now)
        elif key in ("Rev", "Left"):
            self._seek(-10, now)
        elif key == "InstantReplay":
            self._seek(-7, now)
        elif key == "Up":
            self.zone = "bar"
        elif key == "Back":
            self._home(now)

    def _search_key(self, key: str, now: float) -> None:
        if key == "Back":
            if self.back_to == "app":
                self.screen, self.zone = "app", "player"
            else:
                self._home(now)
            return
        if key == "Backspace":
            self.query = self.query[:-1]
            return
        if self.zone == "results":
            if key == "Left":
                self.result = max(0, self.result - 1)
            elif key == "Right":
                self.result = min(len(RESULT_APPS) - 1, self.result + 1)
            elif key == "Up":
                self.zone = "keyboard"
            elif key in ("Select", "Enter", "Play"):
                name = RESULT_APPS[self.result]
                app = next(a for a in APPS if a[1] == name)
                self._launch(app, now, title=self.query.strip().title())
            return
        if key in ("Enter", "Play"):
            if self.query:
                self.zone = "results"
            return
        if self.kb >= SPACE:                       # bottom row: Space and Delete, each half wide
            if key == "Left":
                self.kb = SPACE
            elif key == "Right":
                self.kb = DELETE
            elif key == "Up":
                self.kb = SPACE - KB_COLS if self.kb == SPACE else SPACE - KB_COLS + 3
            elif key == "Down" and self.query:
                self.zone = "results"
        else:
            row, col = divmod(self.kb, KB_COLS)
            if key == "Left" and col > 0:
                self.kb -= 1
            elif key == "Right" and col < KB_COLS - 1:
                self.kb += 1
            elif key == "Up" and row > 0:
                self.kb -= KB_COLS
            elif key == "Down":
                self.kb = self.kb + KB_COLS if self.kb + KB_COLS < SPACE else (SPACE if col < 3 else DELETE)
        if key == "Select":
            k = KEYBOARD[self.kb]
            if k == "Space":
                self.query = (self.query + " ")[:40]
            elif k == "Delete":
                self.query = self.query[:-1]
            else:
                self.query = (self.query + k.lower())[:40]

    # ---------- answers ----------
    def snapshot(self) -> dict:
        now = time.monotonic()
        with self.lock:
            return {
                "screen": self.screen, "zone": self.zone, "menu": self.menu, "focus": self.focus,
                "apps": [name for _, name in APPS], "app": self.app[1], "title": self.title,
                "playing": self.playing, "position": self._pos(now), "length": self.length,
                "kb": self.kb, "keyboard": KEYBOARD, "query": self.query,
                "result": self.result, "results": RESULT_APPS if self.query.strip() else [],
                "volume": self.volume, "muted": self.muted, "showVolume": now < self.volume_until,
                "showInfo": now < self.info_until, "note": self.note if now < self.note_until else "",
                "log": list(self.log), "name": NAME, "limited": self.limited,
            }

    def apps_xml(self) -> str:
        rows = "".join(f'<app id="{i}" type="appl" version="1.0.0">{escape(n)}</app>' for i, n in APPS)
        return f'<?xml version="1.0" encoding="UTF-8" ?>\n<apps>{rows}</apps>\n'

    def device_xml(self) -> str:
        with self.lock:
            power = "DisplayOff" if self.screen == "off" else "PowerOn"
        fields = {"udn": "zephyr-sim-0001", "serial-number": "ZEPHYRSIM0001", "vendor-name": "Zephyr",
                  "model-name": "Roku TV (simulated)", "model-number": "SIM-1", "user-device-name": NAME,
                  "is-tv": "true", "power-mode": power, "software-version": "15.0.0",
                  "supports-find-remote": "true", "developer-enabled": "false"}
        body = "".join(f"<{k}>{escape(v)}</{k}>" for k, v in fields.items())
        return f'<?xml version="1.0" encoding="UTF-8" ?>\n<device-info>{body}</device-info>\n'

    def active_xml(self) -> str:
        with self.lock:
            app = self.app if self.screen == "app" else None
        inner = f'<app id="{app[0]}" type="appl" version="1.0.0">{escape(app[1])}</app>' if app else "<app>Roku</app>"
        return f'<?xml version="1.0" encoding="UTF-8" ?>\n<active-app>{inner}</active-app>\n'


def _handler(state: TvState):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ZephyrTvSimulator/1.0"

        def log_message(self, *args) -> None:       # quiet: the page shows the log
            pass

        def _send(self, code: int, body: str = "", ctype: str = "text/plain; charset=utf-8") -> None:
            data = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            path = urllib.parse.urlsplit(self.path).path
            if path in ("/", "/index.html"):
                return self._send(200, PAGE, "text/html; charset=utf-8")
            if path == "/state":
                return self._send(200, json.dumps(state.snapshot()), "application/json")
            answers = {"/query/apps": state.apps_xml, "/query/device-info": state.device_xml,
                       "/query/active-app": state.active_xml}
            if path.startswith("/query/"):
                state.record(f"GET {path}")          # only ECP requests go in the log, not the browser's
            if path in answers:
                return self._send(200, answers[path](), "text/xml; charset=utf-8")
            self._send(404, "Not found")

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            path = urllib.parse.urlsplit(self.path).path
            state.record(f"POST {urllib.parse.unquote(path)}")
            m = re.match(r"^/(keypress|keydown|keyup)/(.+)$", path)
            if m:
                if state.limited:
                    return self._send(403, "ECP command not allowed in Limited mode")
                if m.group(1) == "keyup":
                    return self._send(200)
                return self._send(200 if state.press(urllib.parse.unquote(m.group(2))) else 400)
            m = re.match(r"^/launch/(\w+)$", path)
            if m:
                return self._send(200 if state.launch(m.group(1)) else 404)
            self._send(404, "Not found")

    return Handler


def _local_ip_for(remote: str) -> str:
    """The address of this Mac that remote would reach."""
    if remote.startswith("127."):
        return "127.0.0.1"
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect((remote, 9))
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


def _answer_find_tv(port: int, lan: bool) -> None:
    """Reply to SSDP M-SEARCH for roku:ecp like a Roku does, so Find TV finds the simulator."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        sock.bind(("", 1900))
        try:
            group = struct.pack("4s4s", socket.inet_aton("239.255.255.250"), socket.inet_aton("0.0.0.0"))
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, group)
        except OSError:
            pass                      # no multicast: Find TV still asks this Mac directly
    except OSError as exc:
        print(f"[tv-sim] Find TV can't see the simulator ({exc}); type 127.0.0.1 as the TV address")
        return

    def serve() -> None:
        while True:
            try:
                data, addr = sock.recvfrom(2048)
            except OSError:
                return
            text = data.decode("latin-1", "replace").lower()
            if not text.startswith("m-search") or ("roku:ecp" not in text and "ssdp:all" not in text):
                continue
            here = _local_ip_for(addr[0])
            local = addr[0].startswith("127.") or addr[0] == here
            if not lan and not local:
                continue              # listening on this Mac only: don't advertise to the network
            host = here if lan else "127.0.0.1"
            reply = ("HTTP/1.1 200 OK\r\nCache-Control: max-age=3600\r\nST: roku:ecp\r\n"
                     f"Location: http://{host}:{port}/\r\nUSN: uuid:roku:ecp:ZEPHYRSIM0001\r\n\r\n")
            try:
                sock.sendto(reply.encode(), addr)
            except OSError:
                pass

    threading.Thread(target=serve, name="zephyr-tv-ssdp", daemon=True).start()


def serve(port: int = PORT, lan: bool = False, limited: bool = False, find_tv: bool = True) -> ThreadingHTTPServer:
    """Start a simulator on background threads and return its server (server.state is the TV)."""
    state = TvState(limited)
    server = ThreadingHTTPServer(("0.0.0.0" if lan else "127.0.0.1", port), _handler(state))
    server.daemon_threads = True
    server.state = state
    threading.Thread(target=server.serve_forever, name="zephyr-tv-sim", daemon=True).start()
    if find_tv:
        _answer_find_tv(server.server_address[1], lan)
    return server


_started: Optional[ThreadingHTTPServer] = None


def start(open_page: bool = True) -> str:
    """For the menu bar: start the simulator once, open its page, return the address for Zephyr."""
    global _started
    if _started is None:
        try:
            _started = serve()
        except OSError:
            pass                      # port 8060 is taken, most likely by a simulator already running
    port = _started.server_address[1] if _started else PORT
    if open_page:
        webbrowser.open(f"http://127.0.0.1:{port}/")
    return "127.0.0.1" if port == PORT else f"127.0.0.1:{port}"


def main() -> int:
    ap = argparse.ArgumentParser(description="A stand-in Roku TV for Zephyr demos")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--lan", action="store_true", help="listen on the whole network, not just this Mac")
    ap.add_argument("--limited", action="store_true", help="refuse key presses like a Roku in Limited mode")
    ap.add_argument("--no-open", action="store_true", help="don't open the page in the browser")
    args = ap.parse_args()
    server = serve(args.port, args.lan, args.limited)
    port = server.server_address[1]
    host = _local_ip_for("8.8.8.8") if args.lan else "127.0.0.1"
    print(f"Zephyr demo TV is on: http://127.0.0.1:{port}/  (full screen it on the projector)")
    print(f"In Zephyr, use the TV address {host if port == PORT else f'{host}:{port}'}, then Roku TV > Control the TV.")
    if not args.no_open:
        webbrowser.open(f"http://127.0.0.1:{port}/")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        server.shutdown()
    return 0


PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Zephyr demo TV</title>
<style>
:root { --text: #f2eefb; --dim: #a79fc0; --focus: #ffffff; --accent: #c6a3ff; --ok: #8fe3c0; }
* { box-sizing: border-box; }
html, body { margin: 0; height: 100%; background: #000; color: var(--text); overflow: hidden;
  font-family: -apple-system, "SF Pro Display", "Segoe UI", system-ui, sans-serif; }
body { display: grid; place-items: center; }
#tv { position: relative; width: min(100vw, 177.78vh); aspect-ratio: 16 / 9; font-size: min(1.25vw, 2.22vh);
  background: radial-gradient(120% 90% at 20% 0%, #241a48 0%, #0d0a1a 55%, #07060c 100%); overflow: hidden; }
.badge { position: absolute; top: 1.3em; left: 50%; transform: translateX(-50%); font-size: .95em; color: var(--dim);
  letter-spacing: .14em; text-transform: uppercase; white-space: nowrap; }
header { position: absolute; top: 3.2em; left: 4em; right: 4em; display: flex; justify-content: space-between; align-items: center; }
.brand { font-weight: 800; font-size: 2.3em; letter-spacing: -.02em; }
.brand small { font-weight: 600; font-size: .42em; color: var(--dim); letter-spacing: .12em; text-transform: uppercase; margin-left: .9em; vertical-align: middle; }
#clock { font-size: 1.9em; color: var(--dim); font-variant-numeric: tabular-nums; }
section { position: absolute; inset: 9.5em 4em 4.5em 4em; display: none; }
section.on { display: block; }
#home.on { display: grid; grid-template-columns: 13em minmax(0, 1fr); gap: 3em; }
#menu div { font-size: 1.9em; padding: .45em .75em; border-radius: .45em; color: var(--dim); margin-bottom: .35em; }
#menu div.focus { color: var(--text); background: rgba(255,255,255,.12); box-shadow: inset 0 0 0 .12em var(--focus); }
#grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 2em; align-content: start; padding-top: .4em; }
.tile { min-width: 0; aspect-ratio: 16 / 10; border-radius: 1em; display: flex; align-items: flex-end; padding: .8em 1em;
  font-weight: 750; font-size: 1.6em; line-height: 1.1; letter-spacing: -.01em; transition: transform .12s ease, box-shadow .12s ease; }
.tile.focus { transform: scale(1.08); box-shadow: 0 0 0 .18em var(--focus), 0 1em 3em rgba(0,0,0,.55), 0 0 2.4em rgba(198,163,255,.5); }
.bar { display: flex; justify-content: space-between; align-items: center; font-size: 1.9em; }
.bar b { font-weight: 800; }
#appsearch { padding: .3em 1em; border-radius: 2em; border: .07em solid rgba(255,255,255,.28); color: var(--dim); }
#appsearch.focus { color: var(--text); background: rgba(255,255,255,.12); box-shadow: 0 0 0 .1em var(--focus); }
.video { position: absolute; top: 4.6em; bottom: 7em; left: 0; right: 0; border-radius: 1.2em; overflow: hidden;
  background: linear-gradient(135deg, #2b2152, #12203b 60%, #0b1a24); display: grid; place-items: center; }
.video::after { content: ""; position: absolute; inset: -10%; background: radial-gradient(45% 40% at 50% 45%, rgba(198,163,255,.22), transparent 70%);
  animation: drift 7s ease-in-out infinite alternate; }
.video.paused::after { animation-play-state: paused; }
@keyframes drift { to { transform: translate(8%, -5%) scale(1.15); } }
.vtitle { position: relative; z-index: 1; text-align: center; }
.vtitle b { display: block; font-size: 3.8em; letter-spacing: -.02em; }
.vtitle span { font-size: 1.5em; color: var(--dim); letter-spacing: .12em; text-transform: uppercase; }
.paused .vtitle span { color: var(--accent); }
.progress { position: absolute; left: 0; right: 0; bottom: 3.6em; height: .5em; border-radius: 1em; background: rgba(255,255,255,.18); }
.progress i { position: absolute; left: 0; top: 0; bottom: 0; border-radius: 1em; background: var(--accent); }
.times { position: absolute; left: 0; right: 0; bottom: 1.2em; display: flex; justify-content: space-between; font-size: 1.4em; color: var(--dim); font-variant-numeric: tabular-nums; }
#search.on { display: grid; grid-template-columns: 27em 1fr; gap: 3.5em; }
.query { font-size: 2em; font-weight: 700; padding: .4em .7em; border-radius: .45em; background: rgba(255,255,255,.08); margin-bottom: .7em; min-height: 2em; white-space: nowrap; overflow: hidden; }
.caret { display: inline-block; width: .08em; height: 1em; background: var(--accent); vertical-align: -.12em; margin-left: .06em; animation: blink 1s steps(1) infinite; }
@keyframes blink { 50% { opacity: 0; } }
#kb { display: grid; grid-template-columns: repeat(6, 1fr); gap: .5em; }
#kb div { text-align: center; padding: .38em 0; border-radius: .4em; background: rgba(255,255,255,.07); font-size: 1.45em; font-weight: 650; }
#kb div.wide { grid-column: span 3; }
#kb div.focus { background: var(--text); color: #120c22; }
.results h3 { font-size: 1.6em; color: var(--dim); font-weight: 600; margin: 0 0 .4em; }
#results { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1.6em; }
.result { aspect-ratio: 4 / 5; border-radius: 1em; padding: 1em; display: flex; flex-direction: column; justify-content: flex-end;
  background: linear-gradient(180deg, #2c2152, #151025); font-weight: 750; font-size: 1.5em; }
.result small { color: var(--dim); font-weight: 600; font-size: .7em; margin-top: .3em; }
.result.focus { transform: scale(1.05); box-shadow: 0 0 0 .16em var(--focus), 0 0 2em rgba(198,163,255,.45); }
.hint { margin: 0 0 1.4em; color: var(--dim); font-size: 1.25em; }
#off { position: absolute; inset: 0; background: #000; display: none; place-items: center; color: #3b3552; font-size: 1.7em; }
#off.on { display: grid; }
#volume { position: absolute; right: 3em; top: 50%; transform: translateY(-50%); width: 4.6em; padding: 1.2em .9em; border-radius: 2.5em;
  background: rgba(10,8,20,.88); border: .07em solid rgba(255,255,255,.16); display: none; text-align: center; }
#volume.on { display: block; }
.vbar { height: 14em; width: .9em; margin: 0 auto 1em; border-radius: 1em; background: rgba(255,255,255,.15); position: relative; overflow: hidden; }
.vbar i { position: absolute; left: 0; right: 0; bottom: 0; background: var(--accent); }
#volume b { font-size: 2em; font-variant-numeric: tabular-nums; }
#volume small { display: block; color: var(--dim); font-size: 1.1em; }
#info { position: absolute; left: 4em; right: 4em; bottom: 4.5em; padding: 1.3em 2em; border-radius: 1em; background: rgba(10,8,20,.92);
  border: .07em solid rgba(255,255,255,.15); display: none; font-size: 1.5em; }
#info.on { display: block; }
#note { position: absolute; left: 50%; top: 6.2em; transform: translateX(-50%); padding: .55em 1.4em; border-radius: 2em;
  background: rgba(10,8,20,.92); border: .07em solid var(--accent); font-size: 1.45em; display: none; white-space: nowrap; }
#note.on { display: block; }
#log { position: absolute; left: 4em; right: 4em; bottom: 1.5em; display: flex; align-items: baseline; gap: 1.4em;
  font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 1.1em; white-space: nowrap; overflow: hidden; }
#log h4 { margin: 0; flex: none; font-size: .78em; letter-spacing: .14em; text-transform: uppercase; color: var(--accent); }
#loglines { display: flex; gap: 2em; min-width: 0; overflow: hidden; }
#loglines span { color: var(--dim); }
#loglines span:first-child { color: var(--ok); }
#log time { opacity: .55; margin-right: .6em; }
#lost { position: absolute; left: 50%; top: 3.6em; transform: translateX(-50%); color: #ffd08a; font-size: 1.2em; display: none; }
@media (prefers-reduced-motion: reduce) { .video::after, .caret { animation: none; } .tile { transition: none; } }
</style>
</head>
<body>
<div id="tv">
  <div class="badge">Simulated Roku TV · External Control Protocol on port 8060</div>
  <header><div class="brand">Zephyr TV<small id="tvname"></small></div><div id="clock"></div></header>
  <section id="home"><nav id="menu"><div>Home</div><div>Search</div></nav><div id="grid"></div></section>
  <section id="app">
    <div class="bar"><b id="appname"></b><span id="appsearch">Search</span></div>
    <div class="video" id="video"><div class="vtitle"><b id="vtitle"></b><span id="vstate"></span></div></div>
    <div class="progress"><i id="prog"></i></div>
    <div class="times"><span id="tnow">0:00</span><span id="tlen">0:00</span></div>
  </section>
  <section id="search">
    <div><div class="query" id="query"></div><div id="kb"></div></div>
    <div class="results"><h3 id="rhead"></h3><p class="hint">Arrows and OK type a letter. With Zephyr, hold up a palm and write in the air.</p><div id="results"></div></div>
  </section>
  <div id="off">The TV is off. Press Home to turn it on.</div>
  <div id="volume"><div class="vbar"><i id="vfill"></i></div><b id="vnum"></b><small>Volume</small></div>
  <div id="info"></div>
  <div id="note"></div>
  <aside id="log"><h4>ECP requests received</h4><div id="loglines"></div></aside>
  <div id="lost">The simulator stopped. Start it again from Zephyr.</div>
</div>
<script>
const $ = (id) => document.getElementById(id);
const PALETTE = [["#5b3fd0", "#2a1d66"], ["#2f5fcf", "#172c63"], ["#1d8a7a", "#0d3a35"], ["#8a4bd6", "#3a2063"],
                 ["#b55a8a", "#4f2140"], ["#3f8a58", "#173a26"], ["#b87634", "#4f2f12"], ["#4a6fa8", "#1c2d4d"]];
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const mmss = (s) => { s = Math.max(0, Math.floor(s)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
const titled = (s) => s.replace(/\b\w/g, (c) => c.toUpperCase());
let last = "";

function render(s) {
  for (const id of ["home", "app", "search"]) $(id).classList.toggle("on", s.screen === id);
  $("off").classList.toggle("on", s.screen === "off");
  $("tvname").textContent = s.name;
  [...$("menu").children].forEach((d, i) => d.classList.toggle("focus", s.screen === "home" && s.zone === "menu" && s.menu === i));
  const grid = $("grid");
  if (grid.children.length !== s.apps.length) {
    grid.innerHTML = s.apps.map((n, i) => {
      const [a, b] = PALETTE[i % PALETTE.length];
      return `<div class="tile" style="background: linear-gradient(150deg, ${a}, ${b})">${esc(n)}</div>`;
    }).join("");
  }
  [...grid.children].forEach((t, i) => t.classList.toggle("focus", s.screen === "home" && s.zone === "grid" && s.focus === i));
  $("appname").textContent = s.app;
  $("appsearch").classList.toggle("focus", s.screen === "app" && s.zone === "bar");
  $("vtitle").textContent = s.title;
  $("vstate").textContent = s.playing ? "Playing" : "Paused";
  $("video").classList.toggle("paused", !s.playing);
  $("prog").style.width = `${(100 * s.position) / s.length}%`;
  $("tnow").textContent = mmss(s.position);
  $("tlen").textContent = mmss(s.length);
  $("query").innerHTML = `${esc(s.query)}<span class="caret"></span>`;
  const kb = $("kb");
  if (kb.children.length !== s.keyboard.length) kb.innerHTML = s.keyboard.map((k) => `<div class="${k.length > 1 ? "wide" : ""}">${k}</div>`).join("");
  [...kb.children].forEach((d, i) => d.classList.toggle("focus", s.screen === "search" && s.zone === "keyboard" && s.kb === i));
  $("rhead").textContent = s.query.trim() ? `Results for “${s.query.trim()}”` : "Results show up as you type";
  $("results").innerHTML = s.results.map((a, i) =>
    `<div class="result ${s.zone === "results" && s.result === i ? "focus" : ""}">${esc(titled(s.query.trim()))}<small>${esc(a)}</small></div>`).join("");
  $("volume").classList.toggle("on", s.showVolume);
  $("vfill").style.height = `${s.muted ? 0 : s.volume}%`;
  $("vnum").textContent = s.muted ? "Mute" : s.volume;
  $("info").classList.toggle("on", s.showInfo);
  $("info").textContent = `${s.name} · Roku TV (simulated) · listening for ECP on port 8060`;
  $("note").classList.toggle("on", !!s.note);
  $("note").textContent = s.note;
  $("loglines").innerHTML = s.log.length
    ? s.log.map((e) => `<span><time>${e.t}</time>${esc(e.line)}</span>`).join("")
    : "<span>Waiting for Zephyr: menu bar › Roku TV › Control the TV</span>";
}

async function tick() {
  try {
    const res = await fetch("/state", { cache: "no-store" });
    const s = await res.json();
    $("lost").style.display = "none";
    const sig = JSON.stringify(s);
    if (sig !== last) { last = sig; render(s); }
  } catch (err) {
    $("lost").style.display = "block";
  }
  setTimeout(tick, 100);
}
const clock = () => { $("clock").textContent = new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }); };
clock(); setInterval(clock, 10000);
tick();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
