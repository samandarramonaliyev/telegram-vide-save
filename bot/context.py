"""Shared application services injected into handlers as `ctx`."""
from __future__ import annotations

from dataclasses import dataclass

from bot.config import Settings
from bot.db.session import Database
from bot.services.cache import FileIdCache
from bot.services.downloader import Downloader
from bot.utils.i18n import I18n
from bot.utils.linkstore import LinkStore
from bot.utils.ratelimit import RateLimiter


@dataclass(slots=True)
class AppContext:
    settings: Settings
    i18n: I18n
    db: Database
    downloader: Downloader
    cache: FileIdCache
    link_store: LinkStore
    rate_limiter: RateLimiter
    search_limiter: RateLimiter
