"""How long username -> skin lookups take from this machine.

    python tests/fetch_timing.py
"""
import asyncio
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.WARNING, format="%(message)s")

from chibibot.sources import MojangClient  # noqa: E402

NAMES = ["Notch", "jeb_", "Dinnerbone", "Grian", "Dream", "Technoblade", "Not", "a"]


async def main():
    c = MojangClient()
    t0 = time.time()
    for n in NAMES[:3]:
        t = time.time()
        try:
            real, png, slim = await c.fetch(n)
            print(f"  sequential {n:12s} {time.time() - t:5.2f}s  ok ({len(png)} B)")
        except Exception as exc:  # noqa: BLE001
            print(f"  sequential {n:12s} {time.time() - t:5.2f}s  {exc!r}")
    c._cache.clear()  # noqa: SLF001
    t = time.time()
    res = await asyncio.gather(*(c.fetch(n) for n in NAMES), return_exceptions=True)
    bad = [f"{n}: {r!r}" for n, r in zip(NAMES, res) if isinstance(r, Exception)]
    print(f"  parallel {len(NAMES)} names {time.time() - t:5.2f}s, failed: {bad}")
    await c.close()
    print(f"total {time.time() - t0:.1f}s")


asyncio.run(main())
