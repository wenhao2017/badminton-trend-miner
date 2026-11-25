import time
from threading import Lock

from app.config import settings
from app.utils.security import hash_api_key


class SportradarRateLimiter:
    _lock = Lock()
    _next_request = {}

    def acquire(self, api_key: str) -> None:
        if not api_key:
            return
        key = hash_api_key(api_key)
        interval = 1 / max(1, settings.SPORTRADAR_KEY_QPS)
        with self._lock:
            now = time.monotonic()
            reserved = max(now, self._next_request.get(key, now))
            self._next_request[key] = reserved + interval
        if reserved > now:
            time.sleep(reserved - now)

