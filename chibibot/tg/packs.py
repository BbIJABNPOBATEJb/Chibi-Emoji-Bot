"""A pack's card and everything you can do with it and its emojis."""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from ..db import Emoji, Pack, User
from ..jobs import job_overview, job_result
from ..render.options import RenderSettings
from ..stickers import StickerError
from .app import App
from .media import send_media, show_media
from .ui import AdminCB, EmojiCB, Menu, PackCB, btn, chunks, esc, kb, safe_delete, safe_edit_text

log = logging.getLogger(__name__)
router = Router(name="packs")

EMOJI_PAGE = 16
HIST_PAGE = 12

ACTIONS = {
    "pack_create": "🆕 создал пак",
    "pack_rename": "✏️ переименовал пак",
    "pack_delete": "🗑 удалил пак",
    "emoji_add": "➕ добавил",
    "emoji_delete": "➖ удалил",
    "emoji_replace": "🎨 перерисовал",
    "emoji_move": "⏫ переместил",
    "pack_convert": "🔁 конвертировал",
    "admin_claim": "👑 получил права админа",
    "admin_add": "👑 назначил админа",
    "admin_remove": "👤 снял админа",
    "limits_set": "⚙️ изменил лимиты",
}


class PackEdit(StatesGroup):
    title = State()


async def load_pack(cq: CallbackQuery, app: App, me: User, pid: int) -> Pack | None:
    pack = await app.db.get_pack(pid)
    if not pack:
        await cq.answer("Пак не найден — возможно, его удалили.", show_alert=True)
        return None
    if not await app.can_edit(me.id, pack):
        await cq.answer("Это чужой пак.", show_alert=True)
        return None
    return pack


async def pack_view(app: App, pack: Pack, me: User, admin: bool) -> tuple[str, object]:
    k = pack.k
    lines = [f"{k.icon} <b>{esc(pack.title)}</b> — {k.name}"]
    if pack.tg_created:
        lines.append(f"🏷 В Telegram: «{esc(await app.stickers.tg_title(pack.title))}»")
        lines.append(f"🔗 <a href=\"{pack.link}\">t.me/{k.link_prefix}/{pack.name}</a>")
    else:
        lines.append("⚪️ Ещё не опубликован — появится в Telegram после первого добавления.")
    lines.append(f"{k.icon} {k.items}: <b>{pack.count}</b> / {k.max_items}"
                 + (f" · 🎬 анимированных: {pack.animated_count}" if pack.animated_count else ""))
    if pack.owner_id != me.id or admin:
        lines.append(f"👤 Владелец: {await app.who(pack.owner_id)} (<code>{pack.owner_id}</code>)")
    lines.append(f"🆕 Создан: {app.time(pack.created_at)} — {await app.who(pack.created_by)}")
    lines.append(f"✏️ Изменён: {app.time(pack.updated_at)} — {await app.who(pack.updated_by)}")
    rows = [[btn(f"➕ Добавить {'стикеры' if k.key == 'stickers' else 'эмодзи'}", PackCB(act="add", pid=pack.id))]]
    if pack.count:
        rows.append([btn("🖼 Обзор", PackCB(act="view", pid=pack.id)),
                     btn(f"🗂 {k.items} ({pack.count})", PackCB(act="emojis", pid=pack.id))])
    if pack.tg_created:
        rows.append([btn("🔗 Открыть пак в Telegram", url=pack.link)])
    if pack.count:
        other = "стикер-пак" if k.key == "emoji" else "эмодзи-пак"
        rows.append([btn(f"🔁 Сделать {other} из этого", PackCB(act="conv", pid=pack.id))])
    rows.append([btn("✏️ Название", PackCB(act="rename", pid=pack.id)),
                 btn("📜 История", PackCB(act="hist", pid=pack.id))])
    rows.append([btn("🗑 Удалить пак", PackCB(act="del", pid=pack.id))])
    back = Menu(act="packs") if pack.owner_id == me.id else AdminCB(act="packs")
    rows.append([btn("⬅️ Назад", back), btn("🏠 Меню", Menu(act="home"))])
    return "\n".join(lines), kb(*rows)


