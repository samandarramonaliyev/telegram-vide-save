"""Admin-only commands: /stats, /broadcast, /update_ytdlp."""
from __future__ import annotations

import asyncio
import logging
import sys
from importlib.metadata import version as pkg_version

from aiogram import Bot, Router
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import BaseFilter, Command, CommandObject
from aiogram.types import Message

from bot.context import AppContext
from bot.db.repo import all_active_user_ids, mark_blocked, stats

log = logging.getLogger(__name__)
router = Router(name="admin")

_background: set[asyncio.Task[None]] = set()


class IsAdmin(BaseFilter):
    async def __call__(self, message: Message, ctx: AppContext) -> bool:
        return message.from_user is not None and ctx.settings.is_admin(message.from_user.id)


router.message.filter(IsAdmin())


def ytdlp_version() -> str:
    try:
        return pkg_version("yt-dlp")
    except Exception:  # noqa: BLE001
        return "?"


@router.message(Command("stats"))
async def cmd_stats(message: Message, ctx: AppContext, lang: str) -> None:
    async with ctx.db.session() as s:
        data = await stats(s)
    await message.answer(ctx.i18n(lang, "admin_stats", ytdlp=ytdlp_version(), **data))


@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, command: CommandObject, bot: Bot, ctx: AppContext, lang: str) -> None:
    text = (command.args or "").strip()
    if not text:
        await message.answer(ctx.i18n(lang, "admin_broadcast_usage"))
        return
    async with ctx.db.session() as s:
        ids = await all_active_user_ids(s)
    await message.answer(ctx.i18n(lang, "admin_broadcast_started", count=len(ids)))
    task = asyncio.create_task(_broadcast(bot, ctx, lang, message.chat.id, ids, text))
    _background.add(task)
    task.add_done_callback(_background.discard)


async def _broadcast(bot: Bot, ctx: AppContext, lang: str, report_chat: int, ids: list[int], text: str) -> None:
    sent = blocked = failed = 0
    for uid in ids:
        try:
            await bot.send_message(uid, text)
            sent += 1
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
            try:
                await bot.send_message(uid, text)
                sent += 1
            except Exception:  # noqa: BLE001
                failed += 1
        except TelegramForbiddenError:
            blocked += 1
            async with ctx.db.session() as s:
                await mark_blocked(s, uid)
        except Exception as e:  # noqa: BLE001
            failed += 1
            log.debug("broadcast to %s failed: %s", uid, e)
        await asyncio.sleep(0.05)  # ~20 msg/s, under Telegram's global limit
    await bot.send_message(report_chat, ctx.i18n(lang, "admin_broadcast_done", sent=sent, blocked=blocked, failed=failed))


@router.message(Command("update_ytdlp"))
async def cmd_update_ytdlp(message: Message, ctx: AppContext, lang: str) -> None:
    old = ytdlp_version()
    status = await message.answer(ctx.i18n(lang, "admin_ytdlp_updating"))
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "pip", "install", "--upgrade", "--quiet", "yt-dlp",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    if proc.returncode != 0:
        await status.edit_text(ctx.i18n(lang, "admin_ytdlp_failed", err=out.decode(errors="replace")[-1500:]))
        return
    proc2 = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "pip", "show", "yt-dlp", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
    )
    out2, _ = await proc2.communicate()
    new = next((ln.split(":", 1)[1].strip() for ln in out2.decode(errors="replace").splitlines() if ln.startswith("Version:")), "?")
    await status.edit_text(ctx.i18n(lang, "admin_ytdlp_done", old=old, new=new))
