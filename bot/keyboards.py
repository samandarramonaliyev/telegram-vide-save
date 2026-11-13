"""Inline keyboard builders."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.services.downloader import QUALITIES, SearchResult
from bot.utils.i18n import LANG_NAMES, I18n
from bot.utils.regex import Platform
from bot.utils.text import clip, fmt_duration


def language_keyboard() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for code, name in LANG_NAMES.items():
        b.button(text=name, callback_data=f"lang:{code}")
    b.adjust(3)
    return b.as_markup()


def format_keyboard(i18n: I18n, lang: str | None, platform: Platform, token: str) -> InlineKeyboardMarkup:
    """[🎬 Video] [🎵 MP3]; for YouTube the video row becomes 360p / 720p / 1080p."""
    b = InlineKeyboardBuilder()
    if platform is Platform.YOUTUBE:
        for q in QUALITIES:
            b.button(text=f"🎬 {q}p", callback_data=f"dl:v:{q}:{token}")
        b.button(text=i18n(lang, "btn_audio"), callback_data=f"dl:a:0:{token}")
        b.adjust(3, 1)
    else:
        b.button(text=i18n(lang, "btn_video"), callback_data=f"dl:v:0:{token}")
        b.button(text=i18n(lang, "btn_audio"), callback_data=f"dl:a:0:{token}")
        b.adjust(2)
    return b.as_markup()


def too_large_keyboard(i18n: I18n, lang: str | None, platform: Platform, token: str, failed_quality: int | None) -> InlineKeyboardMarkup:
    """Offered after a TOO_LARGE error: lower qualities (YouTube) + MP3."""
    b = InlineKeyboardBuilder()
    if platform is Platform.YOUTUBE:
        lower = [q for q in QUALITIES if failed_quality is None or q < failed_quality]
        for q in lower:
            b.button(text=f"🎬 {q}p", callback_data=f"dl:v:{q}:{token}")
    b.button(text=i18n(lang, "btn_audio"), callback_data=f"dl:a:0:{token}")
    b.adjust(3, 1)
    return b.as_markup()


def search_results_keyboard(results: list[SearchResult], token: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for idx, r in enumerate(results):
        label = f"{idx + 1}. {clip(r.title, 40)}  ⏱ {fmt_duration(r.duration)}"
        b.button(text=label, callback_data=f"ms:{idx}:{token}")
    b.adjust(1)
    return b.as_markup()


def force_sub_keyboard(i18n: I18n, lang: str | None, channel_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=i18n(lang, "forcesub_btn_join"), url=channel_url)],
            [InlineKeyboardButton(text=i18n(lang, "forcesub_btn_check"), callback_data="chk")],
        ]
    )
