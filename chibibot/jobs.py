"""CPU-heavy work, run in a process pool so the bot keeps answering while it draws.

Everything here takes and returns plain bytes/dicts so it pickles cheaply.
"""
from __future__ import annotations

import asyncio
import logging
import multiprocessing
import os
import threading
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool

from .render import encode, previews
from .render.engine import render
from .render.options import RenderSettings
from .render.skin import load_skin

log = logging.getLogger(__name__)


def job_emoji(skin_png: bytes, slim: bool | None, settings: dict, size: int = 100) -> tuple[str, bytes, bytes]:
    """-> (sticker format 'static'|'video', file bytes, 100x100 PNG thumbnail).

    size is 100 for custom emoji and 512 for regular stickers.
    """
    skin = load_skin(skin_png, slim)
    s = RenderSettings.from_dict(settings)
    res = render(skin, s, size)
    first = encode.png_bytes(res.frames[0])
    thumb = first if size == 100 else encode.png_bytes(_thumb(res.frames[0]))
    if res.animated:
        return "video", encode.webm_bytes(res.frames, res.fps), thumb
    return "static", first, thumb


def _thumb(frame):
    from PIL import Image
    import numpy as np

    return np.array(Image.fromarray(frame, "RGBA").resize((100, 100), Image.LANCZOS))


def job_step(skin_png: bytes, slim: bool | None, settings: dict, step: str, size: int = 100) -> tuple[str, bytes, str]:
    skin = load_skin(skin_png, slim)
    return previews.step_example(skin, RenderSettings.from_dict(settings), step, size)


def job_result(skin_png: bytes, slim: bool | None, settings: dict, size: int = 100) -> tuple[str, bytes, str]:
    skin = load_skin(skin_png, slim)
    return previews.result_preview(skin, RenderSettings.from_dict(settings), size)


def job_overview(thumbs: list[tuple[str, bytes | None, bool]], title: str | None) -> bytes:
    return previews.overview_sheet(thumbs, title)


def job_detect(skin_png: bytes) -> bool:
    return load_skin(skin_png).slim


def _exit_with_parent() -> None:
    """Pool initializer: a render process quits as soon as the bot process is gone.

    Without it a force-killed bot leaves idle render processes behind on Windows.
    """
    parent = multiprocessing.parent_process()
    if parent is None:
        return

    def wait() -> None:
        parent.join()
        os._exit(0)

    threading.Thread(target=wait, daemon=True).start()


class WorkerCrashed(RuntimeError):
    """A render process died twice in a row on the same job."""


class Worker:
    """Process pool that survives a render process being killed (e.g. by the OOM killer).

    A plain ProcessPoolExecutor becomes permanently unusable once any of its processes
    dies; here the pool is replaced and the job retried once.
    """

    def __init__(self, workers: int):
        self.workers = max(1, workers)
        self.pool = self._new_pool()
        # animated example sheets need ~300 MB with ffmpeg: never run two at once
        self._heavy: asyncio.Semaphore | None = None

    def _new_pool(self) -> ProcessPoolExecutor:
        return ProcessPoolExecutor(
            max_workers=self.workers,
            # "spawn" everywhere: forking the running bot (event loop + network threads) is fragile
            mp_context=multiprocessing.get_context("spawn"),
            initializer=_exit_with_parent,
            # recycle processes now and then so memory fragmentation cannot build up
            max_tasks_per_child=200,
        )

    def _replace(self, broken: ProcessPoolExecutor) -> None:
        if self.pool is broken:
            self.pool = self._new_pool()
            try:
                broken.shutdown(wait=False, cancel_futures=True)
            except Exception:  # noqa: BLE001
                pass

    async def run(self, fn, *args, heavy: bool = False):
        if heavy:
            if self._heavy is None:
                self._heavy = asyncio.Semaphore(1)
            async with self._heavy:
                return await self._run(fn, *args)
        return await self._run(fn, *args)

    async def _run(self, fn, *args):
        loop = asyncio.get_running_loop()
        for _ in range(2):
            pool = self.pool
            try:
                return await loop.run_in_executor(pool, fn, *args)
            except BrokenProcessPool:
                log.error("render process died during %s (out of memory?); restarting the pool", fn.__name__)
                self._replace(pool)
        raise WorkerCrashed("процесс рисования аварийно завершился (похоже, серверу не хватило памяти) — "
                            "попробуйте ещё раз")

    def warm_up(self) -> None:
        # spawn the processes now instead of on the first user's click
        for f in [self.pool.submit(job_detect, _tiny_skin()) for _ in range(self.workers)]:
            f.result()

    def shutdown(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)


def _tiny_skin() -> bytes:
    from .render.skin import default_skin

    return default_skin().png