async def open_pack(target: Message, app: App, pack: Pack, me: User, admin: bool, edit: bool = True) -> None:
    try:
        await app.stickers.sync(pack)
    except Exception as exc:  # noqa: BLE001 - a card must open even if Telegram is flaky
        log.warning("sync %s failed: %s", pack.name, exc)
    pack = await app.db.get_pack(pack.id) or pack
    text, markup = await pack_view(app, pack, me, admin)
    if edit:
        await safe_edit_text(target, text, markup)
    else:
        await target.answer(text, reply_markup=markup)


@router.callback_query(PackCB.filter(F.act == "open"))
async def cb_open(cq: CallbackQuery, callback_data: PackCB, state: FSMContext, app: App, me: User, admin: bool):
    await state.clear()
    pack = await load_pack(cq, app, me, callback_data.pid)
    if pack:
        await cq.answer()
        await open_pack(cq.message, app, pack, me, admin)


@router.callback_query(PackCB.filter(F.act == "add"))
async def cb_add(cq: CallbackQuery, callback_data: PackCB, state: FSMContext, app: App, me: User, admin: bool):
    from .wizard import start_collect

    pack = await load_pack(cq, app, me, callback_data.pid)
    if not pack:
        return
    if pack.count >= pack.k.max_items:
        await cq.answer(f"В паке уже {pack.k.count(pack.count)} — это максимум Telegram.", show_alert=True)
        return
    await cq.answer()
    await start_collect(cq.message, state, app, me, admin, pack, replace_msg=cq.message)


# ------------------------------------------------------------------ rename / delete

@router.callback_query(PackCB.filter(F.act == "rename"))
async def cb_rename(cq: CallbackQuery, callback_data: PackCB, state: FSMContext, app: App, me: User):
    pack = await load_pack(cq, app, me, callback_data.pid)
    if not pack:
        return
    await state.set_state(PackEdit.title)
    await state.update_data(pid=pack.id)
    from .menu import title_hint

    await safe_edit_text(cq.message, f"✏️ Пришлите новое название для «{esc(pack.title)}» ({await title_hint(app)}).",
                         kb(btn("❌ Отмена", PackCB(act="open", pid=pack.id))))
    await cq.answer()


@router.message(PackEdit.title, F.text & ~F.text.startswith("/"))
async def msg_rename(message: Message, state: FSMContext, app: App, me: User, admin: bool):
    data = await state.get_data()
    await state.clear()
    pack = await app.db.get_pack(data.get("pid", 0))
    if not pack or not await app.can_edit(me.id, pack):
        await message.answer("Пак не найден.")
        return
    from .menu import clean_title

    title = await clean_title(app, message.text)
    if not title:
        await message.answer("Название не может быть пустым.")
        return
    try:
        await app.stickers.rename(pack, title)
    except StickerError as exc:
        await message.answer(f"Telegram не принял название: {esc(str(exc))}")
        return
    old = pack.title
    await app.db.touch_pack(pack.id, me.id, title=title)
    pack.title = title
    await app.db.log(me.id, "pack_rename", pack, details=f"{old} → {title}")
    await open_pack(message, app, pack, me, admin, edit=False)


@router.callback_query(PackCB.filter(F.act == "del"))
async def cb_delete(cq: CallbackQuery, callback_data: PackCB, app: App, me: User):
    pack = await load_pack(cq, app, me, callback_data.pid)
    if not pack:
        return
    await safe_edit_text(
        cq.message,
        f"🗑 Удалить пак «{esc(pack.title)}» и всё содержимое ({pack.k.count(pack.count)})?\n"
        "Он исчезнет из Telegram у всех, кто его добавил. Это нельзя отменить.",
        kb([btn("🗑 Да, удалить", PackCB(act="delok", pid=pack.id)),
            btn("Отмена", PackCB(act="open", pid=pack.id))]),
    )
    await cq.answer()


