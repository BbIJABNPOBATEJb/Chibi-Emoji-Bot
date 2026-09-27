"""Admin tools: every pack with who/when, the global audit log, users, admin rights."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from ..db import User, ago
from .app import App
from .packs import ACTIONS
from .ui import AdminCB, Menu, PackCB, btn, esc, kb, pack_label, safe_edit_text

router = Router(name="admin")

PACK_PAGE = 10
LOG_PAGE = 15
USER_PAGE = 15


def _pager(act: str, page: int, pages: int, arg: int = 0) -> list:
    nav = []
    if page > 0:
        nav.append(btn("◀️", AdminCB(act=act, page=page - 1, arg=arg)))
    if pages > 1:
        nav.append(btn(f"{page + 1}/{pages}", AdminCB(act=act, page=page, arg=arg)))
    if page < pages - 1:
        nav.append(btn("▶️", AdminCB(act=act, page=page + 1, arg=arg)))
    return nav


async def _deny(cq: CallbackQuery) -> None:
    await cq.answer("Только для администраторов.", show_alert=True)


@router.callback_query(AdminCB.filter(F.act == "packs"))
async def cb_all_packs(cq: CallbackQuery, callback_data: AdminCB, state: FSMContext, app: App, admin: bool):
    if not admin:
        return await _deny(cq)
    await state.clear()
    total = await app.db.count_packs()
    pages = max(1, (total + PACK_PAGE - 1) // PACK_PAGE)
    page = min(max(0, callback_data.page), pages - 1)
    packs = await app.db.all_packs(PACK_PAGE, page * PACK_PAGE)
    lines = [f"👑 <b>Все паки</b> ({total}), свежие изменения сверху:", ""]
    rows = []
    for p in packs:
        lines.append(
            f"{'🟢' if p.tg_created else '⚪️'}{p.k.icon} <b>{esc(p.title)}</b> · {p.k.count(p.count)}\n"
            f"    👤 {await app.who(p.owner_id)} · 🆕 {app.time(p.created_at)} ({await app.who(p.created_by)})\n"
            f"    ✏️ {app.time(p.updated_at)} ({await app.who(p.updated_by)})"
        )
        rows.append([btn(pack_label(p), PackCB(act="open", pid=p.id))])
    if not packs:
        lines.append("Паков пока нет.")
    rows.append(_pager("packs", page, pages))
    rows.append([btn("🏠 Меню", Menu(act="home"))])
    await safe_edit_text(cq.message, "\n".join(lines), kb(*rows))
    await cq.answer()


@router.callback_query(AdminCB.filter(F.act == "log"))
async def cb_log(cq: CallbackQuery, callback_data: AdminCB, app: App, admin: bool):
    if not admin:
        return await _deny(cq)
    total = await app.db.count_events()
    pages = max(1, (total + LOG_PAGE - 1) // LOG_PAGE)
    page = min(max(0, callback_data.page), pages - 1)
    events = await app.db.events(None, LOG_PAGE, page * LOG_PAGE)
    lines = ["📜 <b>Журнал действий</b>", ""]
    for ev in events:
        what = ACTIONS.get(ev.action, ev.action)
        where = f" «{esc(ev.pack_title)}»" if ev.pack_title else ""
        det = f": {esc(ev.details[:160])}" if ev.details else ""
        lines.append(f"<b>{app.time(ev.created_at)}</b> {await app.who(ev.user_id)} {what}{where}{det}")
    if not events:
        lines.append("Пока пусто.")
    await safe_edit_text(cq.message, "\n".join(lines),
                         kb(_pager("log", page, pages), [btn("🏠 Меню", Menu(act="home"))]))
    await cq.answer()


@router.callback_query(AdminCB.filter(F.act == "users"))
async def cb_users(cq: CallbackQuery, callback_data: AdminCB, app: App, admin: bool):
    if not admin:
        return await _deny(cq)
    total = await app.db.count_users()
    pages = max(1, (total + USER_PAGE - 1) // USER_PAGE)
    page = min(max(0, callback_data.page), pages - 1)
    users = await app.db.list_users(USER_PAGE, page * USER_PAGE)
    lines = [f"👥 <b>Пользователи</b> ({total}), недавние сверху:", ""]
    rows = []
    since = ago(24)
    for u, packs in users:
        is_adm = u.is_admin or u.id in app.cfg.admin_ids
        crown = "👑 " if is_adm else ""
        today = await app.db.usage_since(u.id, since)
        personal = " · ⚙️ личные лимиты" if (u.pack_limit is not None or u.daily_limit is not None) and not is_adm else ""
        lines.append(f"{crown}{esc(u.display)} <code>{u.id}</code> · паков: {packs} · за 24 ч: {today}"
                     f"{personal} · был {app.time(u.last_seen)}")
        rows.append([btn(f"{'👑' if is_adm else '👤'} {u.display} · {packs}", AdminCB(act="user", arg=u.id))])
    rows.append(_pager("users", page, pages))
    rows.append([btn("🏠 Меню", Menu(act="home"))])
    await safe_edit_text(cq.message, "\n".join(lines), kb(*rows))
    await cq.answer()


@router.callback_query(AdminCB.filter(F.act == "user"))
async def cb_user_packs(cq: CallbackQuery, callback_data: AdminCB, app: App, admin: bool):
    if not admin:
        return await _deny(cq)
    uid = callback_data.arg
    packs = await app.db.user_packs(uid)
    lines = [await limits_card(app, uid), "",
             f"Изменить: <code>/setlimit {uid} паки эмодзи_в_сутки</code> (0 — без лимита, - — по умолчанию)", ""]
    if not packs:
        lines.append("Паков нет.")
    for p in packs:
        lines.append(f"{'🟢' if p.tg_created else '⚪️'}{p.k.icon} <b>{esc(p.title)}</b> · {p.k.count(p.count)} · "
                     f"изменён {app.time(p.updated_at)} ({await app.who(p.updated_by)})")
    rows = [[btn(pack_label(p), PackCB(act="open", pid=p.id))] for p in packs]
    rows.append([btn("⬅️ Пользователи", AdminCB(act="users")), btn("🏠 Меню", Menu(act="home"))])
    await safe_edit_text(cq.message, "\n".join(lines), kb(*rows))
    await cq.answer()


# ------------------------------------------------------------------ commands

@router.message(Command("admins"))
async def cmd_admins(message: Message, app: App, admin: bool):
    if not admin:
        return
    ids = sorted(set(await app.db.admin_ids()) | app.cfg.admin_ids)
    lines = ["👑 <b>Администраторы</b>:"]
    for i in ids:
        src = " (из .env)" if i in app.cfg.admin_ids else ""
        lines.append(f"• {await app.who(i)} <code>{i}</code>{src}")
    lines.append("\n/addadmin ID — назначить, /deladmin ID — снять, /setlimit ID — лимиты игрока.")
    await message.answer("\n".join(lines))


@router.message(Command("addadmin"))
async def cmd_addadmin(message: Message, command: CommandObject, app: App, me: User, admin: bool):
    if not admin:
        return
    arg = (command.args or "").strip()
    if not arg.lstrip("-").isdigit():
        await message.answer("Использование: /addadmin 123456789 (ID можно узнать командой /id)")
        return
    uid = int(arg)
    await app.db.set_admin(uid, True)
    await app.db.log(me.id, "admin_add", details=f"id {uid}")
    await message.answer(f"👑 <code>{uid}</code> теперь администратор.")


@router.message(Command("deladmin"))
async def cmd_deladmin(message: Message, command: CommandObject, app: App, me: User, admin: bool):
    if not admin:
        return
    arg = (command.args or "").strip()
    if not arg.lstrip("-").isdigit():
        await message.answer("Использование: /deladmin 123456789")
        return
    uid = int(arg)
    if uid in app.cfg.admin_ids:
        await message.answer("Этот администратор задан в .env (ADMIN_IDS) — уберите его оттуда.")
        return
    await app.db.set_admin(uid, False)
    await app.db.log(me.id, "admin_remove", details=f"id {uid}")
    await message.answer(f"👤 <code>{uid}</code> больше не администратор.")


def _limit_value(raw: str) -> int | None:
    """'-' -> None (default from .env), '0' -> 0 (unlimited), 'N' -> N."""
    if raw in ("-", "default", "по-умолчанию"):
        return None
    return max(0, int(raw))


def _fmt_limit(personal: int | None, default: int) -> str:
    if personal is None:
        return f"{default or 'без лимита'} (по умолчанию)"
    return "без лимита (личный)" if personal == 0 else f"{personal} (личный)"


async def limits_card(app: App, uid: int) -> str:
    u = await app.db.get_user(uid)
    name = esc(u.display) if u else f"id{uid}"
    if await app.is_admin(uid):
        return f"👑 {name} <code>{uid}</code> — администратор, лимитов нет."
    q = await app.quota(uid, False)
    return (f"👤 {name} <code>{uid}</code>\n"
            f"📦 Паков: {_fmt_limit(u.pack_limit if u else None, app.cfg.max_packs_per_user)}\n"
            f"😀 Эмодзи за 24 ч: {_fmt_limit(u.daily_limit if u else None, app.cfg.daily_emoji_limit)}\n\n"
            + app.limits_text(q))


@router.message(Command("setlimit"))
async def cmd_setlimit(message: Message, command: CommandObject, app: App, me: User, admin: bool):
    if not admin:
        return
    args = (command.args or "").split()
    usage = ("Использование:\n"
             "<code>/setlimit ID</code> — показать лимиты игрока\n"
             "<code>/setlimit ID паки эмодзи_в_сутки</code> — задать личные лимиты\n"
             "Число, <code>0</code> — без лимита, <code>-</code> — как в .env. Пример: <code>/setlimit 123456789 10 300</code>")
    if not args or not args[0].isdigit():
        await message.answer(usage)
        return
    uid = int(args[0])
    if len(args) == 1:
        await message.answer(await limits_card(app, uid))
        return
    existing = await app.db.get_user(uid)
    try:
        packs = _limit_value(args[1])
        daily = _limit_value(args[2]) if len(args) > 2 else (existing.daily_limit if existing else None)
    except ValueError:
        await message.answer(usage)
        return
    await app.db.set_limits(uid, packs, daily)
    await app.db.log(me.id, "limits_set", details=f"id {uid}: паки {args[1]}, эмодзи {args[2] if len(args) > 2 else '='}")
    await message.answer("✅ Сохранено.\n\n" + await limits_card(app, uid))


@router.message(Command("stats"))
async def cmd_stats(message: Message, app: App, admin: bool):
    if not admin:
        return
    st = await app.db.stats()
    await message.answer(f"📊 Пользователей: {st['users']}\nПаков: {st['packs']}\n"
                         f"Эмодзи: {st['emojis']} (анимированных: {st['animated']})")
