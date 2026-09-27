from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from .app import App


class UserMiddleware(BaseMiddleware):
    """Registers/refreshes the sender and hands handlers `me` and `admin`."""

    def __init__(self, app: App):
        self.app = app

    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
                       event: TelegramObject, data: dict[str, Any]) -> Any:
        u = data.get("event_from_user")
        if u is None or u.is_bot:
            return None
        env_admin = u.id in self.app.cfg.admin_ids
        me = await self.app.db.touch_user(u.id, u.username, u.first_name, u.last_name, admin=env_admin)
        self.app.forget_name(me)
        data["me"] = me
        data["admin"] = env_admin or me.is_admin
        return await handler(event, data)
