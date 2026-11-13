"""Telegram file_id cache: (normalized url, fmt) -> file_id."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from bot.db.models import FileCache
from bot.db.session import Database

log = logging.getLogger(__name__)


def cache_fmt(kind: str, quality: int | None = None) -> str:
    """'audio' or 'video:<height>' (video:best when no quality requested)."""
    if kind == "audio":
        return "audio"
    return f"video:{quality or 'best'}"


@dataclass(slots=True)
class CachedFile:
    file_id: str
    file_type: str
    title: str | None
    file_size: int | None


class FileIdCache:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def get(self, url: str, fmt: str) -> CachedFile | None:
        async with self.db.session() as s:
            row = await s.scalar(select(FileCache).where(FileCache.url == url, FileCache.fmt == fmt))
            if row is None:
                return None
            await s.execute(update(FileCache).where(FileCache.id == row.id).values(hits=FileCache.hits + 1))
            return CachedFile(row.file_id, row.file_type, row.title, row.file_size)

    async def set(
        self,
        url: str,
        fmt: str,
        file_id: str,
        file_type: str,
        title: str | None = None,
        file_size: int | None = None,
    ) -> None:
        async with self.db.session() as s:
            stmt = sqlite_insert(FileCache).values(
                url=url, fmt=fmt, file_id=file_id, file_type=file_type, title=title, file_size=file_size
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[FileCache.url, FileCache.fmt],
                set_={"file_id": file_id, "title": title, "file_size": file_size},
            )
            await s.execute(stmt)
        log.debug("cached %s [%s]", url, fmt)

    async def delete(self, url: str, fmt: str) -> None:
        async with self.db.session() as s:
            row = await s.scalar(select(FileCache).where(FileCache.url == url, FileCache.fmt == fmt))
            if row:
                await s.delete(row)