@router.callback_query(PackCB.filter(F.act == "delok"))
async def cb_delete_ok(cq: CallbackQuery, callback_data: PackCB, app: App, me: User, admin: bool):
    from .menu import packs_view

    pack = await load_pack(cq, app, me, callback_data.pid)
    if not pack:
        return
    async with app.stickers.lock(pack.id):
        try:
            await app.stickers.delete_set(pack)
        except StickerError as exc:
            await cq.answer(f"Не получилось: {exc}", show_alert=True)
            return
        await app.db.delete_pack(pack.id)
        await app.db.log(me.id, "pack_delete", pack, details=f"{pack.title} ({pack.k.count(pack.count)}, {pack.name})")
    await cq.answer("Пак удалён")
    text, markup = await packs_view(app, me, admin)
    await safe_edit_text(cq.message, f"🗑 Пак «{esc(pack.title)}» удалён.\n\n" + text, markup)


# ------------------------------------------------------------------ emoji list / card

def _emoji_line(e: Emoji, i: int) -> str:
    return f"{i + 1}. {e.label}{' 🎬' if e.animated else ''}"


@router.callback_query(PackCB.filter(F.act == "emojis"))
async def cb_emojis(cq: CallbackQuery, callback_data: PackCB, app: App, me: User):
    pack = await load_pack(cq, app, me, callback_data.pid)
    if not pack:
        return
    try:
        emojis = await app.stickers.sync(pack)
    except Exception as exc:  # noqa: BLE001
        log.warning("sync failed: %s", exc)
        emojis = await app.db.pack_emojis(pack.id)
    if not emojis:
        await cq.answer("В паке пока пусто.", show_alert=True)
        return
    pages = (len(emojis) + EMOJI_PAGE - 1) // EMOJI_PAGE
    page = min(max(0, callback_data.arg), pages - 1)
    part = emojis[page * EMOJI_PAGE:(page + 1) * EMOJI_PAGE]
    rows = chunks([btn(_emoji_line(e, page * EMOJI_PAGE + i), EmojiCB(act="open", eid=e.id))
                   for i, e in enumerate(part)], 2)
    nav = []
    if page > 0:
        nav.append(btn("◀️", PackCB(act="emojis", pid=pack.id, arg=page - 1)))
    if pages > 1:
        nav.append(btn(f"{page + 1}/{pages}", PackCB(act="emojis", pid=pack.id, arg=page)))
    if page < pages - 1:
        nav.append(btn("▶️", PackCB(act="emojis", pid=pack.id, arg=page + 1)))
    rows.append(nav)
    rows.append([btn("⬅️ К паку", PackCB(act="open", pid=pack.id))])
    await safe_edit_text(cq.message, f"🗂 <b>{esc(pack.title)}</b>: {pack.k.count(len(emojis))}.\n"
                                     "Выберите, что перерисовать, переместить или удалить.", kb(*rows))
    await cq.answer()


async def _load_emoji(cq: CallbackQuery, app: App, me: User, eid: int) -> tuple[Pack, Emoji] | None:
    e = await app.db.get_emoji(eid)
    if not e:
        await cq.answer("Эмодзи не найден — возможно, его уже удалили.", show_alert=True)
        return None
    pack = await load_pack(cq, app, me, e.pack_id)
    return (pack, e) if pack else None


def _emoji_caption(e: Emoji, s: RenderSettings, created_by: str) -> str:
    lines = [f"<b>#{e.position + 1} {esc(e.label)}</b> {e.emoji}",
             ("🎬 анимированный" if e.animated else "🖼 статичный") + f" · {s.summary_ru()}",
             f"Источник: {'ник' if e.source == 'nick' else 'файл'}",
             f"Добавил: {created_by}"]
    return "\n".join(lines)


