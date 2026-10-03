"""Chibi horses: a box model in three sizes, procedurally painted coats, tack, gaits and a rider.

The horse is built in its own frame like the player (+z forward, +y up, +x its left side,
1 unit = 1 texel) and is turned side-on in the scene, so its flank is the crisp, flat face
the oblique cameras show best. The rider is a regular player rig hung from the saddle.

All textures are painted here in code: a coat colour with a little per-texel grain, a mane and
tail colour, eyes, nostrils and hooves, optional markings, saddle, chests and armour. No game
textures are used.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .options import MARKED_COATS
from .model import (FACE_NAMES, H_BODY, H_FRONT, H_HEAD, H_HIND, H_TACK, H_TAIL, Joint, Part, Style,
                    face_frame)
from .pose import Pose, arms, ease, legs, seg, static_pose, up_out, wave

# ------------------------------------------------------------------ geometry

# Hand-tuned integer boxes per size: (x0, y0, z0, w, h, d). Integers keep the pixel art on the grid.
SIZES: dict[str, dict] = {
    "normal": {
        "body": (-5, 8, -10, 10, 8, 20),
        "legs": {"fl": (1, 0, 5, 4, 9, 4), "fr": (-5, 0, 5, 4, 9, 4),
                 "bl": (1, 0, -9, 4, 9, 4), "br": (-5, 0, -9, 4, 9, 4)},
        "neck": (-2, 13, 4, 4, 10, 6),
        "head": (-3, 20, 4, 6, 7, 8),
        "muzzle": (-2, 20, 12, 4, 5, 5),
        "ears": {"earl": (1, 27, 5, 2, 3, 1), "earr": (-3, 27, 5, 2, 3, 1)},
        "mane": (-1, 15, 3, 2, 12, 2),
        "tail": (-2, 5, -11, 4, 10, 2),
        "tail_pivot": (0, 15, -10),
        "saddle": (-4, 16, -7, 8, 1, 8),
        "pommel": (-1, 17, 0, 2, 1, 1),
        "chests": {"chestl": (5, 9, -9, 2, 6, 6), "chestr": (-7, 9, -9, 2, 6, 6)},
        "seat": (0, 17, -3),
        "ear_long": 6,
    },
    "foal": {
        "body": (-4, 7, -7, 8, 6, 14),
        "legs": {"fl": (1, 0, 3, 3, 8, 3), "fr": (-4, 0, 3, 3, 8, 3),
                 "bl": (1, 0, -6, 3, 8, 3), "br": (-4, 0, -6, 3, 8, 3)},
        "neck": (-2, 11, 2, 4, 7, 5),
        "head": (-3, 16, 2, 6, 6, 7),
        "muzzle": (-2, 16, 9, 4, 4, 4),
        "ears": {"earl": (1, 22, 3, 2, 2, 1), "earr": (-3, 22, 3, 2, 2, 1)},
        "mane": (-1, 12, 1, 2, 9, 2),
        "tail": (-1, 5, -8, 2, 7, 2),
        "tail_pivot": (0, 12, -7),
        "saddle": (-3, 13, -5, 6, 1, 6),
        "pommel": (-1, 14, 0, 2, 1, 1),
        "chests": {"chestl": (4, 8, -6, 2, 5, 5), "chestr": (-6, 8, -6, 2, 5, 5)},
        "seat": (0, 14, -2),
        "ear_long": 4,
    },
    "big": {
        "body": (-6, 11, -11, 12, 9, 22),
        "legs": {"fl": (2, 0, 6, 4, 12, 4), "fr": (-6, 0, 6, 4, 12, 4),
                 "bl": (2, 0, -10, 4, 12, 4), "br": (-6, 0, -10, 4, 12, 4)},
        "neck": (-3, 17, 5, 6, 11, 6),
        "head": (-4, 25, 4, 8, 8, 9),
        "muzzle": (-3, 25, 13, 6, 5, 5),
        "ears": {"earl": (2, 33, 5, 2, 3, 1), "earr": (-4, 33, 5, 2, 3, 1)},
        "mane": (-1, 19, 4, 2, 13, 2),
        "tail": (-2, 7, -12, 4, 12, 2),
        "tail_pivot": (0, 19, -11),
        "saddle": (-4, 20, -8, 8, 1, 9),
        "pommel": (-1, 21, 0, 2, 1, 1),
        "chests": {"chestl": (6, 12, -10, 2, 7, 7), "chestr": (-8, 12, -10, 2, 7, 7)},
        "seat": (0, 21, -4),
        "ear_long": 7,
    },
}
NECK_TILT = 28.0   # the neck leans forward; the head is turned back level so its sides stay crisp

LONG_EARS = {"donkey", "mule"}
NO_MANE = {"skeleton"}
INFLATE = 0.3   # tack overlays sit this far outside the box they cover


def _box(t):
    x, y, z, w, h, d = t
    return (float(x), float(y), float(z)), (float(w), float(h), float(d)), (int(w), int(h), int(d))


def _part(key: str, joint: str, group: int, t, inflate: float = 0.0) -> Part:
    lo, size, tdim = _box(t)
    if inflate:
        lo = (lo[0] - inflate, lo[1] - inflate, lo[2] - inflate)
        size = (size[0] + 2 * inflate, size[1] + 2 * inflate, size[2] + 2 * inflate)
    return Part(key, joint, group, lo, size, tdim, None, None, None, None)


@dataclass(frozen=True)
class HorseModel:
    joints: dict[str, Joint]
    parts: list[Part]
    seat: tuple[float, float, float]
    dims: dict


def build(size: str, coat: str, tack: str) -> HorseModel:
    D = SIZES.get(size, SIZES["normal"])
    bx, by, bz, bw, bh, bd = D["body"]
    hind_z = D["legs"]["bl"][2] + D["legs"]["bl"][5] / 2
    joints = {
        "h.root": Joint(None, (0, 0, 0)),
        # the body turns around the hind hips: rearing then lifts the front, not the whole horse
        "h.body": Joint("h.root", (0, by + bh / 2, hind_z)),
        "h.seat": Joint("h.body", D["seat"]),
    }
    nx, ny, nz, nw, nh, nd = D["neck"]
    joints["h.neck"] = Joint("h.body", (0, ny + 1, nz + nd / 2))
    joints["h.head"] = Joint("h.neck", (0, ny + nh - 2, nz + nd / 2))
    parts = [_part("h.body", "h.body", H_BODY, D["body"])]
    for k, t in D["legs"].items():
        x, y, z, w, h, d = t
        joints["h." + k] = Joint("h.body", (x + w / 2, by + 1, z + d / 2))
        parts.append(_part("h." + k, "h." + k, H_FRONT if k[0] == "f" else H_HIND, t))
    parts.append(_part("h.neck", "h.neck", H_HEAD, D["neck"]))
    parts.append(_part("h.head", "h.head", H_HEAD, D["head"]))
    parts.append(_part("h.muzzle", "h.head", H_HEAD, D["muzzle"]))
    for k, t in D["ears"].items():
        x, y, z, w, h, d = t
        if coat in LONG_EARS:
            t = (x, y, z, w, D["ear_long"], d)
        joints["h." + k] = Joint("h.head", (x + w / 2, y, z + d / 2))
        parts.append(_part("h." + k, "h." + k, H_HEAD, t))
    if coat not in NO_MANE:
        parts.append(_part("h.mane", "h.neck", H_HEAD, D["mane"]))
    joints["h.tail"] = Joint("h.body", D["tail_pivot"])
    parts.append(_part("h.tail", "h.tail", H_TAIL, D["tail"]))
    if tack != "none":
        parts.append(_part("h.body+", "h.body", H_TACK, D["body"], INFLATE))
        parts.append(_part("h.head+", "h.head", H_TACK, D["head"], INFLATE))
        parts.append(_part("h.muzzle+", "h.head", H_TACK, D["muzzle"], INFLATE))
        parts.append(_part("h.saddle", "h.body", H_TACK, D["saddle"]))
        parts.append(_part("h.pommel", "h.body", H_TACK, D["pommel"]))
    if tack in ARMOR:
        parts.append(_part("h.neck+", "h.neck", H_TACK, D["neck"], INFLATE))
    if tack == "chest":
        for k, t in D["chests"].items():
            parts.append(_part("h." + k, "h.body", H_TACK, t))
    seat = D["seat"]
    if tack != "none":
        seat = (seat[0], seat[1], seat[2])
    else:
        seat = (seat[0], seat[1] - 1, seat[2])  # bareback: no saddle under the rider
    return HorseModel(joints, parts, seat, D)


# ------------------------------------------------------------------ paint

def _c(*rgb) -> np.ndarray:
    return np.array(rgb, np.float32) / 255.0


COATS = {
    "white": dict(base=_c(228, 228, 222), mane=_c(190, 190, 184), hoof=_c(92, 86, 80), muzzle=_c(206, 194, 188)),
    "creamy": dict(base=_c(214, 180, 126), mane=_c(240, 226, 188), hoof=_c(88, 70, 54), muzzle=_c(188, 156, 112)),
    "chestnut": dict(base=_c(172, 94, 46), mane=_c(112, 52, 22), hoof=_c(64, 46, 34), muzzle=_c(140, 80, 44)),
    "brown": dict(base=_c(124, 80, 48), mane=_c(46, 30, 18), hoof=_c(48, 38, 30), muzzle=_c(98, 66, 44)),
    "black": dict(base=_c(46, 42, 44), mane=_c(22, 20, 22), hoof=_c(30, 28, 28), muzzle=_c(64, 58, 58)),
    "gray": dict(base=_c(150, 150, 150), mane=_c(82, 82, 84), hoof=_c(56, 54, 54), muzzle=_c(118, 116, 116)),
    "darkbrown": dict(base=_c(80, 52, 34), mane=_c(30, 20, 14), hoof=_c(34, 26, 22), muzzle=_c(66, 46, 34)),
    "donkey": dict(base=_c(124, 112, 102), mane=_c(56, 48, 44), hoof=_c(48, 42, 38), muzzle=_c(212, 204, 192),
                   belly=_c(176, 166, 156)),
    "mule": dict(base=_c(108, 68, 40), mane=_c(44, 28, 18), hoof=_c(40, 30, 24), muzzle=_c(172, 140, 108),
                 belly=_c(140, 104, 74)),
    "skeleton": dict(base=_c(214, 212, 196), mane=_c(214, 212, 196), hoof=_c(150, 148, 136),
                     muzzle=_c(200, 198, 182), dark=_c(96, 94, 86)),
    "zombie": dict(base=_c(86, 118, 72), mane=_c(40, 56, 36), hoof=_c(46, 58, 40), muzzle=_c(70, 98, 60),
                   dark=_c(58, 82, 50), bone=_c(196, 196, 176)),
}
EYE = _c(26, 22, 24)
EYE_SHINE = _c(236, 236, 236)
WHITE_MARK = _c(240, 238, 232)
BLACK_MARK = _c(34, 30, 30)

LEATHER = dict(base=_c(118, 66, 32), dark=_c(76, 40, 18), light=_c(160, 98, 50))
STRAP = _c(64, 36, 18)
METAL = _c(176, 178, 184)
WOOD = dict(base=_c(150, 104, 52), dark=_c(98, 64, 28), latch=_c(190, 190, 196))
ARMOR = {
    "leather": dict(base=_c(140, 84, 44), dark=_c(92, 52, 24), light=_c(184, 122, 70)),
    "iron": dict(base=_c(200, 202, 208), dark=_c(126, 128, 136), light=_c(236, 238, 242)),
    "gold": dict(base=_c(238, 196, 58), dark=_c(170, 120, 26), light=_c(255, 236, 130)),
    "diamond": dict(base=_c(92, 222, 214), dark=_c(36, 146, 146), light=_c(190, 250, 244)),
}


def _hash(ix, iy, iz, seed: int) -> np.ndarray:
    h = (ix.astype(np.int64) * 73856093) ^ (iy.astype(np.int64) * 19349663) ^ (iz.astype(np.int64) * 83492791)
    h = (h ^ seed) * 2654435761
    return ((h >> 7) & 0xFFFF).astype(np.float32) / 65535.0


def _vnoise(P: np.ndarray, scale: float, seed: int) -> np.ndarray:
    """Smooth 3D value noise in 0..1 (for big patches)."""
    q = P / scale
    i = np.floor(q).astype(np.int64)
    f = q - i
    f = f * f * (3 - 2 * f)
    out = np.zeros(P.shape[:-1], np.float32)
    for dx in (0, 1):
        for dy in (0, 1):
            for dz in (0, 1):
                w = ((f[..., 0] if dx else 1 - f[..., 0]) * (f[..., 1] if dy else 1 - f[..., 1])
                     * (f[..., 2] if dz else 1 - f[..., 2]))
                out += w * _hash(i[..., 0] + dx, i[..., 1] + dy, i[..., 2] + dz, seed)
    return out


@dataclass
class Face:
    """Texel centres of one box face: world-ish (horse frame) and box-local coordinates."""
    P: np.ndarray       # (H, W, 3) horse-frame position
    L: np.ndarray       # (H, W, 3) position inside the box, 0..w / 0..h / 0..d
    name: str
    tdim: tuple[int, int, int]


def _faces(part: Part, base_lo) -> dict[str, Face]:
    """Texel grids for the six faces of a part (in its un-inflated box)."""
    w, h, d = part.tdim
    out = {}
    for name in FACE_NAMES:
        o, u, v, _ = face_frame(name, (0, 0, 0), (w, h, d))
        o, u, v = np.array(o, float), np.array(u, float), np.array(v, float)
        W = int(round(np.abs(u).sum()))
        H = int(round(np.abs(v).sum()))
        cols = (np.arange(W) + 0.5) / W
        rows = (np.arange(H) + 0.5) / H
        L = o + cols[None, :, None] * u + rows[:, None, None] * v
        # nudge onto the surface so the texel belongs to the face it is drawn on
        out[name] = Face(L + np.array(base_lo, float), L, name, (w, h, d))
    return out


def _rgba(rgb: np.ndarray, alpha: np.ndarray | float = 1.0) -> np.ndarray:
    a = np.broadcast_to(np.asarray(alpha, np.float32), rgb.shape[:2])
    out = np.concatenate([np.clip(rgb, 0, 1), a[..., None]], -1)
    return (out * 255 + 0.5).astype(np.uint8)


def _fill(f: Face, color: np.ndarray) -> np.ndarray:
    return np.broadcast_to(color, f.L.shape[:2] + (3,)).astype(np.float32).copy()


def _grain(f: Face, seed: int, amount: float = 0.05) -> np.ndarray:
    ip = np.floor(f.P * 1.0 + 1e-4)
    n = _hash(ip[..., 0], ip[..., 1], ip[..., 2], seed)
    return (1.0 + amount * (n * 2 - 1))[..., None]


def _marks(f: Face, rgb: np.ndarray, kind: str, marks: str, allow: bool) -> np.ndarray:
    if not allow or marks == "none":
        return rgb
    P = f.P
    if marks == "whitefield":
        m = _vnoise(P, 5.0, 11) > 0.6
        rgb[m] = WHITE_MARK
    elif marks == "whitedots":
        ip = np.floor(P / 1.0 + 1e-4)
        rear = np.clip((-P[..., 2]) / 8.0, 0, 1)
        m = _hash(ip[..., 0], ip[..., 1], ip[..., 2], 23) < 0.06 + 0.2 * rear
        if kind in ("body", "leg"):
            rgb[m] = WHITE_MARK
    elif marks == "blackdots":
        ip = np.floor(P / 2.0 + 1e-4)
        m = _hash(ip[..., 0], ip[..., 1], ip[..., 2], 37) < 0.22
        if kind in ("body", "leg", "neck"):
            rgb[m] = BLACK_MARK
    return rgb


def _paint_part(part: Part, coat: str, marks: str, dims: dict) -> dict[str, np.ndarray | None]:
    C = COATS.get(coat, COATS["chestnut"])
    allow = coat in MARKED_COATS
    key = part.key[2:]
    kind = ("leg" if key in ("fl", "fr", "bl", "br") else key)
    lo = part.lo
    faces = _faces(part, lo)
    out: dict[str, np.ndarray | None] = {}
    w, h, d = part.tdim
    for name, f in faces.items():
        L = f.L
        if kind in ("mane", "tail"):
            rgb = _fill(f, C["mane"])
            # strands: every column a little lighter or darker
            col = np.floor(L[..., 0] + L[..., 2] * 1.7 + 1e-4)
            rgb *= (1.0 + 0.12 * (_hash(col, col * 0 + 1, col * 0 + 2, 5) * 2 - 1))[..., None]
            if kind == "tail":
                rgb[L[..., 1] < 1.5] *= 0.82
            if coat == "skeleton":
                rgb = _fill(f, C["base"]) * _grain(f, 3)
            if coat in ("donkey", "mule") and kind == "tail":
                # a thin tail with a dark tuft
                rgb = _fill(f, C["base"])
                rgb[L[..., 1] < 4] = C["mane"]
            out[name] = _rgba(rgb * _grain(f, 9, 0.03))
            continue
        base = C.get("belly", C["base"]) if (name == "bottom" and kind == "body") else C["base"]
        rgb = _fill(f, base) * _grain(f, 1)
        if coat == "donkey" and kind == "body" and name == "top":
            rgb[np.abs(L[..., 0] - w / 2) < 1] *= 0.7          # dark stripe along the spine
        if kind == "leg":
            sock = L[..., 1] < h * 0.5
            if marks == "white" and allow:
                rgb[sock] = WHITE_MARK
            rgb[L[..., 1] < 1.5] = C["hoof"]
        if kind == "muzzle":
            rgb = _fill(f, C["muzzle"]) * _grain(f, 2)
            if name == "front":
                # nostrils near the top corners, a mouth line at the bottom
                cx = L[..., 0]
                cy = L[..., 1]
                nost = (cy > h - 2) & (cy < h - 1) & ((cx < 1) | (cx > w - 1))
                rgb[nost] = C["base"] * 0.35
                rgb[cy < 1] *= 0.6
            if name in ("left", "right"):
                rgb[(L[..., 1] < 1) & (L[..., 2] > d - 3)] *= 0.6
            if marks == "white" and allow and name in ("front", "top"):
                rgb[np.abs(L[..., 0] - w / 2) < 1] = WHITE_MARK
        if kind == "head":
            if name in ("left", "right"):
                # eyes on both sides near the front: a dark 2x2 with a shine
                ez = L[..., 2]
                ey = L[..., 1]
                eye = (ey > h - 4) & (ey < h - 2) & (ez > d - 4) & (ez < d - 2)
                rgb[eye] = EYE if coat != "skeleton" else C["dark"] * 0.5
                shine = (ey > h - 3) & (ey < h - 2) & (ez > d - 3) & (ez < d - 2)
                if coat not in ("skeleton", "zombie"):
                    rgb[shine] = EYE_SHINE
            if marks == "white" and allow and name in ("front", "top"):
                star = np.abs(L[..., 0] - w / 2) < 1
                if name == "top":
                    star &= L[..., 2] > d - 3
                rgb[star] = WHITE_MARK
        if kind in ("earl", "earr") and name == "front":
            rgb[(L[..., 1] > 0.5) & (L[..., 1] < h - 0.5)] *= 0.6
        if kind in ("earl", "earr") and coat in LONG_EARS:
            rgb[L[..., 1] > h - 1.5] = C["mane"]
        if coat == "skeleton":
            dark = C["dark"]
            if kind == "body" and name in ("left", "right", "bottom"):
                ribs = (np.floor(L[..., 2]) % 2 == 1) & (L[..., 1] < h - 1.5)
                rgb[ribs] = dark
            if kind == "leg":
                rgb[(np.floor(L[..., 1]) % 4 == 2)] *= 0.8
        elif coat == "zombie":
            rot = _vnoise(f.P, 3.0, 41)
            rgb[rot > 0.6] = C["dark"]
            rgb[rot > 0.78] = C["bone"]
        else:
            rgb = _marks(f, rgb, kind, marks, allow)
        out[name] = _rgba(rgb)
    return out


def _tack_part(part: Part, tack: str, dims: dict) -> dict[str, np.ndarray | None]:
    key = part.key[2:]
    w, h, d = part.tdim
    base_lo = tuple(np.array(part.lo) + (INFLATE if key.endswith("+") else 0.0))
    faces = _faces(part, base_lo)
    out: dict[str, np.ndarray | None] = {}
    armor = ARMOR.get(tack)
    sx, sy, sz, sw, sh, sd = dims["saddle"]
    for name, f in faces.items():
        L, P = f.L, f.P
        rgb = np.zeros(L.shape[:2] + (3,), np.float32)
        a = np.zeros(L.shape[:2], np.float32)

        def put(mask, color):
            rgb[mask] = color
            a[mask] = 1.0

        if key == "body+":
            if armor and name != "bottom":
                cover = np.ones(L.shape[:2], bool)
                if name in ("left", "right"):
                    cover = L[..., 1] > 1
                put(cover, armor["base"])
                trim = cover & ((L[..., 1] < 2.5) if name in ("left", "right", "front", "back") else
                                (np.abs(L[..., 0] - w / 2) < 1))
                put(trim, armor["dark"])
                rivet = trim & (np.floor(L[..., 2] if name in ("left", "right") else L[..., 0]) % 3 == 1) & \
                    (np.floor(L[..., 1]) == 1)
                put(rivet, armor["light"])
                shine = cover & (name == "top") & (np.abs(L[..., 0] - 2) < 0.6)
                put(shine, armor["light"])
            # the saddle's flaps and girth, drawn over the armour
            zin = (P[..., 2] >= sz) & (P[..., 2] < sz + sd)
            if name == "top":
                put(zin & (P[..., 0] >= sx - 1) & (P[..., 0] < sx + sw + 1), LEATHER["dark"])
            if name in ("left", "right"):
                flap = zin & (L[..., 1] > h - 5)
                put(flap, LEATHER["base"])
                put(flap & (np.abs(L[..., 1] - (h - 5)) < 1), LEATHER["light"])
                girth = np.abs(P[..., 2] - (sz + sd / 2)) < 0.5
                put(girth, STRAP)
            if name == "bottom":
                put(np.abs(P[..., 2] - (sz + sd / 2)) < 0.5, STRAP)
        elif key == "neck+" and armor:
            if name != "back":
                put(np.ones(L.shape[:2], bool), armor["base"])
                put(L[..., 1] < 1.5, armor["dark"])
                if name == "front":
                    put(np.abs(L[..., 0] - w / 2) < 0.6, armor["light"])
        elif key == "head+":
            if armor and name in ("front", "top", "left", "right"):
                plate = np.ones(L.shape[:2], bool) if name in ("front", "top") else (L[..., 1] > h - 4)
                put(plate, armor["base"])
                put(plate & (np.abs(L[..., 0] - w / 2) < 0.6) & (name in ("front", "top")), armor["light"])
            # bridle: cheek straps and a browband
            if name in ("left", "right"):
                put(np.abs(L[..., 2] - 2.5) < 0.5, STRAP)
            if name == "top":
                put(np.abs(L[..., 2] - 2.5) < 0.5, STRAP)
        elif key == "muzzle+":
            if name in ("left", "right", "top"):
                put(np.abs(L[..., 2] - 1.5) < 0.5, STRAP)
            if name in ("left", "right"):
                put((L[..., 1] < 1.5) & (L[..., 2] < 1), METAL)   # the bit
        elif key in ("saddle", "pommel"):
            put(np.ones(L.shape[:2], bool), LEATHER["base"] if name == "top" else LEATHER["dark"])
            if name == "top" and key == "saddle":
                edge = (L[..., 0] < 1) | (L[..., 0] > w - 1) | (L[..., 2] < 1) | (L[..., 2] > d - 1)
                put(edge, LEATHER["light"])
        elif key in ("chestl", "chestr"):
            put(np.ones(L.shape[:2], bool), WOOD["base"])
            planks = np.floor(L[..., 1]) % 3 == 0
            put(planks, WOOD["dark"])
            if name in ("left", "right"):
                latch = (np.abs(L[..., 1] - h / 2) < 1) & (np.abs(L[..., 2] - d / 2) < 1)
                put(latch, WOOD["latch"])
        out[name] = _rgba(rgb * _grain(f, 17, 0.04), a) if a.any() else None
    return out


def textures(model: HorseModel, coat: str, marks: str, tack: str) -> dict[str, dict]:
    out = {}
    for p in model.parts:
        if p.group == H_TACK:
            tex = _tack_part(p, tack, model.dims)
        else:
            tex = _paint_part(p, coat, marks, model.dims)
        if any(v is not None for v in tex.values()):
            out[p.key] = tex
    return out


# ------------------------------------------------------------------ the rider

def ride_legs(p: Pose, model: "HorseModel") -> None:
    """Legs hanging down the horse's flanks: chibi legs are too short to straddle its back,
    so they are moved out to the sides (their tops disappear into the saddle)."""
    bx, _, _, bw, _, _ = model.dims["body"]
    out = bw / 2 - 1
    legs(p, fwd_r=16, fwd_l=16, out_r=6, out_l=6)
    p.off["rleg"] = (-out, 0, 0)
    p.off["lleg"] = (out, 0, 0)


def rider_pose(style: Style, pose_key: str) -> Pose:
    """A rider in the saddle: the chosen static pose for the arms (legs: see ride_legs)."""
    if pose_key == "stand" or pose_key not in RIDER_POSE_KEYS:
        return reins(Pose())
    p = static_pose(pose_key, style)
    p.rot.pop("rleg", None)
    p.rot.pop("lleg", None)
    return p


def reins(p: Pose, lift: float = 0.0) -> Pose:
    return arms(p, fwd_r=48 + lift, fwd_l=48 + lift, out_r=-14, out_l=-14)


RIDER_POSE_KEYS = {"stand", "wave", "hand", "zombie", "tpose", "dab", "plead"}


# ------------------------------------------------------------------ gaits and tricks

def _legs(p: Pose, fl=0.0, fr=0.0, bl=0.0, br=0.0) -> None:
    """Swing angles in degrees, > 0 = hoof forward."""
    p.rot["h.fl"] = (-fl, 0, 0)
    p.rot["h.fr"] = (-fr, 0, 0)
    p.rot["h.bl"] = (-bl, 0, 0)
    p.rot["h.br"] = (-br, 0, 0)


def _body(p: Pose, pitch=0.0, roll=0.0, dy=0.0) -> None:
    """pitch > 0 = nose up."""
    p.rot["h.body"] = (-pitch, 0, roll)
    if dy:
        p.off["h.body"] = (0, dy, 0)


def _head(p: Pose, neck=0.0, head=0.0, turn=0.0) -> None:
    """neck/head > 0 = lowered forward; turn > 0 = towards the horse's left."""
    p.rot["h.neck"] = (NECK_TILT + neck, turn, 0)
    p.rot["h.head"] = (-NECK_TILT + head, turn * 0.5, 0)


