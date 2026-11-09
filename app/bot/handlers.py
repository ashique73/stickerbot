import html
import logging
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards import main_menu, pack_actions, pack_menu
from app.config import get_settings
from app.models import Pack, User
from app.services.emoji import extract_emoji
from app.services.errors import StickerBotError, UnsupportedMediaError
from app.services.media import MediaSource, format_size, prepare_sticker
from app.services.packs import (
    add_pending_sticker,
    create_pack,
    enqueue_publish,
    get_user_pack,
    list_sets,
    list_user_packs,
    pending_count,
    set_active_pack,
    undo_last_pending,
)
from app.services.users import clear_state, get_active_pack, set_state

logger = logging.getLogger(__name__)
settings = get_settings()
router = Router(name="stickerbot")
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")

WELCOME = (
    "<b>Sticker pack bot</b>\n"
    "Create a pack, send images, videos, .tgs files, or existing Telegram stickers, "
    "then publish with /done.\n\n"
    f"Files up to {format_size(settings.max_media_bytes)}; .tgs files up to 64 KB.\n\n"
    "Caption any media with emoji to set its emoji. Without a caption I use "
    f"{settings.default_emoji}.\n\n"
    "Commands: /newpack, /packs, /current, /done, /undo, /cancel, /ping"
)
HELP = (
    "<b>How it works</b>\n"
    "1. /newpack and send a title.\n"
    "2. Send images, videos, .tgs files, or Telegram stickers.\n"
    "3. Add emoji in the caption if you want specific ones.\n"
    "4. /done publishes the pack. Packs split automatically at 120 stickers.\n\n"
    "Other commands: /packs, /current, /undo, /cancel, /ping"
)


def _escape(value: str) -> str:
    return html.escape(value)


def _format_label(sticker_format: str) -> str:
    return {"static": "image", "video": "video", "animated": "animated"}.get(
        sticker_format, sticker_format
    )


async def _pack_summary(session: AsyncSession, pack: Pack) -> tuple[str, int]:
    pending = await pending_count(session, pack.id)
    sets = await list_sets(session, pack.id)
    lines = [f"<b>{_escape(pack.title)}</b>", f"Pending stickers: {pending}"]
    if sets:
        lines.append(f"Published sets: {len(sets)}")
        for item in sets:
            link = f"https://t.me/addstickers/{item.short_name}"
            lines.append(f'<a href="{link}">{_escape(item.title)}</a> — {item.sticker_count}/120')
    else:
        lines.append("Not published yet.")
    lines.append("")
    lines.append("Send media now, or captions with emoji, then use /done.")
    return "\n".join(lines), pending


async def _safe_edit(message: Message, text: str, reply_markup=None) -> None:
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramAPIError:
        pass


def _update_age_ms(message: Message, now: datetime) -> int:
    sent_at = message.date
    if sent_at.tzinfo is None:
        sent_at = sent_at.replace(tzinfo=UTC)
    return max(0, int((now - sent_at).total_seconds() * 1000))


async def _send_pack_list(message: Message, session: AsyncSession, user: User) -> None:
    packs = await list_user_packs(session, user.id)
    if not packs:
        await message.answer(
            "You have no packs yet. Create one with /newpack.",
            reply_markup=main_menu(),
        )
        return
    lines = ["<b>Your packs</b>"]
    for pack in packs:
        mark = " ✅" if pack.id == user.active_pack_id else ""
        lines.append(f"• {_escape(pack.title)}{mark}")
    await message.answer(
        "\n".join(lines),
        reply_markup=pack_menu(packs, user.active_pack_id),
    )


async def _start_publish(message: Message, session: AsyncSession, pack: Pack) -> None:
    pending = await pending_count(session, pack.id)
    sets = await list_sets(session, pack.id)
    if pending == 0:
        if sets:
            text, _ = await _pack_summary(session, pack)
            await message.answer(f"{text}\n\nThere are no new stickers to publish.")
        else:
            await message.answer("This pack has no stickers yet. Send some media first.")
        return

    status = await message.answer(
        f"Publishing {pending} sticker(s) from <b>{_escape(pack.title)}</b>..."
    )
    job = await enqueue_publish(session, pack, message.chat.id, status.message_id)
    if job is None:
        await _safe_edit(status, "Publishing is already queued or running for this pack.")


