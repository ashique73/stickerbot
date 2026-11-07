import asyncio
import logging

from app.bot.instance import create_bot, create_dispatcher
from app.config import get_settings
from app.db import init_db
from app.services.publish_worker import reset_running_jobs, run_worker
from app.services.short_names import set_bot_username

logger = logging.getLogger(__name__)


async def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    await init_db()
    bot = create_bot(settings)
    dispatcher = create_dispatcher()
    me = await bot.get_me()
    set_bot_username(me.username)
    await reset_running_jobs()

    stop_event = asyncio.Event()
    worker_task = asyncio.create_task(run_worker(bot, stop_event))

    try:
        await bot.delete_webhook(drop_pending_updates=False)
        await dispatcher.start_polling(
            bot,
            allowed_updates=dispatcher.resolve_used_update_types(),
        )
    finally:
        stop_event.set()
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Publish worker stopped unexpectedly")
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