def _tail(p: Pose, lift=30.0, swish=0.0) -> None:
    p.rot["h.tail"] = (lift, swish, 0)


@dataclass
class Scene:
    horse: Pose          # h.* joints, lift, spin
    rider: Pose          # player joints (legs already riding)
    lean: float = 0.0    # rider pitch in the saddle, > 0 forward


def h_static(t, st: Style, rp: str) -> Scene:
    p = Pose()
    _head(p)
    _tail(p)
    return Scene(p, rider_pose(st, rp))


def h_idle(t, st, rp) -> Scene:
    p = Pose()
    b = wave(t)
    _body(p, dy=0.25 * b)
    _head(p, neck=3 * wave(t, 1, 0.2), head=-2 * wave(t, 1, 0.2))
    _tail(p, 30 + 4 * b, 14 * wave(t, 2))
    k = max(0.0, math.sin(math.pi * seg(t, 0.55, 0.75)))
    p.rot["h.earl"] = (-25 * k, 0, 0)
    r = rider_pose(st, rp)
    return Scene(p, r)


def h_walk(t, st, rp) -> Scene:
    p = Pose()
    a = 20
    _legs(p, fl=a * wave(t, 1, 0.25), fr=a * wave(t, 1, 0.75), bl=a * wave(t, 1, 0.0), br=a * wave(t, 1, 0.5))
    _body(p, dy=0.3 * wave(t, 2))
    _head(p, neck=5 * wave(t, 2, 0.1), head=-3 * wave(t, 2, 0.1))
    _tail(p, 30, 10 * wave(t))
    r = rider_pose(st, "stand")
    return Scene(p, r)


