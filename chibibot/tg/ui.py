"""Callback data and small UI helpers shared by the handlers."""
from __future__ import annotations

import html
import logging

from aiogram.exceptions import TelegramBadRequest
from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

log = logging.getLogger(__name__)


class Menu(CallbackData, prefix="m"):
    act: str


class PackCB(CallbackData, prefix="p"):
    act: str
    pid: int
    arg: int = 0


class EmojiCB(CallbackData, prefix="e"):
    act: str
    eid: int
    arg: int = 0


class Wiz(CallbackData, prefix="w"):
    act: str
    val: str = ""


class AdminCB(CallbackData, prefix="a"):
    act: str
    page: int = 0
    arg: int = 0


def btn(text: str, cb: CallbackData | str | None = None, url: str | None = None) -> InlineKeyboardButton:
    if url:
        return InlineKeyboardButton(text=text, url=url)
    data = cb.pack() if isinstance(cb, CallbackData) else cb
    return InlineKeyboardButton(text=text, callback_data=data)


def kb(*rows: list[InlineKeyboardButton] | InlineKeyboardButton) -> InlineKeyboardMarkup:
    out = []
    for r in rows:
        if not r:
            continue
        out.append(r if isinstance(r, list) else [r])
    return InlineKeyboardMarkup(inline_keyboard=out)


def chunks(items: list, n: int) -> list[list]:
    return [items[i:i + n] for i in range(0, len(items), n)]


def esc(s: str | None) -> str:
    return html.escape(s or "")


def plural(n: int, one: str, few: str, many: str) -> str:
    n10, n100 = n % 10, n % 100
    if n10 == 1 and n100 != 11:
        return one
    if 2 <= n10 <= 4 and not 12 <= n100 <= 14:
        return few
    return many


def pack_label(p) -> str:
    """Button text for a pack: published marker, kind icon, title, item count."""
    return f"{'🟢' if p.tg_created else '⚪️'}{p.k.icon} {p.title} · {p.count}"


def skin_word(n: int) -> str:
    return f"{n} {plural(n, 'скин', 'скина', 'скинов')}"


async def safe_edit_text(msg: Message, text: str, markup: InlineKeyboardMarkup | None = None) -> Message | None:
    """Edit a text message, or replace a media message with a new text one."""
    try:
        if msg.text is not None:
            return await msg.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc):
            return msg
        log.debug("edit_text failed: %s", exc)
    new = await msg.answer(text, reply_markup=markup)
    try:
        await msg.delete()
    except TelegramBadRequest:
        pass
    return new


async def safe_delete(msg: Message | None) -> None:
    if msg is None:
        return
    try:
        await msg.delete()
    except TelegramBadRequest:
        pass
