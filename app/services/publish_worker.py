import asyncio
import html
import logging
from datetime import UTC, datetime

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from sqlalchemy import select, update

from app.db import SessionLocal
from app.models import Pack, PublishJob
from app.services.publish import PublishResult, publish_pack

logger = logging.getLogger(__name__)
wake_event = asyncio.Event()


def wake_worker() -> None:
    wake_event.set()


async def reset_running_jobs() -> None:
    async with SessionLocal() as session:
        await session.execute(
            update(PublishJob)
            .where(PublishJob.status == "running")
            .values(status="queued", started_at=None)
        )
        await session.commit()


async def claim_job() -> int | None:
    async with SessionLocal() as session:
        job = await session.scalar(
            select(PublishJob)
            .where(PublishJob.status == "queued")
            .order_by(PublishJob.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if job is None:
            return None
        job.status = "running"
        job.started_at = datetime.now(UTC)
        job.attempts += 1
        await session.commit()
        return job.id


async def _notify(bot: Bot, chat_id: int, message_id: int, text: str) -> None:
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text)
    except TelegramAPIError:
        try:
            await bot.send_message(chat_id=chat_id, text=text)
        except TelegramAPIError:
            logger.exception("Could not send publish result to chat %s", chat_id)


def _success_text(result: PublishResult) -> str:
    lines = [f"✅ Published {result.published} sticker(s)."]
    if result.links:
        lines.append("Sticker sets:")
        lines.extend(f'<a href="{link}">{html.escape(link)}</a>' for link in result.links)
    return "\n".join(lines)


def _failure_text(error: Exception) -> str:
    message = html.escape(str(error)[:500] or error.__class__.__name__)
    return f"❌ Publishing failed: {message}"


async def fail_job(bot: Bot, job_id: int, error: Exception) -> None:
    async with SessionLocal() as session:
        job = await session.get(PublishJob, job_id)
        if job is None or job.status != "running":
            return

        chat_id = job.chat_id
        status_message_id = job.status_message_id
        job.status = "failed"
        job.error = str(error)[:1000]
        job.finished_at = datetime.now(UTC)
        await session.commit()
        await _notify(bot, chat_id, status_message_id, _failure_text(error))


async def process_job(bot: Bot, job_id: int) -> None:
    async with SessionLocal() as session:
        job = await session.get(PublishJob, job_id)
        if job is None or job.status != "running":
            return

        chat_id = job.chat_id
        status_message_id = job.status_message_id
        pack = await session.get(Pack, job.pack_id)
        if pack is None:
            job.status = "failed"
            job.error = "Pack not found"
            job.finished_at = datetime.now(UTC)
            await session.commit()
            await _notify(
                bot,
                chat_id,
                status_message_id,
                _failure_text(RuntimeError("Pack not found")),
            )
            return

        try:
            result = await publish_pack(bot, session, pack.id)
        except Exception as error:
            logger.exception("Publishing failed for pack %s", pack.id)
            await session.rollback()
            job = await session.get(PublishJob, job_id)
            if job is not None:
                job.status = "failed"
                job.error = str(error)[:1000]
                job.finished_at = datetime.now(UTC)
                await session.commit()
            await _notify(bot, chat_id, status_message_id, _failure_text(error))
            return

        job = await session.get(PublishJob, job_id)
        if job is not None:
            job.status = "done"
            job.error = None
            job.finished_at = datetime.now(UTC)
            await session.commit()
        await _notify(bot, chat_id, status_message_id, _success_text(result))


async def run_worker(bot: Bot, stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            job_id = await claim_job()
        except Exception:
            logger.exception("Could not claim a publish job")
            await asyncio.sleep(5)
            continue

        if job_id is None:
            await wake_event.wait()
            wake_event.clear()
            continue

        try:
            await process_job(bot, job_id)
        except Exception as error:
            logger.exception("Unexpected publish worker error")
            try:
                await fail_job(bot, job_id, error)
            except Exception:
                logger.exception("Could not mark publish job %s as failed", job_id)
            await asyncio.sleep(5)