@router.callback_query(EmojiCB.filter(F.act == "open"))
async def cb_emoji(cq: CallbackQuery, callback_data: EmojiCB, bot: Bot, app: App, me: User):
    got = await _load_emoji(cq, app, me, callback_data.eid)
    if not got:
        return
    pack, e = got
    await cq.answer("⏳ Рисую превью…")
    s = RenderSettings.from_dict(e.settings)
    try:
        kind, data, name = await app.worker.run(job_result, app.store.load_bytes(e.skin_sha1), e.slim, s.to_dict(),
                                                 pack.k.size)
    except FileNotFoundError:
        kind, data, name = None, None, None
    page = e.position // EMOJI_PAGE
    markup = kb(
        [btn("🎨 Перерисовать", EmojiCB(act="redo", eid=e.id)), btn("🗑 Удалить", EmojiCB(act="del", eid=e.id))],
        [btn("⏫ Сделать первым (иконка пака)", EmojiCB(act="first", eid=e.id))] if e.position > 0 else [],
        [btn("⬅️ К списку", PackCB(act="emojis", pid=pack.id, arg=page))],
    )
    caption = _emoji_caption(e, s, await app.who(e.created_by))
    if data is None:
        await safe_edit_text(cq.message, caption + "\n\n⚠️ Файл скина не найден на сервере.", markup)
        return
    await show_media(app, bot, cq.message.chat.id, cq.message, kind, data, name, caption, markup)


