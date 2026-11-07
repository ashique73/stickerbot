from typing import Any

from aiogram.types import User as TelegramUser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Pack, User


async def get_or_create_user(session: AsyncSession, telegram_user: TelegramUser) -> User:
    user = await session.get(User, telegram_user.id)
    if user is None:
        user = User(
            id=telegram_user.id,
            username=telegram_user.username,
            first_name=telegram_user.first_name,
            state_data={},
        )
        session.add(user)
    else:
        user.username = telegram_user.username
        user.first_name = telegram_user.first_name
    await session.flush()
    return user


async def set_state(
    session: AsyncSession, user: User, state: str, data: dict[str, Any] | None = None
) -> None:
    user.state = state
    user.state_data = data or {}
    await session.flush()


async def clear_state(session: AsyncSession, user: User) -> None:
    user.state = None
    user.state_data = {}
    await session.flush()


async def get_active_pack(session: AsyncSession, user_id: int) -> Pack | None:
    user = await session.get(User, user_id)
    if not user or not user.active_pack_id:
        return None
    return await session.scalar(
        select(Pack).where(Pack.id == user.active_pack_id, Pack.user_id == user_id)
    )
