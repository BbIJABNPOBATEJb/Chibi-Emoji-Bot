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


def job_emoji(skin_png: bytes, slim: bool | None, settings: dict, size: int = 100,
              max_bytes: int | None = None) -> tuple[str, bytes, bytes]:
    """-> (sticker format 'static'|'video', file bytes, 100x100 PNG thumbnail).

    size is 100 for custom emoji and 512 for regular stickers; max_bytes overrides the
    video size cap (used to retry when Telegram still finds a video too big).
    """
    skin = load_skin(skin_png, slim)
    s = RenderSettings.from_dict(settings)
    res = render(skin, s, size)
    first = encode.png_bytes(res.frames[0])
    thumb = first if size == 100 else encode.png_bytes(_thumb(res.frames[0]))
    if res.animated:
        return "video", encode.webm_bytes(res.frames, res.fps, max_bytes), thumb
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
        self.replaced = 0  # pools thrown away after a crash or stall
        # animated example sheets need ~300 MB with ffmpeg: never run two at once
        self._heavy: asyncio.Semaphore | None = None

    def _new_pool(self) -> ProcessPoolExecutor:
        # No max_tasks_per_child: on Python 3.12 a worker that retires with work still queued is
        # not replaced, and the pool silently ends up with zero processes (every job hangs).
        return ProcessPoolExecutor(
            max_workers=self.workers,
            # "spawn" everywhere: forking the running bot (event loop + network threads) is fragile
            mp_context=multiprocessing.get_context("spawn"),
            initializer=_exit_with_parent,
        )

    def _replace(self, broken: ProcessPoolExecutor) -> None:
        if self.pool is broken:
            self.pool = self._new_pool()
            self.replaced += 1
            try:
                broken.shutdown(wait=False, cancel_futures=True)
            except Exception:  # noqa: BLE001
                pass

    def _stalled(self, pool: ProcessPoolExecutor) -> bool:
        """Work is queued but not a single render process is alive to do it."""
        pending = getattr(pool, "_pending_work_items", None) or {}
        procs = getattr(pool, "_processes", None) or {}
        return bool(pending) and not any(p.is_alive() for p in list(procs.values()))

    def _unstick(self, pool: ProcessPoolExecutor) -> None:
        """Replace a stalled pool; its waiting jobs fail with BrokenProcessPool and are retried."""
        mgr = getattr(pool, "_executor_manager_thread", None)
        if mgr is not None and hasattr(mgr, "terminate_broken"):
            # The executor's own teardown (Python 3.12): fails the waiting futures, kills the
            # workers and lets its helper threads finish — otherwise they would keep the
            # interpreter from exiting when the bot stops.
            try:
                mgr.terminate_broken(None)
                wakeup = getattr(pool, "_executor_manager_thread_wakeup", None)
                if wakeup is not None:
                    wakeup.wakeup()
                self._replace(pool)
                return
            except Exception:  # noqa: BLE001 - fall back to doing it by hand
                log.exception("terminate_broken failed, falling back")
        pending = list((getattr(pool, "_pending_work_items", None) or {}).values())
        # fail the waiting jobs *before* shutting the pool down: shutdown would cancel them,
        # and a cancellation would travel up into the Telegram handler instead of a retry
        for item in pending:
            fut = getattr(item, "future", None)
            if fut is not None and not fut.done():
                try:
                    fut.set_exception(BrokenProcessPool("render pool stalled"))
                except Exception:  # noqa: BLE001 - finished in the meantime
                    pass
        self._replace(pool)
        for p in list((getattr(pool, "_processes", None) or {}).values()):
            if p.is_alive():
                p.terminate()

    async def watchdog(self, interval: float = 30.0) -> None:
        """Safety net: a pool that stays without live processes while work waits gets replaced."""
        seen = None
        while True:
            await asyncio.sleep(interval)
            pool = self.pool
            if self._stalled(pool):
                if seen is pool:  # stalled on two checks in a row: not just a respawn in progress
                    log.error("render pool has work but no live processes; replacing it")
                    self._unstick(pool)
                    seen = None
                else:
                    seen = pool
            else:
                seen = None

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