def h_trot(t, st, rp) -> Scene:
    p = Pose()
    a = 30
    _legs(p, fl=a * wave(t), br=a * wave(t), fr=-a * wave(t), bl=-a * wave(t))
    p.lift = 1.0 * abs(wave(t))
    _head(p, neck=6 * abs(wave(t)), head=-4 * abs(wave(t)))
    _tail(p, 40, 8 * wave(t))
    r = rider_pose(st, "stand")
    r.off["root"] = (0, 0.8 * abs(wave(t, 1, 0.12)), 0)   # posting
    return Scene(p, r, lean=8)


def h_gallop(t, st, rp) -> Scene:
    p = Pose()
    a = 42
    _legs(p, fl=a * wave(t, 1, 0.0), fr=a * wave(t, 1, 0.08), bl=a * wave(t, 1, 0.5), br=a * wave(t, 1, 0.58))
    _body(p, pitch=7 * wave(t, 1, 0.25))
    p.lift = 1.8 * max(0.0, wave(t, 1, 0.1))
    _head(p, neck=12 - 8 * wave(t, 1, 0.25), head=-6)
    _tail(p, 62, 6 * wave(t, 2))
    r = reins(rider_pose(st, "stand"), 10)
    return Scene(p, r, lean=16 - 4 * wave(t, 1, 0.25))


