"""Per-user sliding-window rate limiter (in-memory)."""
from __future__ import annotations

import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self, max_per_minute: int, window_seconds: float = 60.0) -> None:
        self.max = max_per_minute
        self.window = window_seconds
        self._hits: dict[int, deque[float]] = defaultdict(deque)

    def _prune(self, q: deque[float], now: float) -> None:
        while q and now - q[0] >= self.window:
            q.popleft()

    def check(self, user_id: int) -> bool:
        """Return True and record a hit if allowed, else False."""
        if self.max <= 0:
            return True
        now = time.monotonic()
        q = self._hits[user_id]
        self._prune(q, now)
        if len(q) >= self.max:
            return False
        q.append(now)
        return True

    def retry_after(self, user_id: int) -> int:
        """Seconds until the oldest hit leaves the window (0 if allowed now)."""
        q = self._hits.get(user_id)
        if not q or len(q) < self.max:
            return 0
        return max(0, int(self.window - (time.monotonic() - q[0])) + 1)

    def purge(self) -> None:
        now = time.monotonic()
        for uid in list(self._hits):
            self._prune(self._hits[uid], now)
            if not self._hits[uid]:
                del self._hits[uid]
