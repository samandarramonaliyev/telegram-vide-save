"""Optional force-subscribe gate (FORCE_SUB_CHANNEL in .env)."""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import CallbackQuery, Message, TelegramObject

from bot.config import Settings
from bot.keyboards import force_sub_keyboard
from bot.utils.i18n import I18n

log = logging.getLogger(__name__)

ALLOWED = {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR}


async def is_subscribed(bot: Bot, channel: str, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=channel, user_id=user_id)
    except (TelegramBadRequest, TelegramForbiddenError) as e:
        # Bot is not admin in the channel / channel misconfigured -> do not lock users out.
        log.error("force-sub check failed for %s: %s", channel, e)
        return True
    return member.status in ALLOWED


def channel_url(settings: Settings) -> str:
    if settings.force_sub_channel_url:
        return settings.force_sub_channel_url
    ch = settings.force_sub_channel or ""
    return f"https://t.me/{ch.lstrip('@')}" if ch.startswith("@") else "https://t.me/"


class ForceSubMiddleware(BaseMiddleware):
    def __init__(self, settings: Settings, i18n: I18n) -> None:
        self.settings = settings
        self.i18n = i18n

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        channel = self.settings.force_sub_channel
        if not channel or not isinstance(event, (Message, CallbackQuery)) or event.from_user is None:
            return await handler(event, data)
        if event.from_user.is_bot or self.settings.is_admin(event.from_user.id):
            return await handler(event, data)
        # let /start, language selection and the "check" button through
        if isinstance(event, Message) and event.text and event.text.startswith("/start"):
            return await handler(event, data)
        if isinstance(event, CallbackQuery) and event.data and (event.data == "chk" or event.data.startswith("lang:")):
            return await handler(event, data)

        bot: Bot = data["bot"]
        if await is_subscribed(bot, channel, event.from_user.id):
            return await handler(event, data)

        lang = data.get("lang")
        kb = force_sub_keyboard(self.i18n, lang, channel_url(self.settings))
        text = self.i18n(lang, "forcesub_text")
        if isinstance(event, Message):
            await event.answer(text, reply_markup=kb)
        else:
            await event.answer()
            if event.message is not None:
                await event.message.answer(text, reply_markup=kb)
        return None
