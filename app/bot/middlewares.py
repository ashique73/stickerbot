from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from app.db import SessionLocal
from app.services.users import get_or_create_user


class SessionMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with SessionLocal() as session:
            data["session"] = session
            telegram_user = data.get("event_from_user")
            db_user = None
            if telegram_user is not None and not telegram_user.is_bot:
                db_user = await get_or_create_user(session, telegram_user)
            data["db_user"] = db_user
            try:
                result = await handler(event, data)
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                raise
