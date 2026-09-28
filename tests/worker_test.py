"""The render pool must survive a render process being killed (e.g. by the OOM killer).

    python tests/worker_test.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chibibot.jobs import Worker, WorkerCrashed, job_detect  # noqa: E402
from chibibot.render.skin import default_skin  # noqa: E402


def die_once(marker: str) -> str:
    """Killed on the first call, fine on the retry — like a one-off OOM kill."""
    if not os.path.exists(marker):
        Path(marker).touch()
        os._exit(137)
    return "survived"


def busy(i: int) -> int:
    import time
    t = time.time()
    while time.time() - t < 0.05:
        pass
    return i


def always_die() -> None:
    os._exit(137)


async def main() -> None:
    w = Worker(2)
    skin = default_skin().png
    marker = os.path.join(tempfile.mkdtemp(), "died")

    assert await w.run(job_detect, skin) is False
    print("  ok   normal job")

    assert await w.run(die_once, marker) == "survived"
    print("  ok   killed process: pool replaced, job retried and succeeded")

    try:
        await w.run(always_die)
        raise AssertionError("expected WorkerCrashed")
    except WorkerCrashed as exc:
        print(f"  ok   job that always kills its process -> friendly error: {exc}")

    assert await w.run(job_detect, skin) is False
    print("  ok   pool still usable afterwards")

    results = await asyncio.gather(*(w.run(job_detect, skin) for _ in range(6)))
    assert results == [False] * 6
    print("  ok   parallel jobs after recovery")

    # Python 3.12 bug the bot hit in production: with max_tasks_per_child, workers that retire
    # while work is queued are not replaced and the pool silently ends up with no processes.
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor
    w.pool.shutdown(wait=False)
    w.pool = ProcessPoolExecutor(max_workers=2, mp_context=multiprocessing.get_context("spawn"),
                                 max_tasks_per_child=2)
    dog = asyncio.create_task(w.watchdog(interval=1.0))
    try:
        results = await asyncio.wait_for(asyncio.gather(*(w.run(busy, i) for i in range(20))), 60)
    finally:
        dog.cancel()
    assert sorted(results) == list(range(20)), results
    print("  ok   silently stalled pool (no live processes) is replaced by the watchdog, all 20 jobs done")
    w.shutdown()
    print("ALL OK")
    # the abandoned stalled pool can keep Python 3.12 waiting at exit (the bot exits hard too)
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    asyncio.run(main())
