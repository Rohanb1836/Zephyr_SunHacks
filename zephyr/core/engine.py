"""zephyr/core/engine.py    The gesture state machine. Owner: Aadharsh.

    Engine(hub, gate, matcher).start()       starts the vision thread
    run_clip(path, gate=None, matcher=None)  runs a clip through the same pipeline (Ryan's replay tool)

Top-level mode: DISARMED, ARMED, SUSPENDED. Each frame:
 1. Build HandObs for every hand, role = gate.role_of(hand), publish HandsFrame (strangers included).
 2. DISARMED: only both-palms (any hands). On fire, gate.on_arm(hands); True -> ARMED.
 3. ARMED/SUSPENDED: ok, reason = gate.can_act(). Not ok -> SUSPENDED (cancel everything, hide the
    pointer, publish SystemState) or, for "owner_left", gate.on_disarm() and DISARMED. ok -> ARMED.
 4. Controller role = GUEST if gate.guest_active() else OWNER. Hands with that role run the detectors
    in priority pen, level, pointer and pinch, swipe, holds, custom. One acting hand at a time.
 5. Both palms by the OWNER while armed: revoke_guest() if a guest is active, else disarm.
Per-hand states: idle, pointer, pinching, pen, fist_pending, level, hold_palm, hold_custom.
Never publishes a gesture from a STRANGER hand. A lost acting hand goes idle; its state never moves
to another hand. The vision thread makes no OS, network, file or Qt calls.
"""
from __future__ import annotations

import threading
import time
from typing import Optional

from zephyr.bus import bus
from zephyr.config import cfg, maybe_reload
from zephyr.contracts import (GestureEvent, GestureKind, HandObs, HandsFrame, Pose, Role,
                              SystemMode, SystemState, T)
from zephyr.core import features as F
from zephyr.core import holds, levels, pointer, strokes
from zephyr.core.handctx import (FIST_PENDING, HOLD_CUSTOM, HOLD_PALM, IDLE, LEVEL, PEN, PINCHING,
                                 POINTER, HandCtx)
from zephyr.core.swipes import SwipeDetector
from zephyr.core.tracker import HandTracker
from zephyr.stubs import NullGate, NullMatcher


def _now_ms() -> int:
    return int(time.monotonic() * 1000)


HIST_KEEP_MS = 800


