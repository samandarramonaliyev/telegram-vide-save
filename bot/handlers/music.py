"""Music search: plain text -> ytsearch5 -> inline buttons -> MP3."""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message

from bot.context import AppContext
from bot.db.models import User
from bot.handlers.download import process_download
from bot.keyboards import search_results_keyboard
from bot.services.downloader import MediaDownloadError, SearchResult
from bot.utils.regex import DetectedLink, Platform, normalize_url
from bot.utils.text import clip, h

log = logging.getLogger(__name__)
router = Router(name="music")

MIN_QUERY = 3
MAX_QUERY = 100


@router.message(F.text, ~F.text.startswith("/"))
async def on_search(message: Message, ctx: AppContext, lang: str, db_user: User) -> None:
    query = " ".join((message.text or "").split())
    if len(query) < MIN_QUERY:
        await message.answer(ctx.i18n(lang, "search_too_short"))
        return
    query = clip(query, MAX_QUERY)

    if not ctx.search_limiter.check(db_user.telegram_id):
        await message.answer(ctx.i18n(lang, "rate_limited", seconds=ctx.search_limiter.retry_after(db_user.telegram_id)))
        return

    status = await message.answer(ctx.i18n(lang, "searching", query=h(query)))
    try:
        results = await ctx.downloader.search(query, limit=5)
    except MediaDownloadError as e:
        log.info("search failed for %r: %s", query, e.detail[:200])
        results = []
    except Exception:  # noqa: BLE001
        log.exception("search crashed for %r", query)
        results = []

    if not results:
        await _edit(status, ctx.i18n(lang, "search_no_results"))
        return

    token = ctx.link_store.put(results)
    await _edit(status, ctx.i18n(lang, "search_results", query=h(query)), search_results_keyboard(results, token))


@router.callback_query(F.data.startswith("ms:"))
async def on_pick(cb: CallbackQuery, bot: Bot, ctx: AppContext, lang: str, db_user: User) -> None:
    try:
        _, idx_s, token = (cb.data or "").split(":", 2)
        idx = int(idx_s)
    except ValueError:
        await cb.answer()
        return
    results = ctx.link_store.get(token)
    if not isinstance(results, list) or not (0 <= idx < len(results)) or not isinstance(cb.message, Message):
        await cb.answer(ctx.i18n(lang, "link_expired"), show_alert=True)
        return
    r: SearchResult = results[idx]
    await cb.answer()
    url = normalize_url(r.url, Platform.YOUTUBE) or r.url
    link = DetectedLink(platform=Platform.YOUTUBE, url=url, original=r.url)
    await process_download(bot, cb.message, ctx, lang, db_user, link, "audio", None)


async def _edit(message: Message, text: str, markup=None) -> None:  # type: ignore[no-untyped-def]
    try:
        await message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:
        pass
