"""Line/area charts for the admin statistics, drawn with Pillow (no plotting library needed).

Dark surface, recessive hairline grid, 2 px lines over a light wash of the same hue, a legend
for two series, direct labels only on the peak or the line ends. Drawn at 2x and scaled down
for smooth edges.
"""
from __future__ import annotations

import io
import math
import os
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

from .previews import font as _fallback_font

# 16:10 and large type: the picture is first seen as a phone-sized preview in the chat
W, H, SS = 1200, 750, 2

SURFACE = (26, 26, 25)
GRID = (46, 46, 44)
AXIS = (84, 84, 80)
TEXT = (255, 255, 255)
TEXT2 = (195, 194, 183)
MUTED = (140, 139, 132)
# categorical slots 1-2 of the validated dark palette (blue, orange)
SERIES = [(57, 135, 229), (217, 89, 38)]
WASH = 0.18

_REGULAR = [
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]
_BOLD = [
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]


@lru_cache(maxsize=16)
def _font(size: int, bold: bool = False):
    for path in _BOLD if bold else _REGULAR:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size * SS)
            except OSError:
                continue
    return _fallback_font(size * SS)


def num(n: float) -> str:
    return f"{int(round(n)):,}".replace(",", " ")


def nice_step(raw: float) -> int:
    """Smallest 1/2/5 x 10^k step >= raw (at least 1: the values are counts)."""
    if raw <= 1:
        return 1
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 5, 10):
        if m * mag >= raw:
            return int(m * mag)
    return int(10 * mag)


