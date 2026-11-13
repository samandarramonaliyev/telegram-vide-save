"""Async engine / session factory."""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from bot.db.models import Base

log = logging.getLogger(__name__)


class Database:
    def __init__(self, url: str) -> None:
        self._url = url
        self.engine: AsyncEngine = create_async_engine(url, echo=False, pool_pre_ping=True)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def init(self) -> None:
        if self._url.startswith("sqlite"):
            path = self._url.split("///", 1)[-1]
            if path and path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            if self._url.startswith("sqlite"):
                await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        log.info("database ready: %s", self._url)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    async def close(self) -> None:
        await self.engine.dispose()