def _document_source(message: Message) -> MediaSource | None:
    document = message.document
    if document is None:
        return None
    file_name = (document.file_name or "").lower()
    mime_type = (document.mime_type or "").lower()
    suffix = Path(file_name).suffix
    if suffix not in {".tgs", ".webp", ".png", ".jpg", ".jpeg", ".webm", ".mp4", ".gif"}:
        suffix = ""

    if suffix == ".tgs" or mime_type == "application/x-tgsticker":
        return MediaSource(
            file_id=document.file_id,
            format="animated",
            suffix=".tgs",
            declared_size=document.file_size,
            max_download_bytes=64 * 1024,
        )
    if suffix in {".webm", ".mp4", ".gif"} or mime_type in {"video/webm", "video/mp4", "image/gif"}:
        return MediaSource(
            file_id=document.file_id,
            format="video",
            suffix=suffix or ".mp4",
            declared_size=document.file_size,
            max_download_bytes=settings.max_media_bytes,
        )
    if suffix in {".webp", ".png", ".jpg", ".jpeg"} or mime_type in {
        "image/webp",
        "image/png",
        "image/jpeg",
    }:
        return MediaSource(
            file_id=document.file_id,
            format="static",
            suffix=suffix or ".png",
            declared_size=document.file_size,
            max_download_bytes=settings.max_media_bytes,
        )
    return None


def _media_source(message: Message) -> MediaSource | None:
    if message.sticker is not None:
        sticker = message.sticker
        if getattr(sticker, "type", "regular") != "regular":
            return None
        sticker_format = (
            "animated" if sticker.is_animated else "video" if sticker.is_video else "static"
        )
        override = [sticker.emoji] if sticker.emoji else None
        return MediaSource(
            file_id=sticker.file_id,
            format=sticker_format,
            existing=True,
            emoji_override=override,
        )
    if message.photo:
        photo = message.photo[-1]
        return MediaSource(
            file_id=photo.file_id,
            format="static",
            suffix=".jpg",
            declared_size=photo.file_size,
            max_download_bytes=settings.max_media_bytes,
        )
    if message.video:
        return MediaSource(
            file_id=message.video.file_id,
            format="video",
            suffix=".mp4",
            declared_size=message.video.file_size,
            max_download_bytes=settings.max_media_bytes,
        )
    if message.animation:
        return MediaSource(
            file_id=message.animation.file_id,
            format="video",
            suffix=".mp4",
            declared_size=message.animation.file_size,
            max_download_bytes=settings.max_media_bytes,
        )
    if message.video_note:
        return MediaSource(
            file_id=message.video_note.file_id,
            format="video",
            suffix=".mp4",
            declared_size=message.video_note.file_size,
            max_download_bytes=settings.max_media_bytes,
        )
    if message.document:
        return _document_source(message)
    return None


@router.message(CommandStart())
async def cmd_start(message: Message, session: AsyncSession, db_user: User | None) -> None:
    if db_user is None:
        return
    await clear_state(session, db_user)
    await message.answer(WELCOME, reply_markup=main_menu())


@router.message(Command("help"))
async def cmd_help(message: Message, session: AsyncSession, db_user: User | None) -> None:
    if db_user is not None:
        await clear_state(session, db_user)
    await message.answer(HELP, reply_markup=main_menu())


@router.message(Command("ping"))
async def cmd_ping(message: Message, bot: Bot) -> None:
    started = time.perf_counter()
    received_at = datetime.now(UTC)
    await bot.get_me()
    api_ms = (time.perf_counter() - started) * 1000
    update_age_ms = _update_age_ms(message, received_at)

    send_started = time.perf_counter()
    sent = await message.answer(
        f"Pong!\nTelegram API: {api_ms:.0f} ms\nUpdate age: {update_age_ms} ms"
    )
    send_ms = (time.perf_counter() - send_started) * 1000
    total_ms = (time.perf_counter() - started) * 1000

    try:
        await sent.edit_text(
            f"Pong!\n"
            f"Telegram API: {api_ms:.0f} ms\n"
            f"Send: {send_ms:.0f} ms\n"
            f"Total: {total_ms:.0f} ms\n"
            f"Update age: {update_age_ms} ms"
        )
    except TelegramAPIError:
        pass


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, session: AsyncSession, db_user: User | None) -> None:
    if db_user is None:
        return
    await clear_state(session, db_user)
    await message.answer("Cancelled.", reply_markup=main_menu())


