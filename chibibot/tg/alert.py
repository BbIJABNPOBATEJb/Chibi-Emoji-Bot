"""/alert: tell everyone who used the bot recently (e.g. while it was stuck) that it works again.

Recipients are picked by last_seen, which the middleware bumps on every message or button
press — even when the handler behind it hung — so users who wrote during an outage are included.
"""
from __future__ import annotations

import asyncio
import html
import logging
from typing import Awaitable, Callable

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from ..db import User, ago
from .app import App
from .ui import AdminCB, Menu, btn, kb

log = logging.getLogger(__name__)
router = Router(name="alert")

DEFAULT_HOURS = 24
MAX_HOURS = 24 * 14
DEFAULT_TEXT = ("✅ <b>Бот снова работает!</b>\n"
                "Пока он был недоступен, ваши запросы могли не выполниться — просто повторите их. "
                "Спасибо за терпение 🙌")
USAGE = ("Использование:\n"
         "<code>/alert</code> — всем, кто писал боту за последние 24 ч\n"
         "<code>/alert 30</code> — за последние 30 часов\n"
         "<code>/alert 30 Свой текст</code> или <code>/alert Свой текст</code> — со своим текстом")


def _parse(args: str | None) -> tuple[int, str]:
    parts = (args or "").strip().split(maxsplit=1)
    hours, text = DEFAULT_HOURS, DEFAULT_TEXT
    if parts and parts[0].isdigit():
        hours = max(1, min(MAX_HOURS, int(parts[0])))
        parts = parts[1:]
    if parts:
        text = html.escape(parts[0])
    return hours, text


async def _recipients(app: App, hours: int, me: User) -> list[User]:
    return [u for u in await app.db.users_seen_since(ago(hours)) if u.id != me.id]


@router.message(Command("alert"))
async def cmd_alert(message: Message, command: CommandObject, state: FSMContext, app: App, me: User, admin: bool):
    if not admin:
        return
    if (command.args or "").strip() in ("help", "?"):
        await message.answer(USAGE)
        return
    hours, text = _parse(command.args)
    users = await _recipients(app, hours, me)
    await state.update_data(alert={"hours": hours, "text": text})
    if not users:
        await message.answer(f"За последние {hours} ч боту никто, кроме вас, не писал — отправлять некому.\n\n{USAGE}")
        return
    await message.answer(
        f"📢 <b>Оповещение</b> получат {len(users)} чел. — все, кто писал боту за последние {hours} ч "
        f"(кроме вас).\n\nТекст:\n\n{text}\n\n{USAGE}",
        reply_markup=kb([btn(f"📢 Отправить ({len(users)})", AdminCB(act="alert_go")),
                         btn("Отмена", Menu(act="home"))]),
    )


@router.callback_query(AdminCB.filter(F.act == "alert_go"))
async def cb_alert_go(cq: CallbackQuery, state: FSMContext, bot: Bot, app: App, me: User, admin: bool):
    if not admin:
        await cq.answer("Только для администраторов.", show_alert=True)
        return
    draft = (await state.get_data()).get("alert")
    if not draft:
        await cq.answer("Черновик устарел — отправьте /alert ещё раз.", show_alert=True)
        return
    await state.update_data(alert=None)  # one tap = one broadcast
    hours, text = draft["hours"], draft["text"]
    users = await _recipients(app, hours, me)
    await cq.answer("Отправляю…")
    status = await cq.message.edit_text(f"📢 Отправляю оповещение: 0/{len(users)}…")
    markup = kb(btn("🏠 Меню", Menu(act="home")))

    async def send(uid: int) -> None:
        await bot.send_message(uid, text, reply_markup=markup)

    sent, blocked, failed = await deliver(users, send, status, "📢 Отправляю оповещение")
    await app.db.log(me.id, "alert", details=f"за {hours} ч: доставлено {sent}, заблокировали {blocked}, ошибок {failed}")
    await report(cq, status, "📢 Оповещение отправлено.", sent, blocked, failed)


async def deliver(users: list[User], send: Callable[[int], Awaitable[None]], status: Message | None,
                  label: str) -> tuple[int, int, int]:
    """Send one message to each user, gently; -> (sent, blocked the bot, failed)."""
    sent, blocked, failed = 0, 0, 0
    for n, u in enumerate(users, 1):
        for _ in range(3):
            try:
                await send(u.id)
                sent += 1
                break
            except TelegramRetryAfter as exc:
                await asyncio.sleep(exc.retry_after + 1)
            except TelegramForbiddenError:
                blocked += 1  # blocked the bot or deleted their account
                break
            except TelegramBadRequest as exc:
                log.warning("broadcast to %s failed: %s", u.id, exc)
                failed += 1
                break
        await asyncio.sleep(0.05)  # stay far below Telegram's ~30 messages/second
        if n % 20 == 0 and isinstance(status, Message):
            try:
                await status.edit_text(f"{label}: {n}/{len(users)}…")
            except TelegramBadRequest:
                pass
    return sent, blocked, failed


async def report(cq: CallbackQuery, status: Message | bool | None, head: str, sent: int, blocked: int,
                 failed: int) -> None:
    markup = kb(btn("🏠 Меню", Menu(act="home")))
    text = (f"{head}\n✅ Доставлено: {sent}\n🚫 Заблокировали бота: {blocked}"
            + (f"\n⚠️ Ошибок: {failed}" if failed else ""))
    if isinstance(status, Message):
        await status.edit_text(text, reply_markup=markup)
    else:
        await cq.message.answer(text, reply_markup=markup)
