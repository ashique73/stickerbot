from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.models import Pack


def main_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ New pack", callback_data="newpack")
    builder.button(text="📦 My packs", callback_data="packs")
    builder.adjust(2)
    return builder.as_markup()


def pack_menu(packs: list[Pack], active_pack_id: int | None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for pack in packs:
        prefix = "✅ " if pack.id == active_pack_id else ""
        builder.button(text=f"{prefix}{pack.title[:40]}", callback_data=f"use:{pack.id}")
    builder.button(text="➕ New pack", callback_data="newpack")
    builder.adjust(1)
    return builder.as_markup()


def pack_actions(pack_id: int, pending: int, has_sets: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if pending:
        builder.button(text="📤 Publish now", callback_data=f"publish:{pack_id}")
        builder.button(text="↩️ Undo last sticker", callback_data=f"undo:{pack_id}")
    if has_sets:
        builder.button(text="🔗 Show links", callback_data=f"use:{pack_id}")
    builder.button(text="📦 My packs", callback_data="packs")
    builder.adjust(1)
    return builder.as_markup()