@router.message(Command("newpack"))
async def cmd_newpack(message: Message, session: AsyncSession, db_user: User | None) -> None:
    if db_user is None:
        return
    await set_state(session, db_user, "awaiting_title")
    await message.answer("Send a title for the new sticker pack (1-64 characters).")


@router.message(Command("packs"))
async def cmd_packs(message: Message, session: AsyncSession, db_user: User | None) -> None:
    if db_user is None:
        return
    await clear_state(session, db_user)
    await _send_pack_list(message, session, db_user)


@router.message(Command("use"))
async def cmd_use(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
    db_user: User | None,
) -> None:
    if db_user is None:
        return
    await clear_state(session, db_user)
    if not command.args:
        await _send_pack_list(message, session, db_user)
        return
    try:
        pack_id = int(command.args.strip())
    except ValueError:
        await message.answer("Usage: /use <pack number>")
        return
    pack = await set_active_pack(session, db_user, pack_id)
    if pack is None:
        await message.answer("That pack does not belong to you.")
        return
    text, pending = await _pack_summary(session, pack)
    await message.answer(
        text,
        reply_markup=pack_actions(pack.id, pending, bool(await list_sets(session, pack.id))),
    )


@router.message(Command("current"))
async def cmd_current(message: Message, session: AsyncSession, db_user: User | None) -> None:
    if db_user is None:
        return
    await clear_state(session, db_user)
    pack = await get_active_pack(session, db_user.id)
    if pack is None:
        await message.answer("No active pack. Create one with /newpack.", reply_markup=main_menu())
        return
    text, pending = await _pack_summary(session, pack)
    await message.answer(
        text,
        reply_markup=pack_actions(pack.id, pending, bool(await list_sets(session, pack.id))),
    )


@router.message(Command("done"))
async def cmd_done(message: Message, session: AsyncSession, db_user: User | None) -> None:
    if db_user is None:
        return
    await clear_state(session, db_user)
    pack = await get_active_pack(session, db_user.id)
    if pack is None:
        await message.answer("No active pack. Create one with /newpack.", reply_markup=main_menu())
        return
    await _start_publish(message, session, pack)


@router.message(Command("undo"))
async def cmd_undo(message: Message, session: AsyncSession, db_user: User | None) -> None:
    if db_user is None:
        return
    await clear_state(session, db_user)
    pack = await get_active_pack(session, db_user.id)
    if pack is None:
        await message.answer("No active pack. Create one with /newpack.", reply_markup=main_menu())
        return
    removed = await undo_last_pending(session, pack)
    if removed is None:
        await message.answer("There are no pending stickers to remove.")
        return
    pending = await pending_count(session, pack.id)
    await message.answer(
        f"Removed the last pending sticker. {pending} still pending.",
        reply_markup=pack_actions(pack.id, pending, bool(await list_sets(session, pack.id))),
    )


@router.callback_query(F.data == "newpack")
async def cb_newpack(callback: CallbackQuery, session: AsyncSession, db_user: User | None) -> None:
    if db_user is None or not isinstance(callback.message, Message):
        await callback.answer()
        return
    await set_state(session, db_user, "awaiting_title")
    await callback.answer()
    await callback.message.answer("Send a title for the new sticker pack (1-64 characters).")


@router.callback_query(F.data == "packs")
async def cb_packs(callback: CallbackQuery, session: AsyncSession, db_user: User | None) -> None:
    if db_user is None or not isinstance(callback.message, Message):
        await callback.answer()
        return
    await clear_state(session, db_user)
    await callback.answer()
    await _send_pack_list(callback.message, session, db_user)


@router.callback_query(F.data.startswith("use:"))
async def cb_use(callback: CallbackQuery, session: AsyncSession, db_user: User | None) -> None:
    if db_user is None or not isinstance(callback.message, Message):
        await callback.answer()
        return
    await clear_state(session, db_user)
    try:
        pack_id = int(callback.data.partition(":")[2])
    except ValueError:
        await callback.answer("Invalid pack", show_alert=True)
        return
    pack = await set_active_pack(session, db_user, pack_id)
    if pack is None:
        await callback.answer("That pack does not belong to you.", show_alert=True)
        return
    text, pending = await _pack_summary(session, pack)
    sets = await list_sets(session, pack.id)
    await _safe_edit(
        callback.message,
        text,
        pack_actions(pack.id, pending, bool(sets)),
    )
    await callback.answer("Selected")


