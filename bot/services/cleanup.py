"""Periodic cleanup of stale temp directories and in-memory stores."""
from __future__ import annotations

import asyncio
import logging
import shutil
import time
from pathlib import Path

from bot.utils.linkstore import LinkStore
from bot.utils.ratelimit import RateLimiter

log = logging.getLogger(__name__)


def remove_stale_dirs(root: Path, ttl_seconds: float) -> int:
    """Delete sub-directories (download jobs) older than ttl. Returns count removed."""
    if not root.exists():
        return 0
    now = time.time()
    removed = 0
    for p in root.iterdir():
        try:
            if now - p.stat().st_mtime > ttl_seconds:
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    p.unlink(missing_ok=True)
                removed += 1
        except OSError as e:  # pragma: no cover
            log.warning("cleanup failed for %s: %s", p, e)
    return removed


async def cleanup_loop(
    download_dir: Path,
    ttl_minutes: int,
    interval_minutes: int,
    link_store: LinkStore,
    rate_limiter: RateLimiter,
) -> None:
    interval = max(60, interval_minutes * 60)
    while True:
        try:
            removed = await asyncio.to_thread(remove_stale_dirs, download_dir, ttl_minutes * 60)
            link_store.purge()
            rate_limiter.purge()
            if removed:
                log.info("cleanup: removed %d stale item(s)", removed)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("cleanup iteration failed")
        await asyncio.sleep(interval)
