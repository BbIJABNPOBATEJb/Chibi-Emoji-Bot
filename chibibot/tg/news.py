"""/news: an announcement about the horses update, sent to every user only when the admin confirms.

The admin first gets the exact message users will receive (an animated example and a short
guide), then a confirmation button. Nothing goes out without that tap.
"""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from ..db import User
from ..jobs import WorkerCrashed, job_news_example
from ..render.encode import EncodeError
from ..render.engine import RenderError
from ..render.skin import default_skin
from ..sources import collect_names
from .alert import deliver, report
from .app import App
from .ui import AdminCB, Menu, btn, kb

log = logging.getLogger(__name__)
router = Router(name="news")

EXAMPLE_NICK = "Grian"   # a well-known skin for the example; the built-in one is used if it cannot be fetched

NEWS_TEXT = (
    "🐴 <b>Новое: лошади!</b>\n\n"
    "Ваш чиби теперь может скакать верхом, а ещё можно сделать лошадок без наездника.\n"
    "• 11 мастей: от белой и рыжей до ослика, мула, скелета и зомби\n"
    "• отметины, седло, сундуки и броня: кожаная, железная, золотая, алмазная\n"
    "• жеребёнок, обычная или большая лошадь\n"
    "• 11 анимаций: шагом, рысью, галопом, на дыбы, прыжок, щиплет траву, брыкается и другие\n\n"
    "<b>Как сделать</b>\n"
    "1. Откройте пак → «➕ Добавить» и пришлите ник или скин.\n"
    "2. На экране «Итог» нажмите «🐴 Лошадь» → «Верхом».\n"
    "3. Отметьте масти, выберите отметины, снаряжение и размер.\n"
    "4. «🎬 Анимация» — отметьте нужные и нажмите «✅ Создать».\n\n"
    "🐎 Лошадки без игрока: «➕ Добавить» → «🐴 Только лошадки (без скина)».\n"
    "Можно отметить сразу несколько мастей и анимаций — получится по картинке на каждую."
)


def _markup():
    return kb([btn("📦 Мои паки", Menu(act="packs")), btn("➕ Новый пак", Menu(act="new"))])


async def _recipients(app: App, me: User) -> list[User]:
    return [u for u in await app.db.users_seen_since("") if u.id != me.id]


@router.message(Command("news"))
async def cmd_news(message: Message, bot: Bot, state: FSMContext, app: App, me: User, admin: bool):
    if not admin:
        return
    wait = await message.answer("⏳ Рисую пример для новости…")
    skin = default_skin().png
    got = await collect_names(app.mojang, app.store, [EXAMPLE_NICK])
    if got.items:
        try:
            skin = app.store.load_bytes(got.items[0].sha1)
        except FileNotFoundError:
            pass
    try:
        data, name = await app.worker.run(job_news_example, skin, heavy=True)
    except (RenderError, EncodeError, WorkerCrashed) as exc:
        await wait.edit_text(f"⚠️ Не получилось нарисовать пример: {exc}")
        return
    preview = await bot.send_animation(message.chat.id, BufferedInputFile(data, name), caption=NEWS_TEXT,
                                       reply_markup=_markup())
    file_id = preview.animation.file_id if preview.animation else (preview.document.file_id if preview.document
                                                                   else None)
    users = await _recipients(app, me)
    await state.update_data(news={"file_id": file_id})
    try:
        await wait.delete()
    except Exception:  # noqa: BLE001 - cosmetic
        pass
    await message.answer(
        f"☝️ Так новость увидят <b>{len(users)}</b> чел. — все, кто когда-либо писал боту (кроме вас).\n"
        "Отправить?",
        reply_markup=kb([btn(f"📣 Отправить всем ({len(users)})", AdminCB(act="news_go")),
                         btn("Отмена", Menu(act="home"))]),
    )


@router.callback_query(AdminCB.filter(F.act == "news_go"))
async def cb_news_go(cq: CallbackQuery, state: FSMContext, bot: Bot, app: App, me: User, admin: bool):
    if not admin:
        await cq.answer("Только для администраторов.", show_alert=True)
        return
    draft = (await state.get_data()).get("news")
    if not draft or not draft.get("file_id"):
        await cq.answer("Черновик устарел — отправьте /news ещё раз.", show_alert=True)
        return
    await state.update_data(news=None)  # one tap = one broadcast
    users = await _recipients(app, me)
    await cq.answer("Отправляю…")
    status = await cq.message.edit_text(f"📣 Отправляю новость: 0/{len(users)}…")
    file_id = draft["file_id"]

    async def send(uid: int) -> None:
        await bot.send_animation(uid, file_id, caption=NEWS_TEXT, reply_markup=_markup())

    sent, blocked, failed = await deliver(users, send, status, "📣 Отправляю новость")
    await app.db.log(me.id, "alert", details=f"новость о лошадях: доставлено {sent}, заблокировали {blocked}, "
                                             f"ошибок {failed}")
    await report(cq, status, "📣 Новость отправлена.", sent, blocked, failed)