def h_rear(t, st, rp) -> Scene:
    p = Pose()
    k = ease(seg(t, 0.08, 0.38)) - ease(seg(t, 0.66, 0.92))
    pitch = 42 * k
    _body(p, pitch=pitch)
    paw = 22 * wave(t, 4) * k
    _legs(p, fl=(78 + paw) * k, fr=(78 - paw) * k, bl=-pitch, br=-pitch)
    _head(p, neck=-10 * k, head=18 * k)
    _tail(p, 30 - 25 * k, 0)
    r = rider_pose(st, "stand")
    arms(r, out_l=up_out(st, 120) * k, fwd_l=10 * k, fwd_r=48, out_r=-14)
    return Scene(p, r, lean=0.75 * pitch)


def h_jump(t, st, rp) -> Scene:
    p = Pose()
    crouch = ease(seg(t, 0.0, 0.2)) - ease(seg(t, 0.2, 0.3)) + ease(seg(t, 0.74, 0.82)) - ease(seg(t, 0.82, 0.95))
    air = seg(t, 0.28, 0.76)
    h = 4 * air * (1 - air) * 5 if 0 < air < 1 else 0.0
    tuck = math.sin(math.pi * air) if 0 < air < 1 else 0.0
    pitch = 14 * math.sin(2 * math.pi * air) if 0 < air < 1 else -4 * crouch
    _body(p, pitch=pitch, dy=-1.0 * crouch)
    _legs(p, fl=70 * tuck, fr=60 * tuck, bl=-55 * tuck, br=-45 * tuck)
    _head(p, neck=8 * crouch - 10 * tuck, head=5 * tuck)
    _tail(p, 40 + 30 * tuck, 0)
    p.lift = h
    r = rider_pose(st, "stand")
    up = tuck
    arms(r, out_r=up_out(st, 110) * up, out_l=up_out(st, 110) * up, fwd_r=48 * (1 - up), fwd_l=48 * (1 - up))
    return Scene(p, r, lean=10 + 0.6 * pitch)


