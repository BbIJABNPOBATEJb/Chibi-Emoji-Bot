"""Peak memory of the heaviest jobs (Linux only: uses resource.getrusage).

    python tests/memory_check.py
"""
import resource
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chibibot.jobs import job_emoji, job_result, job_step  # noqa: E402
from chibibot.render.options import RenderSettings  # noqa: E402

skin = (Path(__file__).parent / "skins" / "Grian.png").read_bytes()
cases = [
    ("anim sheet", lambda: job_step(skin, True, RenderSettings().to_dict(), "anim")),
    ("anim sheet, Live", lambda: job_step(skin, True, RenderSettings(style="live").to_dict(), "anim")),
    ("camera sheet, animated", lambda: job_step(skin, True, RenderSettings(anim="dance").to_dict(), "cam")),
    ("HD animated preview", lambda: job_result(skin, True, RenderSettings(anim="spin", mode="hd").to_dict())),
    ("HD animated emoji", lambda: job_emoji(skin, True, RenderSettings(anim="death", mode="hd").to_dict())),
    ("pixel animated sticker 512", lambda: job_emoji(skin, True, RenderSettings(anim="dance").to_dict(), 512)),
    ("HD animated sticker 512, Live", lambda: job_emoji(skin, True, RenderSettings(style="live", anim="death",
                                                                                   mode="hd").to_dict(), 512)),
]
for name, fn in cases:
    t = time.time()
    fn()
    me = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024
    kids = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss // 1024
    print(f"{name:26s} {time.time() - t:5.1f}s   peak python {me} MB, peak ffmpeg {kids} MB")
