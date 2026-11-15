"""Optional, nonblocking Supermemory hints for air writing."""
import logging
import os
import queue
import threading
from collections import deque

LOG = logging.getLogger(__name__)


class WritingMemory:
    def __init__(self, config, on_saved=None, client_factory=None):
        self.config, self.on_saved, self.client_factory = config, on_saved, client_factory
        self._lock = threading.Lock()
        self._cache = deque(maxlen=30)
        self._queue = queue.Queue(maxsize=100)
        self._started = False
        self._logged = False
        self._client = None

    def _warn(self, message):
        with self._lock:
            if self._logged:
                return
            self._logged = True
        LOG.warning('Writing memory unavailable: %s', message)

    def _add_cache(self, text):
        text = str(text).strip()
        if not text:
            return
        with self._lock:
            try: self._cache.remove(text)
            except ValueError: pass
            self._cache.appendleft(text)

    def hints(self):
        with self._lock:
            return list(self._cache)

    def start(self):
        if not self._started:
            self._started = True
            threading.Thread(target=self._worker, name='zephyr-memory', daemon=True).start()
        return self

    def remember(self, text, app_label, mode):
        self._add_cache(text)
        try:
            self._queue.put_nowait((str(text), str(app_label), str(mode)))
        except queue.Full:
            self._warn('save queue is full')

    def _worker(self):
        section = self.config.get('memory')
        if not section or not section.get('enabled', False):
            return
        namespace = section.get('user', '')
        if not namespace:
            self._warn('memory.user is missing')
            return
        if not os.environ.get('SUPERMEMORY_API_KEY') and self.client_factory is None:
            self._warn('SUPERMEMORY_API_KEY is missing')
            return
        try:
            if self.client_factory:
                self._client = self.client_factory()
            else:
                from supermemory import Supermemory
                self._client = Supermemory(timeout=2.0, max_retries=0)
            page = self._client.list(namespace, 'documents', limit=30, include={'content': True}, timeout=2.0)
            docs = getattr(page, 'documents', None) or []
            for doc in reversed(docs):
                metadata = getattr(doc, 'metadata', None) or {}
                if metadata.get('source') != 'zephyr_air_writing':
                    continue
                content = getattr(doc, 'content', None)
                if isinstance(content, str):
                    self._add_cache(content)
        except Exception as exc:
            self._warn(type(exc).__name__)
        while True:
            text, app, mode = self._queue.get()
            try:
                if self._client is not None:
                    self._client.add(namespace, content=text,
                                     metadata={'source': 'zephyr_air_writing', 'app': app, 'mode': mode},
                                     timeout=2.0)
                    if self.on_saved:
                        self.on_saved()
            except Exception as exc:
                self._warn(type(exc).__name__)
            finally:
                self._queue.task_done()
