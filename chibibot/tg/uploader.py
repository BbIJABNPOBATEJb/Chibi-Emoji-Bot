"""Render skins and put the results into a pack — shared by the wizard and pack conversion."""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest

from ..db import Pack
from ..jobs import WorkerCrashed, job_emoji
from ..render.encode import EncodeError
from ..render.engine import RenderError
from ..stickers import StickerError, explain
from .app import App
from .ui import PackCB, btn, esc, kb

log = logging.getLogger(__name__)


@dataclass
class Item:
    label: str
    source: str
    sha1: str
    slim: bool | None
    settings: dict
    emoji: str
    keyword: str | None = None   # search keyword; defaults to the label


@dataclass
class Outcome:
    ok: list[str]
    failed: list[str]


def why(exc: Exception) -> str:
    """A short, user-facing reason for a failed item."""
    if isinstance(exc, (StickerError, RenderError, EncodeError, WorkerCrashed)):
        return str(exc)
    if isinstance(exc, TelegramBadRequest):
        return explain(exc)
    return exc.__class__.__name__


JOB_PREFIX = "job:"


async def render_and_upload(app: App, pack: Pack, items: list[Item], actor_id: int,
                            progress: Callable[[int, int], Awaitable[None]] | None = None,
                            chat_id: int | None = None) -> Outcome:
    """Render every item at the pack's size and add it to the Telegram set, in order.

    Rendering runs in parallel in the process pool while uploads go one by one, so the
    set keeps the queue order. Every uploaded item counts towards the actor's daily quota.

    With chat_id the upload is written down while it runs: if the bot is restarted in the
    middle, that chat is told on the next start instead of staring at a frozen counter.
    """
    key = f"{JOB_PREFIX}{pack.id}:{actor_id}"
    if chat_id is not None and items:
        await app.db.kv_set(key, json.dumps({"chat": chat_id, "pack": pack.id, "title": pack.title,
                                             "before": pack.count, "total": len(items)}))
    try:
        res = await _render_and_upload(app, pack, items, actor_id, progress)
    except Exception:
        await app.db.kv_del(key)
        raise
    # not in a `finally`: a cancellation means the bot is stopping, and the note must survive it
    await app.db.kv_del(key)
    return res


async def announce_interrupted(app: App, bot: Bot) -> int:
    """Tell the chats whose upload was cut short by a restart; returns how many were told."""
    told = 0
    for key, raw in await app.db.kv_prefix(JOB_PREFIX):
        await app.db.kv_del(key)
        try:
            job = json.loads(raw)
            pack = await app.db.get_pack(int(job["pack"]))
            total = int(job["total"])
            lines = [f"⚠️ Бот перезапускался, и загрузка в «<b>{esc(str(job['title']))}</b>» прервалась."]
            if pack:
                done = max(0, min(total, pack.count - int(job["before"])))
                lines.append(f"Успело добавиться {done} из {total} — они уже в паке. "
                             "Остальное можно добавить заново.")
                markup = kb([btn("➕ Добавить ещё", PackCB(act="add", pid=pack.id)),
                             btn("📦 К паку", PackCB(act="open", pid=pack.id))])
            else:
                lines.append("Попробуйте ещё раз: /menu")
                markup = None
            await bot.send_message(int(job["chat"]), "\n".join(lines), reply_markup=markup)
            told += 1
        except (TelegramAPIError, KeyError, TypeError, ValueError) as exc:
            log.warning("interrupted upload %s not announced: %r", key, exc)
    return told


async def _render_and_upload(app: App, pack: Pack, items: list[Item], actor_id: int,
                             progress: Callable[[int, int], Awaitable[None]] | None = None) -> Outcome:
    loop = asyncio.get_running_loop()
    futures: list[asyncio.Future | None] = []
    for it in items:
        try:
            skin = app.store.load_bytes(it.sha1)
        except FileNotFoundError:
            futures.append(None)
            continue
        futures.append(asyncio.ensure_future(app.worker.run(job_emoji, skin, it.slim, it.settings, pack.k.size)))
    ok: list[str] = []
    failed: list[str] = []
    streak = 0
    last = 0.0
    total = len(items)
    try:
        for n, (it, fut) in enumerate(zip(items, futures), 1):
            try:
                if fut is None:
                    raise StickerError("файл скина потерялся")
                fmt, blob, thumb = await fut
                try:
                    st = await app.stickers.add(pack, fmt, blob, it.emoji, [it.keyword or it.label])
                except StickerError as exc:
                    if fmt != "video" or "тяжёлым" not in str(exc):
                        raise
                    # Telegram's real cap can be below what it documents: re-encode at half the size
                    fmt, blob, thumb = await app.worker.run(job_emoji, app.store.load_bytes(it.sha1), it.slim,
                                                            it.settings, pack.k.size, len(blob) // 2)
                    st = await app.stickers.add(pack, fmt, blob, it.emoji, [it.keyword or it.label])
                e = await app.db.add_emoji(pack.id, it.label, it.source, it.sha1, it.slim, it.settings,
                                           fmt == "video", it.emoji, st.file_id, st.file_unique_id,
                                           st.custom_emoji_id, actor_id)
                (app.cfg.thumbs_dir / f"{e.id}.png").write_bytes(thumb)
                await app.db.add_usage(actor_id, pack.id, "add")
                ok.append(it.label)
                streak = 0
            except Exception as exc:  # noqa: BLE001 - report per item and keep going
                log.warning("item %s failed: %s", it.label, exc)
                failed.append(f"{it.label}: {why(exc)}")
                streak += 1
                if streak >= 3 and not ok:
                    failed.append("остановился после трёх ошибок подряд")
                    break
            if progress and (loop.time() - last > 1.5 or n == total):
                last = loop.time()
                await progress(n, total)
    finally:
        for fut in futures:
            if fut is not None and not fut.done():
                fut.cancel()
    return Outcome(ok, failed)
