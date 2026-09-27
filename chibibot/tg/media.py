"""Sending/replacing photo and animation messages, reusing Telegram file ids."""
from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (BufferedInputFile, InlineKeyboardMarkup, InputMediaAnimation, InputMediaPhoto,
                           Message)

from .app import App

log = logging.getLogger(__name__)


def _file_id(msg: Message, kind: str) -> str | None:
    if kind == "photo" and msg.photo:
        return msg.photo[-1].file_id
    if kind == "animation":
        if msg.animation:
            return msg.animation.file_id
        if msg.document:
            return msg.document.file_id
        if msg.video:
            return msg.video.file_id
    return None


def _input(kind: str, payload: bytes | str, filename: str):
    return BufferedInputFile(payload, filename) if isinstance(payload, bytes) else payload


async def send_media(bot: Bot, chat_id: int, kind: str, payload: bytes | str, filename: str, caption: str,
                     markup: InlineKeyboardMarkup | None) -> Message:
    media = _input(kind, payload, filename)
    if kind == "photo":
        return await bot.send_photo(chat_id, media, caption=caption, reply_markup=markup)
    return await bot.send_animation(chat_id, media, caption=caption, reply_markup=markup)


async def show_media(app: App, bot: Bot, chat_id: int, old: Message | int | None, kind: str,
                     payload: bytes | str, filename: str, caption: str, markup: InlineKeyboardMarkup | None,
                     cache_key: str | None = None) -> Message:
    """Put media into `old` (edit) or send a fresh message; remember the uploaded file id."""
    msg: Message | None = None
    old_id = old.message_id if isinstance(old, Message) else old
    old_is_media = isinstance(old, int) or (isinstance(old, Message) and bool(old.photo or old.animation or old.document))
    if old_id and old_is_media:
        media_cls = InputMediaPhoto if kind == "photo" else InputMediaAnimation
        try:
            res = await bot.edit_message_media(
                chat_id=chat_id, message_id=old_id,
                media=media_cls(media=_input(kind, payload, filename), caption=caption),
                reply_markup=markup,
            )
            msg = res if isinstance(res, Message) else None
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc):
                try:
                    await bot.edit_message_reply_markup(chat_id=chat_id, message_id=old_id, reply_markup=markup)
                except TelegramBadRequest:
                    pass
                return old if isinstance(old, Message) else None  # type: ignore[return-value]
            log.debug("edit_message_media failed: %s", exc)
    if msg is None:
        msg = await send_media(bot, chat_id, kind, payload, filename, caption, markup)
        if old_id:
            try:
                await bot.delete_message(chat_id, old_id)
            except TelegramBadRequest:
                pass
    if cache_key and isinstance(payload, bytes):
        fid = _file_id(msg, kind)
        if fid:
            app.media.put(cache_key, kind, fid)
    return msg


async def set_caption(bot: Bot, chat_id: int, message_id: int | None, caption: str,
                      markup: InlineKeyboardMarkup | None = None) -> None:
    if not message_id:
        return
    try:
        await bot.edit_message_caption(chat_id=chat_id, message_id=message_id, caption=caption, reply_markup=markup)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            log.debug("edit caption failed: %s", exc)