def h_eat(t, st, rp) -> Scene:
    p = Pose()
    k = ease(seg(t, 0.0, 0.25)) - ease(seg(t, 0.8, 1.0))
    chew = 6 * wave(t, 6) * k
    _head(p, neck=88 * k, head=46 * k + chew)
    _tail(p, 28, 16 * wave(t, 2))
    r = rider_pose(st, rp)
    r.rot["head"] = (0, 18 * wave(t), 0)
    return Scene(p, r, lean=-4 * k)


def h_shake(t, st, rp) -> Scene:
    p = Pose()
    k = math.sin(math.pi * t)
    _head(p, neck=-6 * k, head=4 * k, turn=28 * wave(t, 3) * k)
    _body(p, roll=3 * wave(t, 3) * k)
    _tail(p, 34, 18 * wave(t, 3))
    p.rot["h.earl"] = (0, 0, 20 * wave(t, 6) * k)
    p.rot["h.earr"] = (0, 0, -20 * wave(t, 6) * k)
    r = rider_pose(st, rp)
    return Scene(p, r)


def h_buck(t, st, rp) -> Scene:
    p = Pose()
    k = math.sin(math.pi * seg(t, 0.1, 0.6)) if 0.1 < t < 0.6 else 0.0
    kick = math.sin(math.pi * seg(t, 0.22, 0.5)) if 0.22 < t < 0.5 else 0.0
    pitch = -30 * k
    _body(p, pitch=pitch)
    _legs(p, fl=-pitch * 0.9, fr=-pitch * 0.9, bl=-70 * kick, br=-60 * kick)
    _head(p, neck=30 * k, head=-10 * k)
    _tail(p, 30 + 50 * kick, 0)
    r = rider_pose(st, "stand")
    up = math.sin(math.pi * seg(t, 0.25, 0.7)) if 0.25 < t < 0.7 else 0.0
    arms(r, out_r=up_out(st, 120) * up + 4, out_l=up_out(st, 120) * up + 4, fwd_r=48 * (1 - up), fwd_l=48 * (1 - up))
    r.off["root"] = (0, 2.2 * up, 0)
    return Scene(p, r, lean=0.8 * pitch - 6 * up)


