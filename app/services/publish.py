from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InputSticker
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Pack, PackSet, Sticker
from app.services.errors import StickerBotError
from app.services.short_names import build_short_name, generate_base, segment_title
from app.services.telegram import call_with_retry

MAX_STICKERS_PER_SET = 120
MAX_INITIAL_STICKERS = 50


@dataclass(slots=True)
class PublishResult:
    published: int
    links: list[str]
    set_count: int


def _input_sticker(sticker: Sticker) -> InputSticker:
    return InputSticker(
        sticker=sticker.file_id,
        format=sticker.format,
        emoji_list=sticker.emoji_list,
    )


def _link(short_name: str) -> str:
    return f"https://t.me/addstickers/{short_name}"


async def _new_base(session: AsyncSession, pack: Pack) -> str:
    for _ in range(8):
        candidate = generate_base(pack.title)
        exists = await session.scalar(
            select(Pack.id).where(Pack.short_base == candidate, Pack.id != pack.id)
        )
        if not exists:
            return candidate
    raise StickerBotError("Could not generate a unique sticker set name")


async def _create_set(
    bot: Bot,
    session: AsyncSession,
    pack: Pack,
    existing_sets: list[PackSet],
    pending: list[Sticker],
) -> tuple[PackSet, int]:
    segment = max((item.set_index for item in existing_sets), default=0) + 1
    initial_stickers = pending[:MAX_INITIAL_STICKERS]
    title = segment_title(pack.title, segment)
    set_name = ""

    for _ in range(6):
        set_name = build_short_name(pack.short_base, segment)
        try:
            created = await call_with_retry(
                partial(
                    bot.create_new_sticker_set,
                    user_id=pack.user_id,
                    name=set_name,
                    title=title,
                    stickers=[_input_sticker(item) for item in initial_stickers],
                )
            )
        except TelegramBadRequest as error:
            message = str(error).lower()
            if "already" in message or "occupied" in message or "taken" in message:
                pack.short_base = await _new_base(session, pack)
                await session.flush()
                continue
            raise
        if not created:
            raise StickerBotError("Telegram refused to create the sticker set")
        break
    else:
        raise StickerBotError("Could not find an available sticker set name")

    pack_set = PackSet(
        pack_id=pack.id,
        set_index=segment,
        short_name=set_name,
        title=title,
        sticker_count=len(initial_stickers),
    )
    session.add(pack_set)
    await session.flush()

    now = datetime.now(UTC)
    for sticker in initial_stickers:
        sticker.status = "published"
        sticker.set_id = pack_set.id
        sticker.published_at = now
    await session.commit()
    return pack_set, len(initial_stickers)


async def _add_to_set(
    bot: Bot,
    session: AsyncSession,
    pack: Pack,
    pack_set: PackSet,
    sticker: Sticker,
) -> None:
    try:
        added = await call_with_retry(
            lambda: bot.add_sticker_to_set(
                user_id=pack.user_id,
                name=pack_set.short_name,
                sticker=_input_sticker(sticker),
            )
        )
    except TelegramBadRequest as error:
        message = str(error).lower()
        if "too many" in message or "limit" in message:
            pack_set.sticker_count = MAX_STICKERS_PER_SET
            await session.commit()
            raise
        raise
    if not added:
        raise StickerBotError("Telegram refused to add the sticker")

    sticker.status = "published"
    sticker.set_id = pack_set.id
    sticker.published_at = datetime.now(UTC)
    pack_set.sticker_count += 1
    await session.commit()


async def publish_pack(bot: Bot, session: AsyncSession, pack_id: int) -> PublishResult:
    pack = await session.get(Pack, pack_id)
    if pack is None:
        raise StickerBotError("Pack not found")

    pending = list(
        await session.scalars(
            select(Sticker)
            .where(Sticker.pack_id == pack.id, Sticker.status == "pending")
            .order_by(Sticker.position, Sticker.id)
        )
    )
    sets = list(
        await session.scalars(
            select(PackSet).where(PackSet.pack_id == pack.id).order_by(PackSet.set_index)
        )
    )
    current_set = sets[-1] if sets and sets[-1].sticker_count < MAX_STICKERS_PER_SET else None
    published = 0

    while pending:
        if current_set is None:
            current_set, consumed = await _create_set(bot, session, pack, sets, pending)
            sets.append(current_set)
            published += consumed
            pending = pending[consumed:]
            continue

        sticker = pending[0]
        try:
            await _add_to_set(bot, session, pack, current_set, sticker)
        except TelegramBadRequest:
            if current_set.sticker_count >= MAX_STICKERS_PER_SET:
                current_set = None
                continue
            raise

        pending.pop(0)
        published += 1
        if current_set.sticker_count >= MAX_STICKERS_PER_SET:
            current_set = None

    final_sets = list(
        await session.scalars(
            select(PackSet).where(PackSet.pack_id == pack.id).order_by(PackSet.set_index)
        )
    )
    return PublishResult(
        published=published,
        links=[_link(item.short_name) for item in final_sets],
        set_count=len(final_sets),
    )
