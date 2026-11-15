"""Route gesture and settings events to a dedicated action worker.

This first milestone handles discrete actions only. Pointer, level and writing
are added separately. The system starts disarmed and queued actions are dropped
whenever it leaves the armed state.
"""
import logging
import queue
import threading
import time

from zephyr.bus import bus
from zephyr.contracts import ActionFired, Role, SystemMode, T
from zephyr.actions.frontapp import find_profile, front_app, load_profiles
from zephyr.actions.mac import MacBackend
from zephyr.actions.runner import ActionRunner

LOG = logging.getLogger(__name__)


class ActionRouter:
    def __init__(self, event_bus, backend):
        self.bus = event_bus
        self.runner = ActionRunner(backend)
        self.profiles = load_profiles()
        self.queue = queue.Queue(maxsize=128)
        self.lock = threading.Lock()
        self.mode = SystemMode.DISARMED
        self.generation = 0
        self.app_id = ''
        self.app_label = ''
        self.profile = self.profiles['default']

    def start(self):
        self.bus.subscribe(T.SYSTEM, self._system)
        self.bus.subscribe(T.GESTURE, self._gesture)
        self.bus.subscribe(T.ACTION_REQUEST, self._request)
        current = self.bus.latest(T.SYSTEM)
        if current is not None:
            self._system(current)
        threading.Thread(target=self._worker, name='zephyr-actions', daemon=True).start()
        return self

    def _system(self, state):
        with self.lock:
            if state.mode != self.mode:
                self.generation += 1
                self.mode = state.mode

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
        if event.role == Role.GUEST and event.key not in ('swipe2.left', 'swipe2.right', 'fist_tap'):
            return
        self._enqueue('gesture', event)

    def _request(self, request):
        self._enqueue('request', request)

    def _update_app(self):
        try:
            app_id, label = front_app()
            if app_id and label.lower() not in ('python', 'python3', 'zephyr'):
                self.app_id, self.app_label = app_id, label
                self.profile = find_profile(app_id, self.profiles)
        except Exception:
            LOG.debug('Could not read foreground app', exc_info=True)

    def _resolve(self, key):
        return self.profile.get('map', {}).get(key) or self.profiles['default'].get('map', {}).get(key)

    def _worker(self):
        while True:
            generation, kind, item = self.queue.get()
            try:
                with self.lock:
                    allowed = self.mode == SystemMode.ARMED and generation == self.generation
                if not allowed:
                    continue
                self._update_app()
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
            finally:
                self.queue.task_done()


_router = None


def start_actions():
    global _router
    if _router is None:
        _router = ActionRouter(bus, MacBackend()).start()
    return _router
