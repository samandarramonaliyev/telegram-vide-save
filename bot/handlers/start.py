"""/start, /help, /lang and language selection."""
from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from bot.context import AppContext
from bot.db.models import User
from bot.db.repo import set_language
from bot.keyboards import language_keyboard
from bot.middlewares.forcesub import is_subscribed
from bot.utils.i18n import LANG_NAMES, SUPPORTED
from bot.utils.text import h

router = Router(name="start")


async def send_welcome(message: Message, ctx: AppContext, lang: str) -> None:
    name = message.from_user.first_name if message.from_user else ""
    await message.answer(ctx.i18n(lang, "welcome", name=h(name)))


@router.message(CommandStart())
async def cmd_start(message: Message, ctx: AppContext, lang: str, lang_is_set: bool) -> None:
    if not lang_is_set:
        await message.answer(ctx.i18n(lang, "choose_lang"), reply_markup=language_keyboard())
        return
    await send_welcome(message, ctx, lang)


@router.message(Command("lang", "language"))
async def cmd_lang(message: Message, ctx: AppContext, lang: str) -> None:
    await message.answer(ctx.i18n(lang, "choose_lang"), reply_markup=language_keyboard())


@router.message(Command("help"))
async def cmd_help(message: Message, ctx: AppContext, lang: str) -> None:
    await message.answer(ctx.i18n(lang, "help", limit=ctx.settings.max_file_size_mb))


@router.callback_query(F.data == "chk")
async def on_check_subscription(cb: CallbackQuery, bot: Bot, ctx: AppContext, lang: str) -> None:
    channel = ctx.settings.force_sub_channel
    if not channel or cb.from_user is None:
        await cb.answer()
        return
    if await is_subscribed(bot, channel, cb.from_user.id):
        await cb.answer()
        if isinstance(cb.message, Message):
            try:
                await cb.message.edit_text(ctx.i18n(lang, "forcesub_ok"))
            except TelegramBadRequest:
                pass
    else:
        await cb.answer(ctx.i18n(lang, "forcesub_not_yet"), show_alert=True)


@router.callback_query(F.data.startswith("lang:"))
async def on_language(cb: CallbackQuery, ctx: AppContext, db_user: User) -> None:
    code = (cb.data or "").split(":", 1)[1]
    if code not in SUPPORTED:
        await cb.answer()
        return
    async with ctx.db.session() as s:
        await set_language(s, db_user.telegram_id, code)
    await cb.answer()
    if isinstance(cb.message, Message):
        try:
            await cb.message.edit_text(ctx.i18n(code, "lang_set", lang=LANG_NAMES[code]))
        except TelegramBadRequest:
            pass
        await send_welcome(cb.message, ctx, code)
