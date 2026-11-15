"""Route gesture, pointer, and level events without blocking the vision thread."""
import logging
import queue
import threading
import time

from zephyr.bus import bus
from zephyr.config import cfg
from zephyr.contracts import ActionFired, ContextState, LevelValue, Role, SystemMode, T
from zephyr.actions.frontapp import find_profile, front_app, load_profiles
from zephyr.actions.mac import MacBackend
from zephyr.actions.runner import ActionRunner

LOG = logging.getLogger(__name__)


class ActionRouter:
    def __init__(self, event_bus, backend):
        self.bus, self.backend = event_bus, backend
        self.runner = ActionRunner(backend)
        self.profiles = load_profiles()
        self.queue = queue.Queue(maxsize=128)
        self.lock = threading.Lock()
        self.mode, self.generation = SystemMode.DISARMED, 0
        self.app_id, self.app_label = '', ''
        self.profile = self.profiles['default']
        self.pending_pointer = None
        self.levels = {'volume': 0.0, 'brightness': 0.0}
        self._last_context = None
        self._started = False

    def start(self):
        if self._started:
            return self
        self._started = True
        for topic, handler in ((T.SYSTEM, self._system), (T.GESTURE, self._gesture),
                               (T.ACTION_REQUEST, self._request), (T.POINTER, self._pointer),
                               (T.LEVEL, self._level)):
            self.bus.subscribe(topic, handler)
        current = self.bus.latest(T.SYSTEM)
        if current is not None:
            self._system(current)
        threading.Thread(target=self._worker, name='zephyr-actions', daemon=True).start()
        threading.Thread(target=self._pointer_loop, name='zephyr-pointer', daemon=True).start()
        return self

    def _system(self, state):
        with self.lock:
            if state.mode != self.mode:
                self.generation += 1
                self.mode = state.mode
                self.pending_pointer = None
                self.levels = {'volume': 0.0, 'brightness': 0.0}

    def _enqueue(self, kind, item):
        with self.lock:
            if self.mode != SystemMode.ARMED:
                return
            generation = self.generation
        try:
            self.queue.put_nowait((generation, kind, item))
        except queue.Full:
            LOG.warning('Action queue full; dropping event')

    def _gesture(self, event):
        if event.role == Role.STRANGER:
            return
        if event.role == Role.GUEST and event.key not in ('swipe2.left', 'swipe2.right', 'fist_tap', 'pinch_tap'):
            return
        self._enqueue('gesture', event)

    def _request(self, request):
        self._enqueue('request', request)

    def _pointer(self, event):
        with self.lock:
            if self.mode == SystemMode.ARMED:
                self.pending_pointer = (self.generation, event)

    def _level(self, event):
        with self.lock:
            if self.mode == SystemMode.ARMED and event.kind in self.levels:
                self.levels[event.kind] += event.delta

    @staticmethod
    def _screen_point(pos, size):
        return tuple(max(0.0, min(1.0, float(v))) * n for v, n in zip(pos, size))

    def _pointer_loop(self):
        while True:
            time.sleep(1 / 60)
            with self.lock:
                pending, self.pending_pointer = self.pending_pointer, None
                valid = pending is not None and self.mode == SystemMode.ARMED and pending[0] == self.generation
            if not valid:
                continue
            try:
                event = pending[1]
                pos = (event.x, event.y) if event.visible else cfg.pointer.park
                self.backend.move_cursor(*self._screen_point(pos, self.backend.screen_size_points()))
            except Exception:
                LOG.exception('Pointer movement failed')

    def _update_app(self):
        try:
            app_id, label = front_app()
            if app_id and label.lower() not in ('python', 'python3', 'zephyr'):
                self.app_id, self.app_label = app_id, label
                self.profile = find_profile(app_id, self.profiles)
            context = ContextState(self.app_id, self.app_label, self.profile['id'], bool(self.profile.get('ink')), 'normal')
            if context != self._last_context:
                self._last_context = context
                self.bus.publish(T.CONTEXT, context)
        except Exception:
            LOG.debug('Could not read foreground app', exc_info=True)

    def _resolve(self, key):
        if key.startswith('custom.'):
            try:
                from zephyr.custom.store import custom_action
                return custom_action(key, self.profile.get('id', 'default'))
            except Exception:
                LOG.exception('Could not load custom gesture binding')
                return None
        return self.profile.get('map', {}).get(key) or self.profiles['default'].get('map', {}).get(key)

    def _flush_levels(self):
        with self.lock:
            if self.mode != SystemMode.ARMED:
                return
            pending, self.levels = self.levels, {'volume': 0.0, 'brightness': 0.0}
            generation = self.generation
        for kind, delta in pending.items():
            if not delta:
                continue
            try:
                with self.lock:
                    if self.mode != SystemMode.ARMED or self.generation != generation:
                        return
                getter = getattr(self.backend, 'get_' + kind)
                setter = getattr(self.backend, 'set_' + kind)
                setter(max(0.0, min(100.0, getter() + delta)))
                self.bus.publish(T.LEVEL_VALUE, LevelValue(kind, getter()))
            except Exception:
                LOG.exception('Could not adjust %s', kind)

    def _worker(self):
        next_level = next_context = 0.0
        while True:
            now = time.monotonic()
            if now >= next_context:
                self._update_app()
                next_context = now + .25
            if now >= next_level:
                self._flush_levels()
                next_level = now + .1
            try:
                generation, kind, item = self.queue.get(timeout=.02)
            except queue.Empty:
                continue
            try:
                with self.lock:
                    allowed = self.mode == SystemMode.ARMED and generation == self.generation
                if not allowed:
                    continue
                if kind == 'gesture' and item.key == 'pinch_tap':
                    if item.pos is None:
                        continue
                    self.backend.click(*self._screen_point(item.pos, self.backend.screen_size_points()))
                    continue
                spec = self._resolve(item.key) if kind == 'gesture' else item.spec
                if not spec:
                    continue
                label = spec.get('label') or str(spec.get('value', spec.get('type', 'Action')))
                try:
                    self.runner.run(spec)
                    self.bus.publish(T.ACTION, ActionFired(label, self.app_label, True, int(time.monotonic() * 1000)))
                except Exception:
                    LOG.exception('Action failed: %s', label)
                    self.bus.publish(T.ACTION, ActionFired(label, self.app_label, False, int(time.monotonic() * 1000)))
            except Exception:
                LOG.exception('Action worker error')
            finally:
                self.queue.task_done()


_router = None


def start_actions():
    global _router
    if _router is None:
        _router = ActionRouter(bus, MacBackend()).start()
    return _router