def h_wave(t, st, rp) -> Scene:
    p = Pose()
    paw = max(0.0, wave(t, 2))
    _legs(p, fr=55 * paw)
    _head(p, neck=-4 + 6 * wave(t, 2, 0.25), head=4)
    _tail(p, 34, 12 * wave(t))
    r = rider_pose(st, "stand")
    arms(r, out_l=up_out(st, 108) + 20 * wave(t, 2), fwd_l=12, fwd_r=48, out_r=-14)
    r.rot["head"] = (0, 0, -7 * wave(t, 1))
    return Scene(p, r)


def h_spin(t, st, rp) -> Scene:
    s = h_walk((t * 2) % 1.0, st, rp)
    s.horse.spin = -360 * t
    return s


HORSE_ANIMS = {
    "hidle": (2.4, h_idle),
    "hwalk": (1.2, h_walk),
    "htrot": (0.8, h_trot),
    "hgallop": (0.6, h_gallop),
    "hrear": (2.4, h_rear),
    "hjump": (1.6, h_jump),
    "heat": (2.8, h_eat),
    "hshake": (1.2, h_shake),
    "hbuck": (1.6, h_buck),
    "hwave": (1.4, h_wave),
    "hspin": (2.4, h_spin),
}


def scene(key: str | None, t: float, style: Style, rider_pose_key: str) -> Scene:
    if key in HORSE_ANIMS:
        return HORSE_ANIMS[key][1](t % 1.0, style, rider_pose_key)
    return h_static(t, style, rider_pose_key)

