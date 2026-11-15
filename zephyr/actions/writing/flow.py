"""Air-writing state machine; network and rendering run off the event thread."""
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from zephyr.contracts import T, WriteState
from zephyr.actions.writing.render import render_strokes
from zephyr.actions.writing.transcribe import transcribe

LOG = logging.getLogger(__name__)


class WritingFlow:
    def __init__(self, bus, config, paste, memory=None, known_apps=()):
        self.bus, self.config, self.paste, self.memory = bus, config, paste, memory
        self.known_apps = list(known_apps)
        self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='zephyr-writing')
        self.phase = 'idle'
        self.mode = 'document'
        self.app_label = ''
        self.strokes = {}
        self.last_activity = 0.0
        self.open_strokes = set()
        self.text = ''
        self.anchor = None
        self.generation = 0
        self.started = False
        self.error_at = 0.0

    def start(self):
        if not self.started:
            self.started = True
            threading.Thread(target=self._timer, daemon=True, name='zephyr-writing-timer').start()
        return self

    def _publish(self, phase, text='', anchor=None):
        self.phase = phase
        self.bus.publish(T.WRITE, WriteState(phase, text, anchor))

    def ready(self, app_label, mode='document'):
        with self.lock:
            self.generation += 1
            self.mode, self.app_label = mode, app_label
            self.strokes, self.open_strokes = {}, set()
            self.text, self.anchor = '', None
            self.last_activity = time.monotonic()
            self._publish('ready')

    def stroke(self, event):
        with self.lock:
            if self.phase not in ('ready', 'inking'):
                return
            if event.phase == 'begin':
                self.open_strokes.add(event.stroke_id)
                self.strokes[event.stroke_id] = []
                if self.phase != 'inking':
                    self._publish('inking')
            if event.stroke_id not in self.strokes:
                return
            self.strokes[event.stroke_id].append((event.x, event.y))
            if event.phase == 'end':
                self.open_strokes.discard(event.stroke_id)
            self.last_activity = time.monotonic()

    def gesture(self, key):
        """Return True if preview consumes this gesture. Paste runs in worker pool."""
        with self.lock:
            if self.phase != 'preview':
                return False
            if key == 'pinch_tap':
                text, mode, app = self.text, self.mode, self.app_label
                self._publish('accepted', text, self.anchor)
                self.generation += 1
                self.pool.submit(self._accept, text, app, mode)
                return True
            if key.startswith('swipe'):
                self.generation += 1
                self._publish('cancelled')
                return True
            return False

    def cancel(self):
        with self.lock:
            if self.phase in ('idle', 'cancelled'):
                return
            self.generation += 1
            self.strokes, self.open_strokes = {}, set()
            self._publish('cancelled')

    def _accept(self, text, app, mode):
        try:
            self.paste(text, mode)
            if self.memory:
                self.memory.remember(text, app, mode)
        except Exception:
            LOG.exception('Could not paste accepted text')

    def _timer(self):
        while True:
            time.sleep(.05)
            with self.lock:
                elapsed = (time.monotonic() - self.last_activity) * 1000
                if self.mode == 'search' and self.phase == 'ready' and elapsed >= float(self.config.write.idle_cancel_ms):
                    self.generation += 1
                    self._publish('cancelled')
                if self.phase == 'error' and time.monotonic() - self.error_at >= 1.5:
                    self.strokes, self.open_strokes = {}, set()
                    self._publish('ready')
                if self.phase == 'inking' and not self.open_strokes and elapsed >= float(self.config.write.pause_ms):
                    strokes = list(self.strokes.values())
                    points = [p for stroke in strokes for p in stroke]
                    if not points:
                        continue
                    self.anchor = ((min(x for x, y in points) + max(x for x, y in points)) / 2,
                                   max(y for x, y in points))
                    token = self.generation
                    self._publish('transcribing', anchor=self.anchor)
                    self.pool.submit(self._transcribe, token, strokes, self.app_label, self.mode)

    def _transcribe(self, token, strokes, app, mode):
        try:
            img = render_strokes(strokes, int(self.config.write.image_long_side), int(self.config.write.stroke_px))
            hints = self.memory.hints() if self.memory else []
            llm = self.config.llm
            text = transcribe(img, app, mode, self.known_apps + hints,
                              llm.model, float(llm.timeout_s), llm.provider)
        except Exception:
            LOG.exception('Writing transcription failed')
            text = 'UNREADABLE'
        with self.lock:
            if token != self.generation or self.phase != 'transcribing':
                return
            if not text or text == 'UNREADABLE':
                self.strokes, self.open_strokes = {}, set()
                self.error_at = time.monotonic()
                self._publish('error', 'Could not read. Try again.', self.anchor)
            else:
                self.text = text
                self._publish('preview', text, self.anchor)
