from aiogram import Router

from bot.handlers import admin, download, music, start


def build_router() -> Router:
    """Order matters: commands first, link detection before music search."""
    root = Router(name="root")
    root.include_routers(start.router, admin.router, download.router, music.router)
    return root