class Engine:
    def __init__(self, hub, gate=None, matcher=None, detector="auto"):
        self.hub = hub
        self.gate = gate or NullGate()
        self.matcher = matcher or NullMatcher()
        self.mode = SystemMode.DISARMED
        self.reason = "start"
        self.tracker = HandTracker()
        self.ctxs: dict[int, HandCtx] = {}
        self.acting: Optional[int] = None
        self.swipes = SwipeDetector()
        self.both = holds.BothPalms()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._last_fid = -1
        self._reload_matcher = False
        self._warned: set[str] = set()
        self.detector = None
        if detector == "auto":
            try:
                from zephyr.core.hands import HandDetector
                self.detector = HandDetector()
            except Exception as exc:
                print(f"[engine] hand detector not available, publishing empty frames: {exc!r}")
        else:
            self.detector = detector
        bus.subscribe(T.CUSTOM_RELOAD, self._on_custom_reload)

    # ---------------- lifecycle ----------------
    def start(self) -> "Engine":
        self._running = True
        self.pub(T.SYSTEM, SystemState(self.mode, self.reason))
        self._thread = threading.Thread(target=self._loop, name="zephyr-vision", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._running = False

    def _on_custom_reload(self, _msg) -> None:
        self._reload_matcher = True      # handled on the vision thread

    def _loop(self) -> None:
        next_cfg_check = 0.0
        while self._running:
            now = time.monotonic()
            if now >= next_cfg_check:
                maybe_reload()
                next_cfg_check = now + 1.0
            got = self.hub.latest()
            if got is None or got[2] == self._last_fid:
                time.sleep(0.002)
                continue
            frame, ts, fid = got
            self._last_fid = fid
            try:
                self.process(frame, ts, fid)
            except Exception as exc:     # never let one bad frame kill the vision thread
                self.warn_once(f"frame:{type(exc).__name__}", f"[engine] frame {fid} error: {exc!r}")

    # ---------------- helpers used by detectors ----------------
    def pub(self, topic: str, msg) -> None:
        bus.publish(topic, msg)

    def gesture(self, kind: GestureKind, ctx: HandCtx, **kw) -> None:
        h = ctx.hand
        if h is None or h.role is Role.STRANGER:       # hard wall
            return
        self.pub(T.GESTURE, GestureEvent(kind, ctx.ts, h.hand_id, h.side, h.role, **kw))

    def ink_enabled(self) -> bool:
        c = bus.latest(T.CONTEXT)
        if c is None:
            pc = cfg.get("pen")
            return bool(pc.get("ink_default", False)) if pc else False
        return bool(getattr(c, "ink_enabled", False))

    def warn_once(self, key: str, text: str) -> None:
        if key not in self._warned:
            self._warned.add(key)
            print(text)

    def _set_mode(self, mode: SystemMode, reason: str) -> None:
        if mode is self.mode and reason == self.reason:
            return
        self.mode, self.reason = mode, reason
        self.pub(T.SYSTEM, SystemState(mode, reason))

    # ---------------- per frame ----------------
    def process(self, frame, ts_ms: int, frame_id: int) -> None:
        raws = self.detector.detect(frame, ts_ms) if self.detector else []
        self.process_raws(raws, ts_ms, frame_id)

    def process_raws(self, raws, ts_ms: int, frame_id: int) -> None:
        t_infer = _now_ms()
        if self._reload_matcher:
            self._reload_matcher = False
            try:
                self.matcher.reload()
            except Exception as exc:
                print(f"[engine] matcher.reload error: {exc!r}")

        tracks = self.tracker.update(raws, ts_ms)
        hands: list[HandObs] = []
        for t in tracks:
            pose = t.poses.update(t.lm, t.world, t.scale, t.score)
            obs = HandObs(t.hand_id, t.side, t.lm, t.world, float(t.scale), pose,
                          F.pinch_ratio(t.lm, t.scale), float(t.score))
            try:
                role = self.gate.role_of(obs)
            except Exception as exc:
                self.warn_once("role_of", f"[engine] gate.role_of error: {exc!r}")
                role = Role.STRANGER
            hands.append(HandObs(obs.hand_id, obs.side, obs.landmarks, obs.world, obs.scale,
                                 obs.pose, obs.pinch, obs.score, role))
        self.pub(T.HANDS, HandsFrame(frame_id, ts_ms, tuple(hands)))

        self._update_ctxs(hands, ts_ms)
        self._state_machine(hands, ts_ms)

        self.pub(T.LATENCY, {"frame_id": frame_id, "capture_ms": ts_ms,
                             "inference_ms": t_infer, "publish_ms": _now_ms()})

    def _update_ctxs(self, hands: list[HandObs], ts: int) -> None:
        sign = float(cfg.poses.get("palm_sign", 1))
        for h in hands:
            ctx = self.ctxs.get(h.hand_id)
            if ctx is None:
                ctx = self.ctxs[h.hand_id] = HandCtx(h.hand_id)
            ctx.hand = h
            ctx.ts = ts
            ctx.cx, ctx.cy = F.palm_centre(h.landmarks)
            ctx.scale = h.scale
            ctx.facing = F.palm_facing(h.landmarks, h.side, sign)
            ctx.push_hist(HIST_KEEP_MS)
            ctx.speed = ctx.compute_speed(100)
            if h.pose is not Pose.OPEN:
                ctx.palm_fired = False
            if h.pose is not Pose.UNKNOWN:
                ctx.custom_fired = False
        # hands past their dropout grace: cancel and forget
        for hid in list(self.ctxs):
            if not self.tracker.alive(hid):
                ctx = self.ctxs.pop(hid)
                ctx.ts = ts
                self._cancel_ctx(ctx)
                if self.acting == hid:
                    self.acting = None

    def _state_machine(self, hands: list[HandObs], ts: int) -> None:
        g = self.gate
        visible = [self.ctxs[h.hand_id] for h in hands]

        if self.mode is SystemMode.DISARMED:
            if self.both.step(self, visible, ts):
                armed = False
                try:
                    armed = bool(g.on_arm(tuple(hands)))
                except Exception as exc:
                    print(f"[engine] gate.on_arm error: {exc!r}")
                if armed:
                    self._set_mode(SystemMode.ARMED, "armed")
                    self.both.need_release = True        # do not immediately count toward a pause
                    who = next((c for c in visible if c.hand.pose is Pose.OPEN), None)
                    if who is not None:
                        self.pub(T.GESTURE, GestureEvent(GestureKind.ARM_TOGGLE, ts, who.hand_id,
                                                         who.hand.side, Role.OWNER))
                else:
                    self._set_mode(SystemMode.DISARMED, "no_owner_found")
            return

        try:
            ok, reason = g.can_act()
        except Exception as exc:
            self.warn_once("can_act", f"[engine] gate.can_act error: {exc!r}")
            ok, reason = False, "gate_error"
        if not ok:
            if self.mode is not SystemMode.SUSPENDED or reason == "owner_left":
                self.cancel_all()
            if reason == "owner_left":
                try:
                    g.on_disarm()
                except Exception as exc:
                    print(f"[engine] gate.on_disarm error: {exc!r}")
                self._set_mode(SystemMode.DISARMED, reason)
            else:
                self._set_mode(SystemMode.SUSPENDED, reason)
            return
        if self.mode is SystemMode.SUSPENDED:
            self._set_mode(SystemMode.ARMED, "ok")

        try:
            guest = bool(g.guest_active())
        except Exception:
            guest = False
        controller = Role.GUEST if guest else Role.OWNER

        # step 5: both palms by the owner while armed
        owner_ctxs = [c for c in visible if c.hand.role is Role.OWNER]
        if self.both.step(self, owner_ctxs, ts):
            who = next((c for c in owner_ctxs if c.hand.pose is Pose.OPEN), owner_ctxs[0])
            self.gesture(GestureKind.ARM_TOGGLE, who)
            self.cancel_all()
            self.both.need_release = True
            if guest:
                try:
                    g.revoke_guest()
                except Exception as exc:
                    print(f"[engine] gate.revoke_guest error: {exc!r}")
                self._set_mode(SystemMode.ARMED, "control_returned")
            else:
                try:
                    g.on_disarm()
                except Exception as exc:
                    print(f"[engine] gate.on_disarm error: {exc!r}")
                self._set_mode(SystemMode.DISARMED, "paused")
            return

        allowed = [c for c in visible if c.hand.role is controller]
        allowed_ids = {c.hand_id for c in allowed}
        visible_ids = {c.hand_id for c in visible}
        n_open = sum(1 for c in allowed if c.hand.pose is Pose.OPEN)
        both_active = self.both.active and controller is Role.OWNER

        # the acting hand keeps the lock until its gesture finishes
        if self.acting is not None:
            ctx = self.ctxs.get(self.acting)
            if ctx is None:
                self.acting = None
            elif self.acting in allowed_ids:
                self._run_hand(ctx, n_open, both_active)
                if ctx.state == IDLE:
                    self.acting = None
                return
            elif self.acting in visible_ids:
                self._cancel_ctx(ctx)         # visible but its role changed: stop it
                self.acting = None
            else:
                return                        # inside dropout grace: keep its state and wait

        # nobody holds the lock: fists, pinches and points get the first look
        def prio(c: HandCtx) -> int:
            p = c.hand.pose
            if p is Pose.FIST:
                return 0
            if c.hand.pinch < float(cfg.pinch.down):
                return 1
            if p is Pose.POINT:
                return 2
            return 3

        for ctx in sorted(allowed, key=prio):
            self._run_hand(ctx, n_open, both_active)
            if ctx.state != IDLE:
                self.acting = ctx.hand_id
                break

    def _run_hand(self, ctx: HandCtx, n_open: int, both_active: bool) -> None:
        st = ctx.state
        # active states first; each returns True when it consumed the frame
        if st == PEN:
            strokes.step(self, ctx)
            return
        if st in (FIST_PENDING, LEVEL):
            if levels.step(self, ctx):
                return
        elif st == PINCHING:
            pointer.pinch_step(self, ctx)
            return
        elif st == POINTER:
            if pointer.pointer_step(self, ctx):
                return
        elif st == HOLD_PALM:
            sw = self.swipes.check(ctx)
            if sw:
                holds.palm_cancel(self, ctx)
                self.gesture(GestureKind.SWIPE, ctx, direction=sw[0], fingers=sw[1])
                return
            if holds.palm_step(self, ctx, n_open, both_active):
                return
        elif st == HOLD_CUSTOM:
            if holds.custom_step(self, ctx):
                return

        # IDLE: try to start something, in priority order
        pose = ctx.hand.pose
        if pose is Pose.FIST:
            ctx.pt_frames = 0
            levels.start(self, ctx)
            levels.step(self, ctx)
            return
        if pointer.pinch_is_down(ctx):
            pointer.start_pinch(self, ctx, from_pointer=False)
            return
        if pointer.idle_point(self, ctx):
            return
        sw = self.swipes.check(ctx)
        if sw:
            self.gesture(GestureKind.SWIPE, ctx, direction=sw[0], fingers=sw[1])
            return
        if holds.palm_can_start(ctx, n_open, both_active):
            holds.palm_start(self, ctx)
            holds.palm_step(self, ctx, n_open, both_active)
            return
        holds.custom_try_start(self, ctx)

    # ---------------- cancelling ----------------
    def _cancel_ctx(self, ctx: HandCtx) -> None:
        st = ctx.state
        if ctx.hand is None:
            ctx.reset()
            return
        if st == PEN:
            strokes.end(self, ctx)
        elif st in (FIST_PENDING, LEVEL):
            levels.cancel(self, ctx)
        elif st in (POINTER, PINCHING):
            pointer.cancel(self, ctx)
        elif st == HOLD_PALM:
            holds.palm_cancel(self, ctx)
        elif st == HOLD_CUSTOM:
            holds.custom_cancel(self, ctx)
        ctx.reset()

    def cancel_all(self) -> None:
        for ctx in self.ctxs.values():
            self._cancel_ctx(ctx)
        self.acting = None
        self.both.cancel(self)


def run_clip(path: str, gate=None, matcher=None) -> None:
    """Run a recorded clip through the real pipeline and publish the normal bus events.
    Uses the clip's own timestamps. Returns when the clip ends.
    Defaults to NullGate and NullMatcher."""
    import cv2
    from zephyr import contracts

    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise FileNotFoundError(path)
    eng = Engine(hub=None, gate=gate, matcher=matcher)
    eng.pub(T.SYSTEM, SystemState(eng.mode, "clip"))
    fid, last_ts = 0, -1
    while True:
        ok, frame = cap.read()
        if not ok or frame is None:
            break
        h, w = frame.shape[:2]
        contracts.ASPECT = w / h
        ts = int(cap.get(cv2.CAP_PROP_POS_MSEC))
        if ts <= last_ts:
            ts = last_ts + 1
        last_ts = ts
        fid += 1
        eng.process(frame, ts, fid)
    cap.release()
