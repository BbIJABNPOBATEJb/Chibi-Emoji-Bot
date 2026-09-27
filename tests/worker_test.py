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
    w.shutdown()
    print("ALL OK")


if __name__ == "__main__":
    asyncio.run(main())
