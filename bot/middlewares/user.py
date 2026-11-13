"""Upsert the Telegram user into the DB and inject `db_user` / `lang` into handler data."""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from bot.config import Settings
from bot.db.repo import upsert_user
from bot.db.session import Database

log = logging.getLogger(__name__)


class UserMiddleware(BaseMiddleware):
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = None
        if isinstance(event, (Message, CallbackQuery)):
            tg_user = event.from_user
        if tg_user is None or tg_user.is_bot:
            return await handler(event, data)

        async with self.db.session() as s:
            user = await upsert_user(s, tg_user.id, tg_user.username, tg_user.first_name)
            data["db_user"] = user
            data["lang"] = user.language or self.settings.default_language
            data["lang_is_set"] = user.language is not None
        return await handler(event, data)
