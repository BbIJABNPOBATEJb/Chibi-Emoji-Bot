"""Turning rendered frames into files Telegram accepts.

* static custom emoji: 100x100 PNG
* animated custom emoji: 100x100 WEBM, VP9 with alpha, <= 3 s, <= 30 fps, <= 256 KB, no audio
* chat previews: PNG / H.264 MP4 (sent as an animation), GIF when ffmpeg is missing
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
from functools import lru_cache
from typing import Callable, Iterable

import numpy as np
from PIL import Image

MAX_WEBM_BYTES = 256 * 1024


class EncodeError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def ffmpeg_path() -> str | None:
    env = os.environ.get("FFMPEG_PATH")
    if env and os.path.exists(env):
        return env
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:  # optional pip package that ships a static ffmpeg build
        import imageio_ffmpeg  # type: ignore

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return None


def png_bytes(frame: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(frame, "RGBA").save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _bleed(frame: np.ndarray, steps: int = 3) -> np.ndarray:
    """Give transparent pixels the colour of their opaque neighbours.

    VP9 stores colour at half resolution; without this the edges pick up a dark fringe
    from the black behind the alpha.
    """
    out = frame.copy()
    known = out[..., 3] > 0
    for _ in range(steps):
        if known.all():
            break
        acc = np.zeros(out.shape[:2] + (3,), np.float32)
        cnt = np.zeros(out.shape[:2], np.float32)
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            sk = np.roll(known, (dy, dx), (0, 1))
            sc = np.roll(out[..., :3], (dy, dx), (0, 1)).astype(np.float32)
            acc += sc * sk[..., None]
            cnt += sk
        fill = (~known) & (cnt > 0)
        out[fill, :3] = (acc[fill] / cnt[fill, None]).astype(np.uint8)
        known = known | fill
    return out


def _ffmpeg(args: list[str], chunks: Iterable[bytes], out_suffix: str) -> bytes:
    """Run ffmpeg with raw video streamed to stdin chunk by chunk; return the output file.

    Streaming keeps memory flat: holding a whole clip as one bytes object doubled the peak.
    stderr goes to a file, not a pipe: a full pipe could block ffmpeg while we write frames.
    """
    exe = ffmpeg_path()
    if not exe:
        raise EncodeError("ffmpeg не найден — анимации недоступны")
    fd, path = tempfile.mkstemp(suffix=out_suffix)
    os.close(fd)
    with tempfile.TemporaryFile() as errfile:
        try:
            proc = subprocess.Popen([exe, "-hide_banner", "-loglevel", "error", "-y", *args, path],
                                    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=errfile)
            try:
                for chunk in chunks:
                    proc.stdin.write(chunk)
            except BrokenPipeError:
                pass  # ffmpeg died early: its exit code and stderr tell why
            finally:
                try:
                    proc.stdin.close()
                except BrokenPipeError:
                    pass
            try:
                code = proc.wait(timeout=120)
            except subprocess.TimeoutExpired:
                proc.kill()
                raise EncodeError("ffmpeg не уложился в 2 минуты")
            if code != 0:
                errfile.seek(0)
                raise EncodeError(errfile.read().decode("utf-8", "replace")[-500:] or f"ffmpeg exit {code}")
            with open(path, "rb") as fh:
                return fh.read()
        finally:
            try:
                os.remove(path)
            except OSError:
                pass


def webm_bytes(frames: list[np.ndarray], fps: int) -> bytes:
    """RGBA frames -> VP9 WEBM with alpha within Telegram's 256 KB (quality steps down if needed)."""
    h, w = frames[0].shape[:2]
    # bigger frames (512 px stickers) take a faster, slightly less thorough encoder setting
    speed = "2" if max(w, h) <= 128 else "4"
    bled = [_bleed(f) for f in frames]
    for crf in (30, 38, 46, 54, 63):
        data = _ffmpeg(
            ["-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{w}x{h}", "-framerate", str(fps), "-i", "pipe:0",
             "-an", "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0", "-crf", str(crf),
             "-deadline", "good", "-cpu-used", speed, "-row-mt", "1", "-threads", "2", "-auto-alt-ref", "0",
             "-t", "3", "-f", "webm"],
            (f.tobytes() for f in bled), ".webm",
        )
        if len(data) <= MAX_WEBM_BYTES:
            return data
    raise EncodeError("анимация получилась слишком большой для Telegram")


def mp4_bytes(frames: Iterable[np.ndarray], fps: int) -> bytes:
    """RGB frames (already composited on a background) -> H.264 MP4, streamed frame by frame."""
    it = iter(frames)
    first = next(it)
    h, w = first.shape[:2]
    pad_h, pad_w = h % 2, w % 2  # yuv420p needs even dimensions

    def prep(f: np.ndarray) -> bytes:
        f = f[..., :3]
        if pad_h or pad_w:
            f = np.pad(f, ((0, pad_h), (0, pad_w), (0, 0)), mode="edge")
        return np.ascontiguousarray(f).tobytes()

    def chunks():
        yield prep(first)
        for f in it:
            yield prep(f)

    return _ffmpeg(
        ["-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w + pad_w}x{h + pad_h}", "-framerate", str(fps),
         "-i", "pipe:0", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18",
         "-preset", "veryfast", "-tune", "animation", "-threads", "2", "-rc-lookahead", "10",
         "-movflags", "+faststart", "-f", "mp4"],
        chunks(), ".mp4",
    )


def gif_bytes(frames: Iterable[np.ndarray], fps: int) -> bytes:
    imgs = [Image.fromarray(np.ascontiguousarray(f[..., :3]), "RGB") for f in frames]
    buf = io.BytesIO()
    imgs[0].save(buf, "GIF", save_all=True, append_images=imgs[1:], duration=int(1000 / fps), loop=0,
                 optimize=False, disposal=2)
    return buf.getvalue()


def animation_bytes(make_frames: Callable[[], Iterable[np.ndarray]], fps: int) -> tuple[bytes, str]:
    """Best available chat-preview animation: (data, file name).

    Takes a factory rather than a list so frames can be produced lazily (and produced
    again for the GIF fallback).
    """
    if ffmpeg_path():
        try:
            return mp4_bytes(make_frames(), fps), "preview.mp4"
        except EncodeError:
            pass
    return gif_bytes(make_frames(), fps), "preview.gif"