@router.callback_query(EmojiCB.filter(F.act == "del"))
async def cb_emoji_del(cq: CallbackQuery, callback_data: EmojiCB, bot: Bot, app: App, me: User):
    got = await _load_emoji(cq, app, me, callback_data.eid)
    if not got:
        return
    pack, e = got
    await cq.answer()
    await cq.message.answer(
        f"Удалить «{esc(e.label)}» из пака «{esc(pack.title)}»?",
        reply_markup=kb([btn("🗑 Удалить", EmojiCB(act="delok", eid=e.id)),
                         btn("Отмена", PackCB(act="emojis", pid=pack.id, arg=e.position // EMOJI_PAGE))]),
    )
    await safe_delete(cq.message)


@router.callback_query(EmojiCB.filter(F.act == "delok"))
async def cb_emoji_delok(cq: CallbackQuery, callback_data: EmojiCB, app: App, me: User, admin: bool):
    got = await _load_emoji(cq, app, me, callback_data.eid)
    if not got:
        return
    pack, e = got
    async with app.stickers.lock(pack.id):
        try:
            if pack.tg_created:
                await app.stickers.delete(pack, e)
        except StickerError as exc:
            await cq.answer(f"Не получилось: {exc}", show_alert=True)
            return
        await app.db.delete_emoji(e.id)
        (app.cfg.thumbs_dir / f"{e.id}.png").unlink(missing_ok=True)
        await app.db.touch_pack(pack.id, me.id)
        await app.db.log(me.id, "emoji_delete", pack, details=e.label)
    await cq.answer("Удалено")
    pack = await app.db.get_pack(pack.id)
    if pack:
        await open_pack(cq.message, app, pack, me, admin)


@router.callback_query(EmojiCB.filter(F.act == "first"))
async def cb_emoji_first(cq: CallbackQuery, callback_data: EmojiCB, app: App, me: User, admin: bool):
    got = await _load_emoji(cq, app, me, callback_data.eid)
    if not got:
        return
    pack, e = got
    async with app.stickers.lock(pack.id):
        try:
            await app.stickers.move(pack, e, 0)
            await app.stickers.sync(pack)
        except StickerError as exc:
            await cq.answer(f"Не получилось: {exc}", show_alert=True)
            return
        await app.db.touch_pack(pack.id, me.id)
        await app.db.log(me.id, "emoji_move", pack, details=f"{e.label}: #{e.position + 1} → #1")
    await cq.answer("Теперь это первый в паке и его иконка")
    await open_pack(cq.message, app, pack, me, admin)


@router.callback_query(EmojiCB.filter(F.act == "redo"))
async def cb_emoji_redo(cq: CallbackQuery, callback_data: EmojiCB, state: FSMContext, app: App, me: User,
                        admin: bool):
    from .wizard import start_replace

    got = await _load_emoji(cq, app, me, callback_data.eid)
    if not got:
        return
    pack, e = got
    if not app.store.path(e.skin_sha1).exists():
        await cq.answer("Файл скина не найден на сервере.", show_alert=True)
        return
    await cq.answer()
    await start_replace(cq.message, state, app, me, admin, pack, e)


# ------------------------------------------------------------------ overview / history

@router.callback_query(PackCB.filter(F.act == "view"))
async def cb_view(cq: CallbackQuery, callback_data: PackCB, bot: Bot, app: App, me: User):
    pack = await load_pack(cq, app, me, callback_data.pid)
    if not pack:
        return
    await cq.answer("⏳ Собираю обзор…")
    try:
        emojis = await app.stickers.sync(pack)
    except Exception:  # noqa: BLE001
        emojis = await app.db.pack_emojis(pack.id)
    if not emojis:
        await cq.message.answer("В паке пока пусто.")
        return
    per = 48
    parts = chunks(emojis, per)
    for n, part in enumerate(parts):
        thumbs = []
        for e in part:
            p = app.cfg.thumbs_dir / f"{e.id}.png"
            thumbs.append((e.label, p.read_bytes() if p.exists() else None, e.animated))
        title = f"{pack.title}" + (f" ({n + 1}/{len(parts)})" if len(parts) > 1 else "")
        png = await app.worker.run(job_overview, thumbs, title)
        last = n == len(parts) - 1
        caption = f"🖼 <b>{esc(pack.title)}</b>: {pack.k.count(len(emojis))}" if last else None
        markup = kb([btn("🗂 Эмодзи", PackCB(act="emojis", pid=pack.id)),
                     btn("⬅️ К паку", PackCB(act="open", pid=pack.id))]) if last else None
        await send_media(bot, cq.message.chat.id, "photo", png, "overview.png", caption or "", markup)
    await safe_delete(cq.message)


@router.callback_query(PackCB.filter(F.act == "hist"))
async def cb_history(cq: CallbackQuery, callback_data: PackCB, app: App, me: User):
    pack = await load_pack(cq, app, me, callback_data.pid)
    if not pack:
        return
    total = await app.db.count_events(pack.id)
    pages = max(1, (total + HIST_PAGE - 1) // HIST_PAGE)
    page = min(max(0, callback_data.arg), pages - 1)
    events = await app.db.events(pack.id, HIST_PAGE, page * HIST_PAGE)
    lines = [f"📜 <b>История «{esc(pack.title)}»</b>", ""]
    for ev in events:
        what = ACTIONS.get(ev.action, ev.action)
        det = f": {esc(ev.details[:200])}" if ev.details else ""
        lines.append(f"<b>{app.time(ev.created_at)}</b> {await app.who(ev.user_id)} {what}{det}")
    if not events:
        lines.append("Пока пусто.")
    nav = []
    if page > 0:
        nav.append(btn("◀️", PackCB(act="hist", pid=pack.id, arg=page - 1)))
    if pages > 1:
        nav.append(btn(f"{page + 1}/{pages}", PackCB(act="hist", pid=pack.id, arg=page)))
    if page < pages - 1:
        nav.append(btn("▶️", PackCB(act="hist", pid=pack.id, arg=page + 1)))
    await safe_edit_text(cq.message, "\n".join(lines), kb(nav, [btn("⬅️ К паку", PackCB(act="open", pid=pack.id))]))
    await cq.answer()


@router.callback_query(F.data == "noop")
async def cb_noop(cq: CallbackQuery):
    await cq.answer()