@router.callback_query(F.data.startswith("publish:"))
async def cb_publish(callback: CallbackQuery, session: AsyncSession, db_user: User | None) -> None:
    if db_user is None or not isinstance(callback.message, Message):
        await callback.answer()
        return
    await clear_state(session, db_user)
    try:
        pack_id = int(callback.data.partition(":")[2])
    except ValueError:
        await callback.answer("Invalid pack", show_alert=True)
        return
    pack = await get_user_pack(session, db_user.id, pack_id)
    if pack is None:
        await callback.answer("That pack does not belong to you.", show_alert=True)
        return
    await callback.answer("Publishing queued")
    await _start_publish(callback.message, session, pack)


@router.callback_query(F.data.startswith("undo:"))
async def cb_undo(callback: CallbackQuery, session: AsyncSession, db_user: User | None) -> None:
    if db_user is None or not isinstance(callback.message, Message):
        await callback.answer()
        return
    await clear_state(session, db_user)
    try:
        pack_id = int(callback.data.partition(":")[2])
    except ValueError:
        await callback.answer("Invalid pack", show_alert=True)
        return
    pack = await get_user_pack(session, db_user.id, pack_id)
    if pack is None:
        await callback.answer("That pack does not belong to you.", show_alert=True)
        return
    removed = await undo_last_pending(session, pack)
    if removed is None:
        await callback.answer("No pending stickers to remove.", show_alert=True)
        return
    text, pending = await _pack_summary(session, pack)
    sets = await list_sets(session, pack.id)
    await _safe_edit(callback.message, text, pack_actions(pack.id, pending, bool(sets)))
    await callback.answer("Removed the last pending sticker")


@router.message(F.text)
async def handle_title(message: Message, session: AsyncSession, db_user: User | None) -> None:
    if db_user is None:
        return
    if db_user.state != "awaiting_title":
        await message.answer(
            "Use /newpack to create a pack, or /packs to choose one.",
            reply_markup=main_menu(),
        )
        return

    title = (message.text or "").strip()
    if not title or len(title) > 64:
        await message.answer("Send a title between 1 and 64 characters.")
        return

    try:
        pack = await create_pack(session, db_user, title)
    except Exception:
        logger.exception("Could not create pack")
        await message.answer("Could not create the pack. Try again.")
        return

    await message.answer(
        f"Created <b>{_escape(pack.title)}</b>.\n"
        "Send images, videos, .tgs files, or existing stickers. "
        "Use /done when you are ready to publish.",
        reply_markup=pack_actions(pack.id, 0, False),
    )


@router.message(F.photo | F.video | F.animation | F.document | F.sticker | F.video_note)
async def handle_media(
    message: Message,
    session: AsyncSession,
    db_user: User | None,
    bot: Bot,
) -> None:
    if db_user is None:
        return
    if db_user.state == "awaiting_title":
        await message.answer("Send a pack title first, or /cancel.")
        return
    pack = await get_active_pack(session, db_user.id)
    if pack is None:
        await message.answer(
            "No active pack. Create one with /newpack, or choose one with /packs.",
            reply_markup=main_menu(),
        )
        return

    source = _media_source(message)
    if source is None:
        await message.answer(
            "Unsupported media. Send an image, video, .tgs file, or Telegram sticker."
        )
        return
    if (
        not source.existing
        and source.declared_size is not None
        and source.declared_size > source.max_download_bytes
    ):
        await message.answer(
            f"That file is {format_size(source.declared_size)}. "
            f"The limit is {format_size(source.max_download_bytes)}."
        )
        return

    emoji_list = source.emoji_override or extract_emoji(message.caption, settings.default_emoji)
    status = await message.answer("Processing sticker...")

    try:
        with tempfile.TemporaryDirectory() as workdir:
            file_id = await prepare_sticker(bot, db_user.id, source, Path(workdir))
        await add_pending_sticker(session, pack, file_id, source.format, emoji_list)
        pending = await pending_count(session, pack.id)
    except UnsupportedMediaError as error:
        await _safe_edit(status, _escape(str(error)))
        return
    except StickerBotError as error:
        await _safe_edit(status, f"Could not add the sticker: {_escape(str(error))}")
        return
    except Exception:
        logger.exception("Could not process sticker")
        await _safe_edit(status, "Could not process that media. Try another file.")
        return

    emoji_text = "".join(emoji_list)
    await _safe_edit(
        status,
        f"Added {_format_label(source.format)} sticker {emoji_text}.\n"
        f"{pending} pending in <b>{_escape(pack.title)}</b>. Use /done to publish.",
    )
