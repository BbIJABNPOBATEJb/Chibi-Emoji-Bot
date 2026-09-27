"""Turning an emoji pack into a sticker pack (and back).

Telegram cannot change the type of an existing set, so conversion builds a new pack of the
other kind and redraws every item at the new size with its own stored skin and settings.
The source pack is left untouched.
"""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery

from ..db import Pack, User
from ..kinds import EMOJI, STICKERS, Kind, kind_of
from .app import App
from .packs import load_pack
from .ui import Menu, PackCB, btn, esc, kb, safe_edit_text
from .uploader import Item, render_and_upload

log = logging.getLogger(__name__)
router = Router(name="convert")


def target_kind(pack: Pack) -> Kind:
    return kind_of(STICKERS if pack.kind == EMOJI else EMOJI)


async def _items(app: App, pack: Pack):
    try:
        return await app.stickers.sync(pack)
    except Exception as exc:  # noqa: BLE001 - fall back to what the database knows
        log.warning("sync %s failed: %s", pack.name, exc)
        return await app.db.pack_emojis(pack.id)


async def _blocker(app: App, pack: Pack, me: User, admin: bool, n: int) -> str | None:
    """Why this conversion cannot run right now, if anything stops it."""
    if not admin:
        reason = await app.why_no_new_pack(pack.owner_id, await app.is_admin(pack.owner_id))
        if reason:
            return reason
    q = await app.quota(me.id, admin)
    if q.left is not None and q.left < n:
        if q.daily_limit < n:
            return (f"⏳ Для конвертации нужно перерисовать {n}, а дневной лимит — {q.daily_limit}. "
                    "Попросите администратора поднять лимит.")
        when = app.when(await app.quota_allows_at(me.id, n))
        return (f"⏳ Для конвертации нужно перерисовать {n}, а в дневной квоте осталось {q.left} из "
                f"{q.daily_limit}. Места хватит {when}.")
    return None


@router.callback_query(PackCB.filter(F.act == "conv"))
async def cb_convert(cq: CallbackQuery, callback_data: PackCB, app: App, me: User, admin: bool):
    pack = await load_pack(cq, app, me, callback_data.pid)
    if not pack:
        return
    items = await _items(app, pack)
    if not items:
        await cq.answer("Пак пустой — конвертировать нечего.", show_alert=True)
        return
    src, dst = pack.k, target_kind(pack)
    n = min(len(items), dst.max_items)
    lines = [
        f"🔁 <b>{src.icon} {src.name.capitalize()} → {dst.icon} {dst.name}</b>",
        "",
        f"Создам новый {dst.name} «{esc(pack.title)}» и перерисую в нём {src.count(n)} "
        f"в размере {dst.size}×{dst.size} — с теми же скинами и настройками, в том же порядке.",
        f"Исходный {src.name} останется как есть: удалите его сами, если он больше не нужен.",
    ]
    if len(items) > n:
        lines.append(f"⚠️ В {dst.name}е максимум {dst.max_items} — перенесу первые {n} из {len(items)}.")
    q = await app.quota(me.id, admin)
    if q.left is not None:
        lines.append(f"🎨 Спишется {n} из дневной квоты (осталось {q.left} из {q.daily_limit}).")
    reason = await _blocker(app, pack, me, admin, n)
    if reason:
        lines += ["", reason]
        markup = kb(btn("⬅️ К паку", PackCB(act="open", pid=pack.id)))
    else:
        markup = kb([btn(f"✅ Сделать {dst.name}", PackCB(act="convok", pid=pack.id)),
                     btn("Отмена", PackCB(act="open", pid=pack.id))])
    await safe_edit_text(cq.message, "\n".join(lines), markup)
    await cq.answer()


@router.callback_query(PackCB.filter(F.act == "convok"))
async def cb_convert_ok(cq: CallbackQuery, callback_data: PackCB, bot: Bot, app: App, me: User, admin: bool):
    if app.lock(me.id).locked():
        await cq.answer("⏳ Подождите, ещё идёт предыдущая загрузка.", show_alert=True)
        return
    async with app.lock(me.id):
        pack = await load_pack(cq, app, me, callback_data.pid)
        if not pack:
            return
        items = await _items(app, pack)
        dst = target_kind(pack)
        todo = items[:dst.max_items]
        if not todo:
            await cq.answer("Пак пустой — конвертировать нечего.", show_alert=True)
            return
        reason = await _blocker(app, pack, me, admin, len(todo))  # checked again: time has passed
        if reason:
            await cq.answer(reason, show_alert=True)
            return
        await cq.answer("Поехали!")
        new = await app.db.create_pack(pack.owner_id, await app.stickers.new_name(), pack.title, me.id,
                                       pack.settings, kind=dst.key)
        head = f"🔁 Делаю {dst.name} из «{esc(pack.title)}»"
        status = await safe_edit_text(cq.message, f"{head}: 0/{len(todo)}…")

        async def progress(n: int, of: int) -> None:
            if status is not None:
                await safe_edit_text(status, f"{head}: {n}/{of}…")

        res = await render_and_upload(
            app, new, [Item(e.label, e.source, e.skin_sha1, e.slim, e.settings, e.emoji) for e in todo],
            me.id, progress)

        if not res.ok:
            await app.db.delete_pack(new.id)  # nothing made it into Telegram: no empty leftover
            lines = ["😔 Не удалось сконвертировать ни одной картинки."]
            markup = kb(btn("📦 К паку", PackCB(act="open", pid=pack.id)))
        else:
            await app.db.log(me.id, "pack_convert", new, details=f"из {pack.k.name}а «{pack.title}»: {len(res.ok)} шт.")
            await app.db.log(me.id, "pack_convert", pack, details=f"→ {dst.name} ({len(res.ok)} шт.)")
            new = await app.db.get_pack(new.id) or new
            lines = [f"✅ Готово! {dst.icon} {dst.name.capitalize()} «<b>{esc(new.title)}</b>»: "
                     f"{dst.count(len(res.ok))}."]
            if new.tg_created:
                lines.append(f"🔗 <a href=\"{new.link}\">t.me/{dst.link_prefix}/{new.name}</a> — откройте, "
                             "чтобы добавить пак.")
            lines.append(f"Исходный {pack.k.name} «{esc(pack.title)}» остался без изменений.")
            rows = []
            if new.tg_created:
                rows.append([btn("🔗 Открыть в Telegram", url=new.link)])
            rows.append([btn(f"{dst.icon} К новому паку", PackCB(act="open", pid=new.id)),
                         btn(f"{pack.k.icon} К исходному", PackCB(act="open", pid=pack.id))])
            markup = kb(*rows)
        if res.failed:
            lines.append(f"⚠️ Ошибки ({len(res.failed)}):")
            lines += [f"• {esc(f)}" for f in res.failed[:15]]
        q = await app.quota(me.id, admin)
        if q.left is not None:
            lines.append(f"🎨 Осталось на сегодня: {q.left} из {q.daily_limit}.")
        await bot.send_message(cq.message.chat.id, "\n".join(lines), reply_markup=markup)
        if status is not None:
            await safe_edit_text(status, f"{head}: готово.", kb(btn("🏠 Меню", Menu(act="home"))))
