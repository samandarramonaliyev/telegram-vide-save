"""Entry point: python -m bot.main"""
from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.types import BotCommand

from bot.config import Settings, get_settings
from bot.context import AppContext
from bot.db.session import Database
from bot.handlers import build_router
from bot.middlewares import ForceSubMiddleware, UserMiddleware
from bot.services import ffmpeg as ff
from bot.services.cache import FileIdCache
from bot.services.cleanup import cleanup_loop
from bot.services.downloader import Downloader
from bot.utils.i18n import I18n
from bot.utils.linkstore import LinkStore
from bot.utils.ratelimit import RateLimiter

log = logging.getLogger("bot")


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


def build_bot(settings: Settings) -> Bot:
    session = None
    if settings.use_local_bot_api:
        session = AiohttpSession(api=TelegramAPIServer.from_base(settings.local_bot_api_url, is_local=True))
        log.info("using local Bot API server at %s (limit %d MB)", settings.local_bot_api_url, settings.max_file_size_mb)
    return Bot(
        token=settings.bot_token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
    )


async def set_commands(bot: Bot) -> None:
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Boshlash / Start"),
            BotCommand(command="help", description="Yordam / Help"),
            BotCommand(command="lang", description="Tilni o'zgartirish / Language"),
        ]
    )


async def run() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)

    if not ff.ffmpeg_available():
        log.warning("ffmpeg/ffprobe not found in PATH: MP3 extraction, merging and compression will fail")

    db = Database(settings.database_url)
    await db.init()

    i18n = I18n(settings.default_language)
    semaphore = asyncio.Semaphore(settings.max_concurrent_downloads)
    ctx = AppContext(
        settings=settings,
        i18n=i18n,
        db=db,
        downloader=Downloader(settings, semaphore),
        cache=FileIdCache(db),
        link_store=LinkStore(ttl_seconds=3600),
        rate_limiter=RateLimiter(settings.rate_limit_per_minute),
        search_limiter=RateLimiter(settings.rate_limit_per_minute * 2),
    )

    bot = build_bot(settings)
    dp = Dispatcher()
    dp["ctx"] = ctx
    # order matters: user/lang first, then the force-subscribe gate
    user_mw = UserMiddleware(db, settings)
    sub_mw = ForceSubMiddleware(settings, i18n)
    for observer in (dp.message, dp.callback_query):
        observer.outer_middleware(user_mw)
        observer.outer_middleware(sub_mw)
    dp.include_router(build_router())

    cleanup_task = asyncio.create_task(
        cleanup_loop(
            settings.download_dir,
            settings.temp_file_ttl_minutes,
            settings.cleanup_interval_minutes,
            ctx.link_store,
            ctx.rate_limiter,
        )
    )

    try:
        try:
            me = await bot.me()
        except TelegramUnauthorizedError:
            log.error("BOT_TOKEN was rejected by Telegram (Unauthorized). Check the token in .env against @BotFather.")
            return
        log.info("starting @%s (admins=%s, limit=%d MB, concurrency=%d)", me.username, settings.admin_ids, settings.max_file_size_mb, settings.max_concurrent_downloads)
        await set_commands(bot)
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        cleanup_task.cancel()
        await db.close()
        await bot.session.close()


def main() -> None:
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, SystemExit):
        log.info("stopped")


if __name__ == "__main__":
    main()
