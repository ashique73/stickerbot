import asyncio
import hmac
import logging

from aiogram.types import Update
from fastapi import FastAPI, HTTPException, Request
from pydantic import ValidationError

from app.bot.instance import create_bot, create_dispatcher
from app.config import get_settings
from app.db import init_db
from app.services.publish_worker import reset_running_jobs, run_worker
from app.services.short_names import set_bot_username

logger = logging.getLogger(__name__)


async def lifespan(app: FastAPI):
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

    app.state.settings = settings
    app.state.bot = bot
    app.state.dispatcher = dispatcher

    stop_event = asyncio.Event()
    worker_task = asyncio.create_task(run_worker(bot, stop_event))

    webhook_url = settings.resolved_webhook_url
    if webhook_url:
        await bot.set_webhook(
            url=webhook_url,
            secret_token=settings.webhook_secret or None,
            allowed_updates=dispatcher.resolve_used_update_types(),
            drop_pending_updates=False,
        )
        logger.info("Webhook configured for %s", webhook_url)
    else:
        logger.warning("No WEBHOOK_URL or PUBLIC_BASE_URL configured")

    try:
        yield
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


app = FastAPI(title="Sticker Pack Bot", lifespan=lifespan)


@app.get("/")
async def root() -> dict[str, bool]:
    return {"ok": True}


@app.get("/health")
async def health() -> dict[str, bool]:
    return {"ok": True}


@app.post("/webhook")
async def telegram_webhook(request: Request) -> dict[str, bool]:
    settings = request.app.state.settings
    if settings.webhook_secret:
        header = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if not header or not hmac.compare_digest(header, settings.webhook_secret):
            raise HTTPException(status_code=403, detail="Invalid webhook secret")

    try:
        payload = await request.json()
        update = Update.model_validate(payload, context={"bot": request.app.state.bot})
    except (ValueError, ValidationError):
        raise HTTPException(status_code=400, detail="Invalid update") from None

    await request.app.state.dispatcher.feed_update(request.app.state.bot, update)
    return {"ok": True}
