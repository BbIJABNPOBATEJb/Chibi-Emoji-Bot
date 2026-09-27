"""Skins sent outside the wizard: remember them and ask which pack they go to."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from ..db import User
from ..sources import parse_names
from .app import App
from .packs import load_pack
from .ui import Menu, PackCB, btn, kb, pack_label

router = Router(name="fallback")

MAX_PENDING = 20


async def _remember(message: Message, state: FSMContext, app: App, me: User, admin: bool, item: dict) -> None:
    async with app.lock(me.id):
        data = await state.get_data()
        pend = data.get("pending") or {"list": []}
        pend["list"] = (pend["list"] + [item])[-MAX_PENDING:]
        await state.update_data(pending=pend)
        packs = [p for p in await app.db.user_packs(me.id) if p.count < p.k.max_items]
        rows = [[btn(pack_label(p), PackCB(act="qadd", pid=p.id))] for p in packs]
        if await app.why_no_new_pack(me.id, admin) is None:
            rows.append([btn("➕ В новый пак", Menu(act="new"))])
        rows.append([btn("❌ Не добавлять", Menu(act="home"))])
        n = len(pend["list"])
        text = ("📥 Принял" + (f" ({n} сообщ.)" if n > 1 else "") + ". В какой пак добавить эти скины?\n"
                "Можно прислать ещё — я всё запомню.")
        msg = await message.answer(text, reply_markup=kb(*rows))
        old = data.get("ask")
        await state.update_data(ask=msg.message_id)
        if old:
            try:
                await message.bot.delete_message(message.chat.id, old)
            except TelegramBadRequest:
                pass


@router.message(StateFilter(None), F.text & ~F.text.startswith("/"))
async def quick_text(message: Message, state: FSMContext, app: App, me: User, admin: bool):
    names, _ = parse_names(message.text)
    if not names:
        await message.answer("Не понял 🙂 Пришлите ники Minecraft, PNG-скин файлом или ZIP-архив — "
                             "или откройте меню.", reply_markup=kb(btn("🏠 Меню", Menu(act="home"))))
        return
    await _remember(message, state, app, me, admin, {"type": "text", "text": message.text[:4000]})


@router.message(StateFilter(None), F.document)
async def quick_doc(message: Message, state: FSMContext, app: App, me: User, admin: bool):
    d = message.document
    await _remember(message, state, app, me, admin,
                    {"type": "doc", "file_id": d.file_id, "name": d.file_name, "size": d.file_size})


@router.message(StateFilter(None), F.photo)
async def quick_photo(message: Message):
    await message.answer("🖼 Картинку Telegram сжал и испортил прозрачность. "
                         "Отправьте PNG-скин <b>как файл</b>: 📎 → Файл.")


@router.callback_query(PackCB.filter(F.act == "qadd"))
async def cb_quick_add(cq: CallbackQuery, callback_data: PackCB, state: FSMContext, app: App, me: User,
                       admin: bool):
    from .wizard import start_collect

    pack = await load_pack(cq, app, me, callback_data.pid)
    if not pack:
        return
    pending = (await state.get_data()).get("pending")
    await cq.answer()
    await start_collect(cq.message, state, app, me, admin, pack, replace_msg=cq.message, pending=pending)
