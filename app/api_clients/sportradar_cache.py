from collections import OrderedDict
from threading import RLock
from time import monotonic

from app.config import settings


class SportradarCache:
    """Bounded hot cache. Raw responses are also cached durably in SQLite."""

    _entries = OrderedDict()
    _lock = RLock()
    _max_entries = 64
    _max_payload_chars = 256_000

    def get(self, endpoint, params_hash):
        if not settings.SPORTRADAR_ENABLE_CACHE:
            return None
        key = endpoint, params_hash
        with self._lock:
            entry = self._entries.pop(key, None)
            if entry and entry[0] > monotonic():
                self._entries[key] = entry
                return entry[1]
        return None

    def set(self, endpoint, params_hash, response_text, ttl_seconds):
        if not settings.SPORTRADAR_ENABLE_CACHE or len(response_text) > self._max_payload_chars:
            return
        key = endpoint, params_hash
        with self._lock:
            self._entries[key] = monotonic() + ttl_seconds, response_text
            self._entries.move_to_end(key)
            if len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def clear(self):
        with self._lock:
            count = len(self._entries)
            self._entries.clear()
            return count

