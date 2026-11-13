"""Repository helpers for users and download statistics."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Download, FileCache, User


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _today_start() -> datetime:
    return _utcnow().replace(hour=0, minute=0, second=0, microsecond=0)


async def upsert_user(
    session: AsyncSession,
    telegram_id: int,
    username: str | None,
    first_name: str | None,
) -> User:
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    if user is None:
        user = User(telegram_id=telegram_id, username=username, first_name=first_name)
        session.add(user)
        await session.flush()
    else:
        user.username = username
        user.first_name = first_name
        user.last_seen_at = _utcnow()
        if user.is_blocked:
            user.is_blocked = False
    return user


async def set_language(session: AsyncSession, telegram_id: int, lang: str) -> None:
    await session.execute(update(User).where(User.telegram_id == telegram_id).values(language=lang))


async def mark_blocked(session: AsyncSession, telegram_id: int) -> None:
    await session.execute(update(User).where(User.telegram_id == telegram_id).values(is_blocked=True))


async def all_active_user_ids(session: AsyncSession) -> list[int]:
    rows = await session.scalars(select(User.telegram_id).where(User.is_blocked.is_(False)))
    return list(rows)


async def log_download(
    session: AsyncSession,
    *,
    user_id: int,
    url: str,
    platform: str,
    fmt: str,
    status: str,
    error_kind: str | None = None,
    file_size: int | None = None,
) -> None:
    session.add(
        Download(
            user_id=user_id,
            url=url,
            platform=platform,
            fmt=fmt,
            status=status,
            error_kind=error_kind,
            file_size=file_size,
        )
    )


async def stats(session: AsyncSession) -> dict[str, int]:
    today = _today_start()
    ok_statuses = ("ok", "cached")

    async def count(stmt) -> int:  # type: ignore[no-untyped-def]
        return int(await session.scalar(stmt) or 0)

    return {
        "users_total": await count(select(func.count(User.id))),
        "users_today": await count(select(func.count(User.id)).where(User.created_at >= today)),
        "downloads_total": await count(
            select(func.count(Download.id)).where(Download.status.in_(ok_statuses))
        ),
        "downloads_today": await count(
            select(func.count(Download.id)).where(
                Download.status.in_(ok_statuses), Download.created_at >= today
            )
        ),
        "errors_today": await count(
            select(func.count(Download.id)).where(
                Download.status == "error", Download.created_at >= today
            )
        ),
        "cache_entries": await count(select(func.count(FileCache.id))),
        "cache_hits": await count(select(func.coalesce(func.sum(FileCache.hits), 0))),
    }
