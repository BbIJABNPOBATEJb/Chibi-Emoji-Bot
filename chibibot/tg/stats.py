"""Admin dashboards: activity/growth charts and a carousel of the packs that grew the most lately."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from .. import stats
from ..render import charts, previews
from .app import App
from .media import show_media
from .ui import AdminCB, Menu, PackCB, btn, esc, kb, safe_edit_text

router = Router(name="stats")

DEFAULT_PERIOD = 1                  # a week
FRESH_PERIODS = [0, 1, 2]           # 24 h, week, month (indexes into stats.PERIODS)
FRESH_LIMIT = 20
SHEET_ITEMS = 24
MEDALS = ["🥇", "🥈", "🥉"]


async def _deny(cq: CallbackQuery) -> None:
    await cq.answer("Только для администраторов.", show_alert=True)


def _stats_kb(metric: int, period: int):
    ms = [btn(("• " if i == metric else "") + m.button, AdminCB(act="st", page=i, arg=period))
          for i, m in enumerate(stats.METRICS)]
    ps = [btn(("• " if i == period else "") + p.button, AdminCB(act="st", page=metric, arg=i))
          for i, p in enumerate(stats.PERIODS)]
    return kb(ms[:3], ms[3:], ps,
              [btn("🔥 Свежие паки", AdminCB(act="fresh", arg=DEFAULT_PERIOD)), btn("🏠 Меню", Menu(act="home"))])


async def show_stats(app: App, bot: Bot, chat_id: int, old: Message | None, metric: int = 0,
                     period: int = DEFAULT_PERIOD) -> None:
    metric = max(0, min(metric, len(stats.METRICS) - 1))
    period = max(0, min(period, len(stats.PERIODS) - 1))
    spec, caption = await stats.build(app.db, app.tz, metric, period)
    # a fraction of a second of drawing: a thread, so it never waits behind skin renders in the pool
    png = await asyncio.to_thread(charts.render, spec)
    await show_media(app, bot, chat_id, old, "photo", png, "stats.png", caption, _stats_kb(metric, period))


@router.message(Command("stats"))
async def cmd_stats(message: Message, bot: Bot, app: App, admin: bool):
    if admin:
        await show_stats(app, bot, message.chat.id, None)


@router.callback_query(AdminCB.filter(F.act == "st"))
async def cb_stats(cq: CallbackQuery, callback_data: AdminCB, bot: Bot, app: App, admin: bool):
    if not admin:
        return await _deny(cq)
    await cq.answer()
    await show_stats(app, bot, cq.message.chat.id, cq.message, callback_data.page, callback_data.arg)


# ------------------------------------------------------------------ fresh packs

def _since(period: stats.Period) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=period.hours or 24 * 7)).isoformat(timespec="seconds")


def _period_row(act: str, period: int) -> list:
    return [btn(("• " if i == period else "") + stats.PERIODS[i].button, AdminCB(act=act, arg=i))
            for i in FRESH_PERIODS]


async def show_fresh(app: App, bot: Bot, chat_id: int, old: Message | None, period: int, rank: int) -> None:
    period = period if period in FRESH_PERIODS else DEFAULT_PERIOD
    p = stats.PERIODS[period]
    since = _since(p)
    admins = set(await app.db.admin_ids()) | app.cfg.admin_ids
    top = await app.db.fresh_packs(since, admins, FRESH_LIMIT)
    bottom = [btn("📊 Статистика", AdminCB(act="st", arg=period)), btn("🏠 Меню", Menu(act="home"))]
    if not top:
        text = (f"🔥 <b>Свежие паки {p.title}</b>\n\nНичего нового: игроки не добавляли эмодзи и стикеры "
                f"в опубликованные паки. Попробуйте период побольше.")
        markup = kb(_period_row("fresh", period), bottom)
        if old is not None:
            await safe_edit_text(old, text, markup)
        else:
            await bot.send_message(chat_id, text, reply_markup=markup)
        return
    rank = max(0, min(rank, len(top) - 1))
    pack, added = top[rank]
    items = await app.db.pack_items_since(pack.id, since, SHEET_ITEMS)
    thumbs = []
    for e in items:
        path = app.cfg.thumbs_dir / f"{e.id}.png"
        thumbs.append((e.label, path.read_bytes() if path.exists() else None, e.animated))
    sheet = await asyncio.to_thread(previews.overview_sheet, thumbs, f"{pack.title} · +{added}")

    place = MEDALS[rank] if rank < len(MEDALS) else f"#{rank + 1}"
    lines = [
        f"🔥 <b>Свежие паки {p.title}</b> · {rank + 1}/{len(top)}",
        "",
        f"{place} <b>{esc(pack.title)}</b> — {pack.k.icon} {pack.k.name}",
        f"➕ {pack.k.count(added)} {p.title} · всего в паке {pack.k.count(pack.count)}",
        f"👤 {await app.who(pack.owner_id)} · пак создан {app.time(pack.created_at)}",
        f"🔗 <a href=\"{pack.link}\">t.me/{pack.k.link_prefix}/{pack.name}</a>",
    ]
    creators = await app.db.top_creators(since, admins, 3)
    if creators:
        names = [f"{await app.who(uid)} ({n})" for uid, n in creators]
        lines += ["", "🏆 Самые активные авторы: " + ", ".join(names)]
    if len(items) < added:
        lines.append(f"<i>На картинке первые {len(items)}.</i>")

    nav = []
    if rank > 0:
        nav.append(btn("◀️", AdminCB(act="fresh", page=rank - 1, arg=period)))
    nav.append(btn(f"{rank + 1}/{len(top)}", AdminCB(act="fresh", page=rank, arg=period)))
    if rank < len(top) - 1:
        nav.append(btn("▶️", AdminCB(act="fresh", page=rank + 1, arg=period)))
    markup = kb(nav,
                [btn("🔗 Открыть в Telegram", url=pack.link), btn("📦 Карточка пака", PackCB(act="open", pid=pack.id))],
                _period_row("fresh", period), bottom)
    await show_media(app, bot, chat_id, old, "photo", sheet, "fresh.png", "\n".join(lines), markup)


@router.message(Command("fresh"))
async def cmd_fresh(message: Message, bot: Bot, app: App, admin: bool):
    if admin:
        await show_fresh(app, bot, message.chat.id, None, DEFAULT_PERIOD, 0)


@router.callback_query(AdminCB.filter(F.act == "fresh"))
async def cb_fresh(cq: CallbackQuery, callback_data: AdminCB, bot: Bot, app: App, admin: bool):
    if not admin:
        return await _deny(cq)
    await cq.answer()
    await show_fresh(app, bot, cq.message.chat.id, cq.message, callback_data.arg, callback_data.page)
