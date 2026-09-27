"""Example sheets shown in the chat: every option of a step drawn with the user's skin."""
from __future__ import annotations

import io
import os
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import encode
from .engine import FPS, RenderError, render
from .options import ANIMATIONS, BODIES, BUSTS, CAMERAS, POSES, STYLES, Opt, RenderSettings
from .skin import Skin

BG = (236, 239, 244)
CELL_BG = (250, 251, 253)
CHECK = ((250, 251, 253), (232, 236, 242))
ACCENT = (46, 125, 220)
TEXT = (30, 34, 40)

_FONT_FILES = [
    os.path.join(os.path.dirname(__file__), "..", "..", "assets", "font.ttf"),
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]


@lru_cache(maxsize=1)
def _font_file() -> str | None:
    # every candidate above has Cyrillic glyphs; Pillow's built-in fallback does not
    for path in _FONT_FILES:
        if os.path.exists(path):
            try:
                ImageFont.truetype(path, 12)
                return path
            except OSError:
                continue
    return None


@lru_cache(maxsize=8)
def font(size: int):
    path = _font_file()
    if path:
        return ImageFont.truetype(path, size)
    try:
        return ImageFont.load_default(size)
    except TypeError:
        return ImageFont.load_default()


def cyrillic_ok() -> bool:
    return _font_file() is not None


def label_of(o: Opt) -> str:
    return o.ru if cyrillic_ok() else o.en