def render(spec: dict) -> bytes:
    """spec: title, subtitle, n, series [{name, values, total}], labels [(pos, text)],
    markers (dot on every point), peak (label the highest point), ends (label the line ends)."""
    s = SS
    img = Image.new("RGBA", (W * s, H * s), SURFACE + (255,))
    d = ImageDraw.Draw(img)
    series = spec["series"][:len(SERIES)]
    n = max(1, int(spec["n"]))

    # title block
    d.text((36 * s, 26 * s), spec["title"], fill=TEXT, font=_font(40, True))
    if spec.get("subtitle"):
        d.text((36 * s, 82 * s), spec["subtitle"], fill=TEXT2, font=_font(25))

    # y scale: 4-5 clean ticks with a little headroom over the highest value
    vmax = max([max(x["values"], default=0) for x in series] + [0])
    step = nice_step(max(vmax, 4) / 4)
    top = step * max(1, math.ceil(vmax / step))
    if vmax > top * 0.9:
        top += step
    ticks = list(range(0, top + 1, step))
    f_axis = _font(23)
    label_w = max(d.textlength(num(t), font=f_axis) for t in ticks)
    x0, x1 = 36 * s + label_w + 16 * s, (W - 40) * s
    y0 = 150 * s
    y1 = (H - (118 if len(series) > 1 else 72)) * s

    def X(i: float) -> float:
        return (x0 + x1) / 2 if n == 1 else x0 + (x1 - x0) * i / (n - 1)

    def Y(v: float) -> float:
        return y1 - (y1 - y0) * v / top

    for t in ticks:
        y = Y(t)
        d.line([(x0, y), (x1, y)], fill=AXIS if t == 0 else GRID, width=s)
        d.text((x0 - 14 * s, y), num(t), fill=MUTED, font=f_axis, anchor="rm")

    # x labels with a short tick, skipping any that would overlap the previous one
    last_right = -1e9
    for pos, text in spec.get("labels", []):
        x = X(pos)
        tw = d.textlength(text, font=f_axis)
        cx = min(max(x, x0 + tw / 2 - 20 * s), W * s - 16 * s - tw / 2)
        if cx - tw / 2 < last_right + 12 * s:
            continue
        d.line([(x, y1), (x, y1 + 6 * s)], fill=AXIS, width=s)
        d.text((cx, y1 + 12 * s), text, fill=TEXT2, font=f_axis, anchor="mt")
        last_right = cx + tw / 2

    pts = [[(X(i), Y(v)) for i, v in enumerate(x["values"][:n])] for x in series]

    # washes first (each on its own layer so overlaps blend), lines on top of all of them
    for k, p in enumerate(pts):
        if len(p) < 2:
            continue
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(layer).polygon([(p[0][0], y1)] + p + [(p[-1][0], y1)],
                                      fill=SERIES[k] + (int(255 * WASH),))
        img.alpha_composite(layer)
    d = ImageDraw.Draw(img)
    lw = 2 * s
    for k, p in enumerate(pts):
        if len(p) >= 2:
            d.line(p, fill=SERIES[k], width=lw, joint="curve")
        for x, y in (p[0], p[-1]):  # round caps
            d.ellipse([x - lw / 2, y - lw / 2, x + lw / 2, y + lw / 2], fill=SERIES[k])

    def dot(x: float, y: float, color, r: float = 4) -> None:
        R = (r + 2) * s  # 2 px surface ring keeps it readable where lines cross
        d.ellipse([x - R, y - R, x + R, y + R], fill=SURFACE)
        d.ellipse([x - r * s, y - r * s, x + r * s, y + r * s], fill=color)

    if spec.get("markers"):
        for k, p in enumerate(pts):
            for x, y in p:
                dot(x, y, SERIES[k], 3.5)

    f_val = _font(25, True)
    if spec.get("peak") and pts and pts[0]:
        vals = series[0]["values"][:n]
        i = max(range(len(vals)), key=lambda j: (vals[j], j))
        if vals[i] > 0:
            x, y = pts[0][i]
            dot(x, y, SERIES[0], 5)
            text = num(vals[i])
            tw = d.textlength(text, font=f_val)
            cx = min(max(x, x0 + tw / 2), x1 - tw / 2)
            d.text((cx, y - 12 * s), text, fill=TEXT, font=f_val, anchor="mb")

    if spec.get("ends"):
        # value at the end of each line, pushed apart when the ends are close together
        ends = sorted(((p[-1][1], k) for k, p in enumerate(pts) if p), key=lambda e: e[0])
        placed: list[float] = []
        for y, k in ends:
            ty = y - 12 * s
            if placed and ty < placed[-1] + 32 * s:
                ty = placed[-1] + 32 * s
            placed.append(ty)
            x = pts[k][-1][0]
            dot(x, y, SERIES[k], 5)
            d.text((x - 12 * s, ty), num(series[k]["values"][n - 1]), fill=TEXT, font=f_val, anchor="rb")

    # legend for two series: a short line key in the series colour, the name, the total;
    # smaller type if it does not fit (fonts differ in width between systems)
    if len(series) > 1:
        gap = 40 * s
        for size in (24, 22, 20, 18, 16):
            f_name, f_val = _font(size), _font(size, True)
            items = []
            for x in series:
                w = 36 * s + d.textlength(x["name"], font=f_name)
                if x.get("total"):
                    w += 10 * s + d.textlength(x["total"], font=f_val)
                items.append(w)
            if sum(items) + gap * (len(items) - 1) <= (W - 48) * s:
                break
        cx = max(24 * s, (W * s - (sum(items) + gap * (len(items) - 1))) / 2)
        ly = (H - 40) * s
        for k, x in enumerate(series):
            d.line([(cx, ly), (cx + 24 * s, ly)], fill=SERIES[k], width=4 * s)
            tx = cx + 36 * s
            d.text((tx, ly), x["name"], fill=TEXT2, font=f_name, anchor="lm")
            if x.get("total"):
                d.text((tx + d.textlength(x["name"], font=f_name) + 10 * s, ly), x["total"], fill=TEXT,
                       font=f_val, anchor="lm")
            cx += items[k] + gap

    out = img.convert("RGB").resize((W, H), Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, "PNG")
    return buf.getvalue()
