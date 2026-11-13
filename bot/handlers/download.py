"""Link detection, format keyboard and the download/send pipeline."""
from __future__ import annotations

import logging
from typing import Literal

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.types import CallbackQuery, FSInputFile, Message
from aiogram.utils.chat_action import ChatActionSender

from bot.context import AppContext
from bot.db.models import User
from bot.db.repo import log_download
from bot.keyboards import format_keyboard, too_large_keyboard
from bot.services.cache import cache_fmt
from bot.services.downloader import DownloadResult, ErrorKind, MediaDownloadError, ProgressEvent
from bot.utils.regex import DetectedLink, Platform, detect_link, looks_like_url
from bot.utils.text import clip, fmt_size, h

log = logging.getLogger(__name__)
router = Router(name="download")

PLATFORM_NAMES = {Platform.YOUTUBE: "YouTube", Platform.INSTAGRAM: "Instagram", Platform.TIKTOK: "TikTok"}
UPLOAD_TIMEOUT = 900  # seconds; big uploads over slow links

Kind = Literal["video", "audio"]


# ----------------------------------------------------------------------------- link message
@router.message(F.text, F.text.func(looks_like_url))
async def on_link(message: Message, ctx: AppContext, lang: str) -> None:
    link = detect_link(message.text or "")
    if link is None:
        await message.answer(ctx.i18n(lang, "unsupported_link"))
        return
    token = ctx.link_store.put(link)
    await message.answer(
        ctx.i18n(lang, "choose_format", platform=PLATFORM_NAMES[link.platform]),
        reply_markup=format_keyboard(ctx.i18n, lang, link.platform, token),
    )


# ----------------------------------------------------------------------------- format chosen
@router.callback_query(F.data.startswith("dl:"))
async def on_format_chosen(cb: CallbackQuery, bot: Bot, ctx: AppContext, lang: str, db_user: User) -> None:
    try:
        _, kind_code, quality_s, token = (cb.data or "").split(":", 3)
    except ValueError:
        await cb.answer()
        return
    link = ctx.link_store.get(token)
    if not isinstance(link, DetectedLink) or not isinstance(cb.message, Message):
        await cb.answer(ctx.i18n(lang, "link_expired"), show_alert=True)
        return
    await cb.answer()
    kind: Kind = "audio" if kind_code == "a" else "video"
    quality = int(quality_s) or None
    await process_download(bot, cb.message, ctx, lang, db_user, link, kind, quality)