@lru_cache(maxsize=16)
def _checker(w: int, h: int, cell: int) -> np.ndarray:
    yy, xx = np.mgrid[0:h, 0:w]
    m = ((yy // cell) + (xx // cell)) % 2 == 0
    out = np.empty((h, w, 3), np.uint8)
    out[m] = CHECK[0]
    out[~m] = CHECK[1]
    out.flags.writeable = False
    return out


def checker(w: int, h: int, cell: int = 10) -> np.ndarray:
    return _checker(w, h, cell)


def over(rgba: np.ndarray, bg: np.ndarray) -> np.ndarray:
    a = rgba[..., 3:4].astype(np.float32) / 255
    return (rgba[..., :3] * a + bg * (1 - a)).astype(np.uint8)


def upscale(img: np.ndarray, k: int) -> np.ndarray:
    return np.repeat(np.repeat(img, k, 0), k, 1) if k > 1 else img


# ------------------------------------------------------------------ sheets

class Sheet:
    """Grid of labelled cells; each cell can hold one frame or a looping clip."""

    def __init__(self, cols: int, scale: int, n: int, title: str | None = None):
        self.cols, self.scale, self.n = cols, scale, n
        self.cell = 100 * scale
        self.label_h = max(18, 11 * scale)
        self.gap = 6
        self.title_h = 30 if title else 0
        self.title = title
        rows = (n + cols - 1) // cols
        self.w = cols * self.cell + (cols + 1) * self.gap
        self.h = self.title_h + rows * (self.cell + self.label_h) + (rows + 1) * self.gap

    def base(self, labels: list[str], selected: int | None) -> Image.Image:
        im = Image.new("RGB", (self.w, self.h), BG)
        d = ImageDraw.Draw(im)
        if self.title:
            d.text((self.gap + 2, 6), self.title, fill=TEXT, font=font(18))
        f = font(max(11, int(self.label_h * 0.62)))
        for i, lab in enumerate(labels):
            x, y = self.pos(i)
            if selected == i:
                d.rectangle([x - 3, y - 3, x + self.cell + 2, y + self.cell + self.label_h + 2], fill=ACCENT)
            d.rectangle([x, y + self.cell, x + self.cell - 1, y + self.cell + self.label_h - 1],
                        fill=ACCENT if selected == i else (255, 255, 255))
            text = f"{i + 1}. {lab}"
            if d.textlength(text, font=f) > self.cell - 8:
                while len(text) > 3 and d.textlength(text + "..", font=f) > self.cell - 8:
                    text = text[:-1]
                text += ".."
            d.text((x + 4, y + self.cell + 2), text, fill=(255, 255, 255) if selected == i else TEXT, font=f)
        return im

    def pos(self, i: int) -> tuple[int, int]:
        r, c = divmod(i, self.cols)
        return self.gap + c * (self.cell + self.gap), self.title_h + self.gap + r * (self.cell + self.label_h + self.gap)

    def tile(self, frame: np.ndarray | None) -> np.ndarray:
        bg = checker(self.cell, self.cell, max(5, 5 * self.scale))
        return bg if frame is None else over(upscale(frame, self.scale), bg)

    def put(self, im: np.ndarray, i: int, tile: np.ndarray) -> None:
        x, y = self.pos(i)
        im[y:y + self.cell, x:x + self.cell] = tile

    def paste(self, im: np.ndarray, i: int, frame: np.ndarray | None, badge: bool = False) -> None:
        self.put(im, i, self.tile(frame))
        x, y = self.pos(i)
        if frame is not None and badge:  # small "play" triangle: this emoji is animated
            s = 7 * self.scale
            x0, y0 = x + self.cell - s - 4, y + 4
            for r in range(s):
                half = (r if r < s // 2 else s - 1 - r)
                im[y0 + r, x0:x0 + half + 1] = ACCENT


def _png(img: Image.Image | np.ndarray) -> bytes:
    if isinstance(img, np.ndarray):
        img = Image.fromarray(img)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def static_sheet(skin: Skin, variants: list[tuple[str, RenderSettings]], cols: int, scale: int,
                 selected: int | None, title: str | None = None) -> bytes:
    sh = Sheet(cols, scale, len(variants), title)
    im = np.array(sh.base([v[0] for v in variants], selected))
    for i, (_, s) in enumerate(variants):
        try:
            frame = render(skin, s.copy(anim="none")).frames[0]
        except RenderError:
            frame = None
        sh.paste(im, i, frame)
    return _png(im)


def animated_sheet(skin: Skin, variants: list[tuple[str, RenderSettings]], cols: int, scale: int,
                   selected: int | None, title: str | None = None, seconds: float = 3.0) -> tuple[bytes, str]:
    sh = Sheet(cols, scale, len(variants), title)
    base = np.array(sh.base([v[0] for v in variants], selected))
    # Tiles are kept at native 100x100 and blown up only when a frame is assembled, and the
    # frames themselves are streamed to the encoder: this sheet used to need ~700 MB.
    cell = max(5, 5 * sh.scale) // sh.scale
    bg = checker(100, 100, cell)
    blank = [bg]
    clips = []
    for _, s in variants:
        try:
            clips.append([over(f, bg) for f in render(skin, s).frames])
        except RenderError:
            clips.append(blank)
    n = int(seconds * FPS)

    def make_frames():
        for t in range(n):
            im = base.copy()
            for i, clip in enumerate(clips):
                sh.put(im, i, upscale(clip[t % len(clip)], sh.scale))
            yield im

    return encode.animation_bytes(make_frames, FPS)


# ------------------------------------------------------------------ per-step examples

def _index(opts: list[Opt], key: str) -> int | None:
    for i, o in enumerate(opts):
        if o.key == key:
            return i
    return None


def step_example(skin: Skin, s: RenderSettings, step: str, size: int = 100) -> tuple[str, bytes, str]:
    """-> (kind, data, filename); kind is 'photo' or 'animation'."""
    base = s.copy(anim="none") if step != "anim" else s
    if step == "style":
        variants = [(label_of(o), base.copy(style=o.key)) for o in STYLES]
        return "photo", static_sheet(skin, variants, 2, 3, _index(STYLES, s.style)), "style.png"
    if step == "body":
        variants = []
        for o in BODIES:
            lab = label_of(o)
            if o.key == "auto":
                lab += " → Alex" if skin.slim else " → Steve"
            variants.append((lab, base.copy(body=o.key)))
        return "photo", static_sheet(skin, variants, 3, 2, _index(BODIES, s.body)), "body.png"
    if step == "pose":
        variants = [(label_of(o), base.copy(pose=o.key)) for o in POSES]
        return "photo", static_sheet(skin, variants, 5, 2, _index(POSES, s.pose)), "pose.png"
    if step == "bust":
        variants = [(label_of(o), base.copy(bust=o.key)) for o in BUSTS]
        return "photo", static_sheet(skin, variants, 4, 2, _index(BUSTS, s.bust)), "bust.png"
    if step == "cam":
        variants = [(label_of(o), base.copy(cam=o.key)) for o in CAMERAS]
        if s.animated:
            # animated sheets are drawn in pixel mode: smooth mode is ~15x slower and the
            # final preview shows it anyway
            variants = [(lab, v.copy(anim=s.anim, mode="pixel")) for lab, v in variants]
            data, name = animated_sheet(skin, variants, 5, 2, _index(CAMERAS, s.cam))
            return "animation", data, name
        return "photo", static_sheet(skin, variants, 5, 2, _index(CAMERAS, s.cam)), "cam.png"
    if step == "anim":
        variants = [(label_of(o), s.copy(anim=o.key, mode="pixel")) for o in ANIMATIONS]
        data, name = animated_sheet(skin, variants, 5, 2, _index(ANIMATIONS, s.anim))
        return "animation", data, name
    return result_preview(skin, s, size)


def result_preview(skin: Skin, s: RenderSettings, size: int = 100) -> tuple[str, bytes, str]:
    """The emoji/sticker exactly as it will be uploaded; tiny emoji are blown up for the chat."""
    res = render(skin, s, size)
    scale = max(1, 400 // size)
    side = size * scale
    bg = checker(side, side, max(8, side // 25))

    def make_frames():
        return (over(upscale(f, scale), bg) for f in res.frames)

    if res.animated:
        data, name = encode.animation_bytes(make_frames, res.fps)
        return "animation", data, name
    return "photo", _png(over(upscale(res.frames[0], scale), bg)), "preview.png"


def overview_sheet(thumbs: list[tuple[str, bytes | None, bool]], title: str | None = None, cols: int = 8) -> bytes:
    """Contact sheet of a whole pack from stored 100x100 thumbnails."""
    n = max(1, len(thumbs))
    cols = min(cols, n)
    sh = Sheet(cols, 1, n, title)
    im = np.array(sh.base([t[0] for t in thumbs], None))
    for i, (_, png, animated) in enumerate(thumbs):
        frame = None
        if png:
            try:
                frame = np.array(Image.open(io.BytesIO(png)).convert("RGBA").resize((100, 100), Image.NEAREST))
            except Exception:  # noqa: BLE001
                frame = None
        sh.paste(im, i, frame, badge=animated)
    return _png(im)
