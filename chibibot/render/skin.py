"""Loading and normalising Minecraft skin PNGs.

Supports modern 64x64 skins, legacy 64x32 skins (converted the way the game does it)
and HD skins of any power-of-two width (128, 256, ...), square or 2:1.
"""
from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass

import numpy as np
from PIL import Image

MAX_SKIN_BYTES = 4 * 1024 * 1024
MAX_SKIN_WIDTH = 1024


class SkinError(ValueError):
    """The file is not a usable Minecraft skin."""


@dataclass(frozen=True)
class Skin:
    rgba: np.ndarray  # (64*f, 64*f, 4) uint8, modern layout
    f: int            # HD factor: width // 64
    slim: bool        # detected (or reported by Mojang) Alex-style 3px arms
    png: bytes        # normalised PNG, what we store on disk
    sha1: str

    @property
    def size(self) -> int:
        return self.rgba.shape[1]


def _is_pow2(n: int) -> bool:
    return n > 0 and (n & (n - 1)) == 0


def _legacy_to_modern(a: np.ndarray, f: int) -> np.ndarray:
    """64x32 -> 64x64: the left arm/leg are mirrored copies of the right ones."""
    s = 64 * f
    out = np.zeros((s, s, 4), np.uint8)
    out[: 32 * f] = a

    def cp(x, y, dx, dy, w, h):
        src = out[y * f:(y + h) * f, x * f:(x + w) * f][:, ::-1].copy()
        out[(y + dy) * f:(y + dy + h) * f, (x + dx) * f:(x + dx + w) * f] = src

    # leg
    cp(4, 16, 16, 32, 4, 4)
    cp(8, 16, 16, 32, 4, 4)
    cp(0, 20, 24, 32, 4, 12)
    cp(4, 20, 16, 32, 4, 12)
    cp(8, 20, 8, 32, 4, 12)
    cp(12, 20, 16, 32, 4, 12)
    # arm
    cp(44, 16, -8, 32, 4, 4)
    cp(48, 16, -8, 32, 4, 4)
    cp(40, 20, 0, 32, 4, 12)
    cp(44, 20, -8, 32, 4, 12)
    cp(48, 20, -16, 32, 4, 12)
    cp(52, 20, -8, 32, 4, 12)

    # Old skins often filled the hat area with a solid colour; the game treats a hat
    # layer without a single transparent pixel as "no hat".
    hat = out[0:16 * f, 32 * f:64 * f]
    if (hat[..., 3] >= 128).all():
        hat[..., 3] = 0
    return out


def _force_base_opaque(a: np.ndarray, f: int) -> None:
    """The game ignores alpha on the base layer; so do we."""
    for x0, y0, x1, y1 in ((0, 0, 32, 16), (0, 16, 64, 32), (16, 48, 48, 64)):
        a[y0 * f:y1 * f, x0 * f:x1 * f, 3] = 255


def detect_slim(a: np.ndarray, f: int) -> bool:
    """Slim skins leave the wide-only strip of the right arm empty."""
    strip = a[20 * f:32 * f, 54 * f:56 * f, 3]
    return bool((strip == 0).all())


def load_skin(data: bytes, slim_hint: bool | None = None) -> Skin:
    if len(data) > MAX_SKIN_BYTES:
        raise SkinError("файл слишком большой (макс. 4 МБ)")
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as exc:  # noqa: BLE001 - any decoder failure means "not an image"
        raise SkinError("это не PNG-картинка") from exc
    w, h = img.size
    if w < 64 or w > MAX_SKIN_WIDTH or not _is_pow2(w) or h not in (w, w // 2):
        raise SkinError(f"неподходящий размер {w}×{h} (нужно 64×64, 64×32 или HD 128×128…)")
    arr = np.array(img.convert("RGBA"), dtype=np.uint8)
    f = w // 64
    if h == w // 2:
        arr = _legacy_to_modern(arr, f)
        slim_detected = False
    else:
        arr = arr.copy()
        slim_detected = detect_slim(arr, f)
    _force_base_opaque(arr, f)
    buf = io.BytesIO()
    Image.fromarray(arr, "RGBA").save(buf, "PNG", optimize=True)
    png = buf.getvalue()
    slim = slim_detected if slim_hint is None else bool(slim_hint)
    return Skin(rgba=arr, f=f, slim=slim, png=png, sha1=hashlib.sha1(png).hexdigest())


def box_faces(tex: np.ndarray, f: int, u: int, v: int, w: int, h: int, d: int) -> dict[str, np.ndarray]:
    """Cut the six faces of a Minecraft box UV island (texel units, scaled by f)."""

    def r(x, y, ww, hh):
        return tex[y * f:(y + hh) * f, x * f:(x + ww) * f]

    return {
        "top": r(u + d, v, w, d),
        "bottom": r(u + d + w, v, w, d),
        "right": r(u, v + d, d, h),
        "front": r(u + d, v + d, w, h),
        "left": r(u + d + w, v + d, d, h),
        "back": r(u + d + w + d, v + d, w, h),
    }


def default_skin(slim: bool = False) -> Skin:
    """A simple procedurally drawn skin used when no skin is available (tests, examples)."""
    a = np.zeros((64, 64, 4), np.uint8)

    def fill(x, y, w, h, rgb):
        a[y:y + h, x:x + w, :3] = rgb
        a[y:y + h, x:x + w, 3] = 255

    skin_c, hair, shirt, pants, shoe = (198, 150, 110), (70, 45, 25), (40, 170, 170), (60, 60, 160), (70, 70, 70)
    fill(0, 0, 32, 16, skin_c)
    fill(8, 0, 8, 8, hair)            # head top
    fill(0, 8, 32, 2, hair)           # hair band
    fill(24, 8, 8, 8, hair)           # back of head
    fill(9, 12, 2, 1, (255, 255, 255))
    fill(13, 12, 2, 1, (255, 255, 255))
    fill(10, 12, 1, 1, (70, 60, 150))
    fill(13, 12, 1, 1, (70, 60, 150))
    fill(11, 14, 2, 1, (120, 70, 50))
    fill(16, 16, 24, 16, shirt)
    fill(40, 16, 16, 16, skin_c)
    fill(40, 16, 16, 8, shirt)
    fill(0, 16, 16, 16, pants)
    fill(0, 28, 16, 4, shoe)
    fill(32, 48, 16, 16, skin_c)
    fill(32, 48, 16, 8, shirt)
    fill(16, 48, 16, 16, pants)
    fill(16, 60, 16, 4, shoe)
    if slim:
        a[20:32, 54:56] = 0
    buf = io.BytesIO()
    Image.fromarray(a, "RGBA").save(buf, "PNG")
    return load_skin(buf.getvalue(), slim_hint=slim)
