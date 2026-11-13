"""Short-token store so URLs / search results fit into 64-byte callback_data."""
from __future__ import annotations

import secrets
import time
from typing import Any


class LinkStore:
    def __init__(self, ttl_seconds: int = 3600) -> None:
        self.ttl = ttl_seconds
        self._items: dict[str, tuple[float, Any]] = {}

    def put(self, payload: Any) -> str:
        self.purge()
        token = secrets.token_urlsafe(6)  # 8 chars
        while token in self._items:
            token = secrets.token_urlsafe(6)
        self._items[token] = (time.monotonic() + self.ttl, payload)
        return token

    def get(self, token: str) -> Any | None:
        item = self._items.get(token)
        if not item:
            return None
        expires, payload = item
        if time.monotonic() > expires:
            self._items.pop(token, None)
            return None
        return payload

    def purge(self) -> None:
        now = time.monotonic()
        for k in [k for k, (exp, _) in self._items.items() if exp < now]:
            self._items.pop(k, None)

    def __len__(self) -> int:
        return len(self._items)
