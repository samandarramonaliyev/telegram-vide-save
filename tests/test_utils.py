import time

from bot.utils.linkstore import LinkStore
from bot.utils.ratelimit import RateLimiter
from bot.utils.text import clip, fmt_duration, fmt_size


def test_rate_limiter_window() -> None:
    rl = RateLimiter(max_per_minute=2, window_seconds=0.2)
    assert rl.check(1) and rl.check(1)
    assert not rl.check(1)
    assert rl.retry_after(1) >= 1
    assert rl.check(2), "other users are independent"
    time.sleep(0.25)
    assert rl.check(1)


def test_rate_limiter_disabled() -> None:
    rl = RateLimiter(max_per_minute=0)
    assert all(rl.check(1) for _ in range(100))


def test_link_store_roundtrip_and_expiry() -> None:
    store = LinkStore(ttl_seconds=3600)
    token = store.put({"url": "x"})
    assert len(token) <= 12
    assert store.get(token) == {"url": "x"}
    assert store.get("nope") is None
    store._items[token] = (time.monotonic() - 1, {"url": "x"})
    assert store.get(token) is None


def test_text_helpers() -> None:
    assert fmt_duration(65) == "1:05"
    assert fmt_duration(3661) == "1:01:01"
    assert fmt_duration(None) == "--:--"
    assert fmt_size(50 * 1024 * 1024) == "50.0 MB"
    assert clip("abcdef", 4) == "abc…"
    assert clip(None, 4) == ""
