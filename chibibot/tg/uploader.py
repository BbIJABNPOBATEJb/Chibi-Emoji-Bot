"""Render skins and put the results into a pack — shared by the wizard and pack conversion."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

from aiogram.exceptions import TelegramBadRequest

from ..db import Pack
from ..jobs import WorkerCrashed, job_emoji
from ..render.encode import EncodeError
from ..render.engine import RenderError
from ..stickers import StickerError, explain
from .app import App

log = logging.getLogger(__name__)


@dataclass
class Item:
    label: str
    source: str
    sha1: str
    slim: bool | None
    settings: dict
    emoji: str


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


async def render_and_upload(app: App, pack: Pack, items: list[Item], actor_id: int,
                            progress: Callable[[int, int], Awaitable[None]] | None = None) -> Outcome:
    """Render every item at the pack's size and add it to the Telegram set, in order.

    Rendering runs in parallel in the process pool while uploads go one by one, so the
    set keeps the queue order. Every uploaded item counts towards the actor's daily quota.
    """
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
                st = await app.stickers.add(pack, fmt, blob, it.emoji, [it.label])
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