# ----------------------------------------------------------------------------- pipeline
async def process_download(
    bot: Bot,
    message: Message,
    ctx: AppContext,
    lang: str,
    db_user: User,
    link: DetectedLink,
    kind: Kind,
    quality: int | None,
) -> None:
    """Rate-limit -> cache -> download -> (compress) -> send -> cache -> cleanup."""
    chat_id = message.chat.id
    t = ctx.i18n
    if link.platform is not Platform.YOUTUBE:
        quality = None
    fmt = cache_fmt(kind, quality)

    if not ctx.rate_limiter.check(db_user.telegram_id):
        await bot.send_message(chat_id, t(lang, "rate_limited", seconds=ctx.rate_limiter.retry_after(db_user.telegram_id)))
        return

    me = await bot.me()
    bot_username = me.username or ""

    # 1) cache
    cached = await ctx.cache.get(link.url, fmt)
    if cached:
        try:
            caption = t(lang, "caption", title=h(clip(cached.title, 200)), bot=bot_username)
            if cached.file_type == "video":
                await bot.send_video(chat_id, cached.file_id, caption=caption, supports_streaming=True)
            else:
                await bot.send_audio(chat_id, cached.file_id, caption=caption)
            await _log(ctx, db_user, link, fmt, "cached", file_size=cached.file_size)
            return
        except TelegramBadRequest as e:
            log.warning("stale cache entry for %s [%s]: %s", link.url, fmt, e)
            await ctx.cache.delete(link.url, fmt)

    # 2) progress message
    status = await bot.send_message(chat_id, t(lang, "downloading"))
    last_text = {"v": status.text or ""}

    async def set_status(text: str) -> None:
        if text == last_text["v"]:
            return
        last_text["v"] = text
        try:
            await status.edit_text(text)
        except TelegramBadRequest:
            pass
        except TelegramNetworkError as e:
            log.debug("status edit failed: %s", e)

    async def on_progress(ev: ProgressEvent) -> None:
        if ev.stage == "downloading":
            await set_status(t(lang, "downloading_pct", pct=ev.percent) if ev.percent is not None else t(lang, "downloading"))
        elif ev.stage == "processing":
            await set_status(t(lang, "processing"))

    result: DownloadResult | None = None
    try:
        # 3) download (runs in thread; semaphore limits concurrency)
        if kind == "video":
            result = await ctx.downloader.download_video(link.url, quality, on_progress)
        else:
            result = await ctx.downloader.download_audio(link.url, on_progress)

        # 4) send
        await set_status(t(lang, "uploading"))
        caption = t(lang, "caption", title=h(clip(result.title, 200)), bot=bot_username)
        if result.compressed:
            caption += t(lang, "compressed_note")
        thumb = FSInputFile(result.thumbnail) if result.thumbnail else None
        if kind == "video":
            async with ChatActionSender.upload_video(bot=bot, chat_id=chat_id):
                sent = await bot.send_video(
                    chat_id,
                    FSInputFile(result.path),
                    caption=caption,
                    duration=int(result.duration or 0) or None,
                    width=result.width,
                    height=result.height,
                    thumbnail=thumb,
                    supports_streaming=True,
                    request_timeout=UPLOAD_TIMEOUT,
                )
            file_id = sent.video.file_id if sent.video else (sent.document.file_id if sent.document else None)
        else:
            async with ChatActionSender.upload_document(bot=bot, chat_id=chat_id):
                sent = await bot.send_audio(
                    chat_id,
                    FSInputFile(result.path),
                    caption=caption,
                    title=clip(result.title, 64),
                    performer=clip(result.performer, 64) or None,
                    duration=int(result.duration or 0) or None,
                    thumbnail=thumb,
                    request_timeout=UPLOAD_TIMEOUT,
                )
            file_id = sent.audio.file_id if sent.audio else (sent.document.file_id if sent.document else None)

        # 5) cache + stats
        if file_id:
            await ctx.cache.set(link.url, fmt, file_id, kind, title=result.title, file_size=result.size)
        await _log(ctx, db_user, link, fmt, "ok", file_size=result.size)
        await _delete_quietly(status)

    except MediaDownloadError as e:
        await _log(ctx, db_user, link, fmt, "error", error_kind=str(e.kind))
        if e.kind is ErrorKind.TOO_LARGE:
            size = _size_from_detail(e.detail)
            token = ctx.link_store.put(link)
            await set_status(t(lang, "too_large", size=size, limit=ctx.settings.max_file_size_mb))
            try:
                await status.edit_reply_markup(reply_markup=too_large_keyboard(ctx.i18n, lang, link.platform, token, quality))
            except TelegramBadRequest:
                pass
        elif e.kind is ErrorKind.TOO_LONG:
            await set_status(t(lang, "too_long", max=ctx.settings.max_duration_minutes))
        else:
            await set_status(t(lang, f"err_{e.kind}"))
        log.info("download failed [%s] user=%s url=%s: %s", e.kind, db_user.telegram_id, link.url, clip(e.detail, 300))
    except Exception:  # noqa: BLE001
        log.exception("unexpected error user=%s url=%s fmt=%s", db_user.telegram_id, link.url, fmt)
        await _log(ctx, db_user, link, fmt, "error", error_kind="exception")
        await set_status(t(lang, "err_unknown"))
    finally:
        if result is not None:
            result.cleanup()


async def _log(ctx: AppContext, db_user: User, link: DetectedLink, fmt: str, status: str, *, error_kind: str | None = None, file_size: int | None = None) -> None:
    try:
        async with ctx.db.session() as s:
            await log_download(
                s,
                user_id=db_user.id,
                url=link.url,
                platform=str(link.platform),
                fmt=fmt,
                status=status,
                error_kind=error_kind,
                file_size=file_size,
            )
    except Exception:  # noqa: BLE001
        log.exception("failed to write download log")


async def _delete_quietly(message: Message) -> None:
    try:
        await message.delete()
    except TelegramBadRequest:
        pass


def _size_from_detail(detail: str) -> str:
    import re

    m = re.search(r"(\d{6,})\s*bytes", detail or "")
    return fmt_size(int(m.group(1))) if m else "> limit"
