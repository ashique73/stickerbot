from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Pack, PackSet, PublishJob, Sticker, User
from app.services.publish_worker import wake_worker
from app.services.short_names import generate_base


async def create_pack(session: AsyncSession, user: User, title: str) -> Pack:
    base = ""
    for _ in range(8):
        candidate = generate_base(title)
        exists = await session.scalar(select(Pack.id).where(Pack.short_base == candidate))
        if not exists:
            base = candidate
            break
    if not base:
        raise RuntimeError("Could not generate a unique sticker set name")

    pack = Pack(user_id=user.id, title=title, short_base=base)
    session.add(pack)
    await session.flush()

    user.active_pack_id = pack.id
    user.state = None
    user.state_data = {}
    await session.flush()
    return pack


async def list_user_packs(session: AsyncSession, user_id: int) -> list[Pack]:
    result = await session.scalars(
        select(Pack).where(Pack.user_id == user_id).order_by(Pack.id.desc()).limit(20)
    )
    return list(result)


async def get_user_pack(session: AsyncSession, user_id: int, pack_id: int) -> Pack | None:
    return await session.scalar(select(Pack).where(Pack.id == pack_id, Pack.user_id == user_id))


async def set_active_pack(session: AsyncSession, user: User, pack_id: int) -> Pack | None:
    pack = await get_user_pack(session, user.id, pack_id)
    if pack is None:
        return None
    user.active_pack_id = pack.id
    await session.flush()
    return pack


async def pending_count(session: AsyncSession, pack_id: int) -> int:
    value = await session.scalar(
        select(func.count(Sticker.id)).where(
            Sticker.pack_id == pack_id, Sticker.status == "pending"
        )
    )
    return int(value or 0)


async def list_sets(session: AsyncSession, pack_id: int) -> list[PackSet]:
    result = await session.scalars(
        select(PackSet).where(PackSet.pack_id == pack_id).order_by(PackSet.set_index)
    )
    return list(result)


async def add_pending_sticker(
    session: AsyncSession,
    pack: Pack,
    file_id: str,
    sticker_format: str,
    emoji_list: list[str],
) -> Sticker:
    last_position = await session.scalar(
        select(func.max(Sticker.position)).where(Sticker.pack_id == pack.id)
    )
    sticker = Sticker(
        pack_id=pack.id,
        position=int(last_position or 0) + 1,
        file_id=file_id,
        format=sticker_format,
        emoji_list=emoji_list,
        status="pending",
    )
    session.add(sticker)
    await session.flush()
    return sticker


async def undo_last_pending(session: AsyncSession, pack: Pack) -> Sticker | None:
    sticker = await session.scalar(
        select(Sticker)
        .where(Sticker.pack_id == pack.id, Sticker.status == "pending")
        .order_by(Sticker.position.desc(), Sticker.id.desc())
        .limit(1)
    )
    if sticker is None:
        return None
    await session.execute(delete(Sticker).where(Sticker.id == sticker.id))
    await session.flush()
    return sticker


async def enqueue_publish(
    session: AsyncSession,
    pack: Pack,
    chat_id: int,
    status_message_id: int,
) -> PublishJob | None:
    active_job = await session.scalar(
        select(PublishJob.id).where(
            PublishJob.pack_id == pack.id,
            PublishJob.status.in_(("queued", "running")),
        )
    )
    if active_job:
        return None

    job = PublishJob(
        pack_id=pack.id,
        user_id=pack.user_id,
        chat_id=chat_id,
        status_message_id=status_message_id,
        status="queued",
        created_at=datetime.now(UTC),
    )
    try:
        async with session.begin_nested():
            session.add(job)
            await session.flush()
    except IntegrityError:
        return None
    await session.commit()
    wake_worker()
    return job
