"""The chibi renderer: a tiny software rasteriser that draws posed boxes as pixel art.

Pipeline per frame: pose -> joint matrices -> boxes in world space -> camera frame ->
oblique projection -> textured quads into a z-buffer -> shading/ink passes -> frame.

The cameras are oblique rather than perspective: the face of the box you look at keeps
its exact texel grid (2 px per head texel in Classic, 3 in Live) and the side faces are
squished next to it. That is what keeps the result reading as crisp pixel art.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .model import GROUP_PRIORITY, HEAD, STYLES, Part, Style
from .options import FOURS_ANIMATIONS, RenderSettings
from .pose import ANIMS, Pose, anim_pose, pose_fours, static_pose
from .skin import Skin, box_faces

EMOJI_SIZE = 100
FPS = 20
MAX_SECONDS = 3.0
SUPERSAMPLE = 4          # smooth mode at emoji size
SUPERSAMPLE_LARGE = 1    # smooth mode at sticker size (512 px): Telegram shows stickers scaled down anyway
OBLIQUE = 0.375


class RenderError(RuntimeError):
    pass


# ------------------------------------------------------------------ math helpers

def _rx(d: float) -> np.ndarray:
    c, s = math.cos(math.radians(d)), math.sin(math.radians(d))
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]], float)


def _ry(d: float) -> np.ndarray:
    c, s = math.cos(math.radians(d)), math.sin(math.radians(d))
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], float)


def _rz(d: float) -> np.ndarray:
    c, s = math.cos(math.radians(d)), math.sin(math.radians(d))
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], float)


@dataclass(frozen=True)
class Camera:
    rot: np.ndarray   # world -> camera frame (camera frame: +z towards the viewer)
    a: float          # oblique x offset per unit of depth
    b: float          # oblique y offset per unit of depth

    @property
    def view(self) -> np.ndarray:
        return np.array([-self.a, self.b, 1.0])


CAMERAS: dict[str, Camera] = {
    "34r": Camera(np.eye(3), OBLIQUE, 0.0),
    "front": Camera(np.eye(3), 0.0, 0.0),
    "34l": Camera(np.eye(3), -OBLIQUE, 0.0),
    "sider": Camera(_ry(90), 0.0, 0.0),
    "back": Camera(_ry(180), 0.0, 0.0),
    "sidel": Camera(_ry(-90), 0.0, 0.0),
    "b34r": Camera(_ry(180), -OBLIQUE, 0.0),
    "b34l": Camera(_ry(180), OBLIQUE, 0.0),
    "top": Camera(_rx(90), 0.0, -0.4),
    "bottom": Camera(_rz(180) @ _rx(-90), 0.0, -0.4),
}

# face -> (origin corner, U edge, V edge, outward normal) as functions of the box
FACE_NAMES = ("front", "back", "right", "left", "top", "bottom")


def _face_frame(name: str, lo, size):
    x0, y0, z0 = lo
    w, h, d = size
    if name == "front":
        return (x0, y0 + h, z0 + d), (w, 0, 0), (0, -h, 0), (0, 0, 1)
    if name == "back":
        return (x0 + w, y0 + h, z0), (-w, 0, 0), (0, -h, 0), (0, 0, -1)
    if name == "right":
        return (x0, y0 + h, z0), (0, 0, d), (0, -h, 0), (-1, 0, 0)
    if name == "left":
        return (x0 + w, y0 + h, z0 + d), (0, 0, -d), (0, -h, 0), (1, 0, 0)
    if name == "top":
        return (x0, y0 + h, z0), (w, 0, 0), (0, 0, d), (0, 1, 0)
    return (x0, y0, z0), (w, 0, 0), (0, 0, d), (0, -1, 0)


def _box_corners(lo, size) -> np.ndarray:
    x0, y0, z0 = lo
    w, h, d = size
    return np.array([[x0 + i * w, y0 + j * h, z0 + k * d] for i in (0, 1) for j in (0, 1) for k in (0, 1)], float)


# ------------------------------------------------------------------ textures

def _alpha_over(top: np.ndarray, bottom: np.ndarray) -> np.ndarray:
    t = top.astype(np.float32) / 255.0
    b = bottom.astype(np.float32) / 255.0
    ta, ba = t[..., 3:4], b[..., 3:4]
    oa = ta + ba * (1 - ta)
    rgb = (t[..., :3] * ta + b[..., :3] * ba * (1 - ta)) / np.maximum(oa, 1e-6)
    return (np.concatenate([rgb, oa], -1) * 255 + 0.5).astype(np.uint8)


def part_textures(skin: Skin, part: Part, hidden: set[str]) -> dict[str, np.ndarray] | None:
    base_on = part.uv is not None and part.vis_base not in hidden
    over_on = part.uv_over is not None and part.vis_over not in hidden
    if not base_on and not over_on:
        return None
    w, h, d = part.tdim
    base = box_faces(skin.rgba, skin.f, *part.uv, w, h, d) if base_on else None
    over = box_faces(skin.rgba, skin.f, *part.uv_over, w, h, d) if over_on else None
    out = {}
    for k in FACE_NAMES:
        if base is not None and over is not None:
            out[k] = _alpha_over(over[k], base[k])
        else:
            out[k] = (base or over)[k]
        if not (out[k][..., 3] >= 128).any():
            out[k] = None
    if all(v is None for v in out.values()):
        return None
    return out


# ------------------------------------------------------------------ geometry

@dataclass
class Quad:
    o: np.ndarray       # camera-frame origin corner
    u: np.ndarray
    v: np.ndarray
    tex: np.ndarray
    shade: float
    group: int


@dataclass
class Frame:
    quads: list[Quad]
    corners: np.ndarray       # camera-frame corners of the drawn parts, for framing
    cut: float | None         # bust crop line, in screen y at scale 1
    tint: float


class Rig:
    def __init__(self, skin: Skin, settings: RenderSettings):
        self.settings = settings
        self.style: Style = STYLES.get(settings.style, STYLES["classic"])
        if settings.body == "auto":
            slim = skin.slim
        else:
            slim = settings.body == "alex"
        self.slim = slim
        self.parts = self.style.slim if slim else self.style.wide
        self.joints = self.style.slim_joints if slim else self.style.joints
        hidden = set(settings.hidden)
        self.textures = {}
        for p in self.parts:
            if settings.bust == "head" and p.group != HEAD:
                continue
            tex = part_textures(skin, p, hidden)
            if tex is not None:
                self.textures[p.key] = tex
        if not self.textures:
            raise RenderError("все части скрыты — нечего рисовать")
        self.cam = CAMERAS.get(settings.cam, CAMERAS["34r"])

    # -- poses
    def poses(self) -> tuple[list[Pose], int]:
        s = self.settings
        if s.animated and s.anim in ANIMS:
            period, _ = ANIMS[s.anim]
            try:
                speed = float(s.speed)
            except ValueError:
                speed = 1.0
            speed = min(4.0, max(0.25, speed))
            seconds = period / speed
            # longer clips get played faster rather than cut: an emoji loop must be <= 3 s
            seconds = min(seconds, MAX_SECONDS)
            n = max(2, round(seconds * FPS))
            return [anim_pose(s.anim, i / n, self.style) for i in range(n)], FPS
        if s.pose == "fours":
            return [pose_fours(self.style)], FPS
        return [static_pose(s.pose, self.style)], FPS

    def _joint_mats(self, pose: Pose) -> dict[str, np.ndarray]:
        mats: dict[str, np.ndarray] = {}

        def get(name: str) -> np.ndarray:
            if name in mats:
                return mats[name]
            j = self.joints[name]
            parent = get(j.parent) if j.parent else np.eye(4)
            piv = np.array(j.pivot, float)
            off = np.array(pose.off.get(name, (0, 0, 0)), float)
            rx, ry, rz = pose.rot.get(name, (0, 0, 0))
            r = _ry(ry) @ _rx(rx) @ _rz(rz)
            m = np.eye(4)
            m[:3, :3] = r
            m[:3, 3] = piv + off - r @ piv
            mats[name] = parent @ m
            return mats[name]

        for n in self.joints:
            get(n)
        return mats

    def _placement(self, pose: Pose):
        mats = self._joint_mats(pose)
        # ground the figure on its lowest point (hidden parts count, so hiding the legs
        # does not drop the body to the floor)
        lowest = min(
            float((_box_corners(p.lo, p.size) @ mats[p.joint][:3, :3].T + mats[p.joint][:3, 3])[:, 1].min())
            for p in self.parts
        )
        dy = (-lowest if pose.ground else 0.0) + pose.lift
        cam_rot = self.cam.rot @ _ry(pose.spin)
        return mats, np.array([0.0, dy, 0.0]), cam_rot

    def head_anchor(self) -> np.ndarray:
        """Camera-frame corner of the head box in the neutral pose (for pixel snapping)."""
        mats, shift, cam_rot = self._placement(Pose())
        head = next(p for p in self.parts if p.group == HEAD)
        m = mats[head.joint]
        return cam_rot @ (m[:3, :3] @ np.array(head.lo, float) + m[:3, 3] + shift)

    def frame(self, pose: Pose) -> Frame:
        style = self.style
        mats, shift, cam_rot = self._placement(pose)
        view = self.cam.view

        def to_cam(pts: np.ndarray) -> np.ndarray:
            return (pts + shift) @ cam_rot.T

        # far limbs are shaded darker
        def depth_of(pt) -> float:
            c = cam_rot @ pt
            return float(-(c @ view))

        far = {}
        piv = {k: mats[k][:3, :3] @ np.array(self.joints[k].pivot) + mats[k][:3, 3] for k in ("rarm", "larm", "rleg", "lleg")}
        for a, b, fac in (("rarm", "larm", style.far_arm), ("rleg", "lleg", style.far_leg)):
            da, db = depth_of(piv[a]), depth_of(piv[b])
            if da > db + 0.5:
                far[a] = fac
            elif db > da + 0.5:
                far[b] = fac

        quads: list[Quad] = []
        corners = []
        sh = style.shade
        for p in self.parts:
            tex = self.textures.get(p.key)
            if tex is None:
                continue
            m = mats[p.joint]
            m3, t3 = m[:3, :3], m[:3, 3]
            corners.append(to_cam(_box_corners(p.lo, p.size) @ m3.T + t3))
            for fname in FACE_NAMES:
                ftex = tex[fname]
                if ftex is None:
                    continue
                o, u, v, n = _face_frame(fname, p.lo, p.size)
                n_c = cam_rot @ (m3 @ np.array(n, float))
                if float(n_c @ view) <= 1e-6:
                    continue
                o_c = to_cam((m3 @ np.array(o, float) + t3)[None])[0]
                u_c = cam_rot @ (m3 @ np.array(u, float))
                v_c = cam_rot @ (m3 @ np.array(v, float))
                nx, ny, nz = n_c
                shade = (nx * nx * sh["side"] + ny * ny * (sh["top"] if ny > 0 else sh["bottom"])
                         + nz * nz * (sh["front"] if nz > 0 else sh["back"]))
                shade *= far.get(p.key, 1.0)
                quads.append(Quad(o_c, u_c, v_c, ftex, float(shade), p.group))

        cut = None
        if self.settings.bust in ("half", "portrait"):
            body_m = mats["body"]
            if self.settings.bust == "half":
                local = np.array([0.0, style.hip_y, 0.0])
            else:
                local = np.array([0.0, style.neck_y - 0.45 * style.body_h, 0.0])
            c = to_cam((body_m[:3, :3] @ local + body_m[:3, 3])[None])[0]
            cut = float(-c[1] + self.cam.b * c[2])
        return Frame(quads, np.concatenate(corners, 0), cut, pose.tint)


# ------------------------------------------------------------------ rasteriser

class Canvas:
    def __init__(self, w: int, h: int):
        self.w, self.h = w, h
        self.color = np.zeros((h, w, 3), np.float32)
        self.depth = np.full((h, w), np.inf, np.float32)
        self.ids = np.full((h, w), -1, np.int8)

    def draw(self, q: Quad, cam: Camera, g: float, ox: float, oy: float) -> None:
        a, b = cam.a, cam.b
        vn = cam.view / np.linalg.norm(cam.view)

        def scr(p):
            return np.array([g * (p[0] + a * p[2]) + ox, g * (-p[1] + b * p[2]) + oy, -(p @ vn)])

        def vec(p):
            return np.array([g * (p[0] + a * p[2]), g * (-p[1] + b * p[2]), -(p @ vn)])

        o, u, v = scr(q.o), vec(q.u), vec(q.v)
        det = u[0] * v[1] - u[1] * v[0]
        if abs(det) < 1e-6:
            return
        xs_c = (o[0], o[0] + u[0], o[0] + v[0], o[0] + u[0] + v[0])
        ys_c = (o[1], o[1] + u[1], o[1] + v[1], o[1] + u[1] + v[1])
        x0, x1 = max(int(math.floor(min(xs_c))), 0), min(int(math.ceil(max(xs_c))), self.w)
        y0, y1 = max(int(math.floor(min(ys_c))), 0), min(int(math.ceil(max(ys_c))), self.h)
        if x0 >= x1 or y0 >= y1:
            return
        dx = (np.arange(x0, x1, dtype=np.float64) + 0.5 - o[0])[None, :]
        dy = (np.arange(y0, y1, dtype=np.float64) + 0.5 - o[1])[:, None]
        s = (dx * v[1] - dy * v[0]) / det
        t = (dy * u[0] - dx * u[1]) / det
        inside = (s >= 0) & (s < 1) & (t >= 0) & (t < 1)
        if not inside.any():
            return
        th, tw = q.tex.shape[:2]
        si = np.clip((s * tw).astype(np.int32), 0, tw - 1)
        ti = np.clip((t * th).astype(np.int32), 0, th - 1)
        texel = q.tex[ti, si]
        inside &= texel[..., 3] >= 128
        z = (o[2] + s * u[2] + t * v[2]).astype(np.float32)
        dsub = self.depth[y0:y1, x0:x1]
        win = inside & (z < dsub)
        if not win.any():
            return
        dsub[win] = z[win]
        self.color[y0:y1, x0:x1][win] = texel[win][:, :3].astype(np.float32) * (q.shade / 255.0)
        self.ids[y0:y1, x0:x1][win] = q.group


def _neighbor(a: np.ndarray, dx: int, dy: int, fill) -> np.ndarray:
    """out[y, x] = a[y + dy, x + dx] (fill outside)."""
    h, w = a.shape[:2]
    out = np.full_like(a, fill)
    if abs(dx) >= w or abs(dy) >= h:
        return out
    out[max(0, -dy):h - max(0, dy), max(0, -dx):w - max(0, dx)] = \
        a[max(0, dy):h - max(0, -dy), max(0, dx):w - max(0, -dx)]
    return out


_PRIO = np.zeros(8, np.int8)
for _g, _p in GROUP_PRIORITY.items():
    _PRIO[_g] = _p
_DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1))


def _behind_mask(cv: Canvas, dist: int, dirs=_DIRS) -> np.ndarray:
    """Pixels that touch (within dist) a different part lying in front of them."""
    ids, dep = cv.ids, cv.depth
    own = ids >= 0
    pr = _PRIO[np.clip(ids, 0, 7)]
    mask = np.zeros(ids.shape, bool)
    with np.errstate(invalid="ignore"):
        _behind_loop(ids, dep, own, pr, mask, dist, dirs)
    return mask


def _behind_loop(ids, dep, own, pr, mask, dist, dirs) -> None:
    for k in range(1, dist + 1):
        for dx, dy in dirs:
            qi = _neighbor(ids, dx * k, dy * k, -1)
            qd = _neighbor(dep, dx * k, dy * k, np.inf)
            qp = _PRIO[np.clip(qi, 0, 7)]
            front = (qd < dep - 1e-3) | ((np.abs(qd - dep) <= 1e-3) & (qp > pr))
            mask |= own & (qi >= 0) & (qi != ids) & front


def _post(cv: Canvas, style: Style, outline: str, width: int, tint: float) -> np.ndarray:
    rgb = cv.color
    alpha = (cv.ids >= 0)
    if style.ao > 0 and outline != "parts":
        under = _behind_mask(cv, 1, dirs=((0, -1),))
        beside = _behind_mask(cv, 1, dirs=((1, 0), (-1, 0))) & ~under
        rgb[under] *= (1 - style.ao)
        rgb[beside] *= (1 - style.ao * 0.5)
    if outline == "parts":
        inner = _behind_mask(cv, width)
        rgb[inner] *= style.ink
    if outline in ("parts", "figure"):
        # silhouette ring: each new pixel takes a darkened copy of the figure pixel it
        # grows from ("colour from the art"); later rings copy the ink as is
        mask = alpha.copy()
        for step in range(width):
            grow = np.zeros_like(mask)
            src = np.zeros_like(rgb)
            for dx, dy in _DIRS:
                new = _neighbor(mask, dx, dy, False) & ~mask & ~grow
                src[new] = _neighbor(rgb, dx, dy, 0)[new]
                grow |= new
            rgb[grow] = src[grow] * (style.ink if step == 0 else 1.0)
            mask |= grow
        alpha = mask
    if tint > 0:
        rgb[..., 1] *= (1 - 0.7 * tint)
        rgb[..., 2] *= (1 - 0.7 * tint)
    out = np.zeros((cv.h, cv.w, 4), np.uint8)
    out[..., :3] = np.clip(rgb * 255 + 0.5, 0, 255).astype(np.uint8)
    out[..., 3] = alpha.astype(np.uint8) * 255
    return out


# ------------------------------------------------------------------ public API

@dataclass
class RenderResult:
    frames: list[np.ndarray]   # RGBA uint8, EMOJI_SIZE x EMOJI_SIZE
    fps: int
    animated: bool
    slim: bool


def _outline_mode(settings: RenderSettings, style: Style) -> str:
    return style.outline if settings.outline == "auto" else settings.outline


def _pick_scale(extent: float, texel: float, avail: float) -> tuple[float, int]:
    """(native scale g, integer upscale k) so a head texel is a whole number of pixels."""
    f_max = avail / extent
    t_max = max(1, int(math.floor(texel * f_max + 1e-6)))
    lo = max(1, math.ceil(0.9 * t_max))
    for t in range(t_max, lo - 1, -1):
        for k in range(t, 1, -1):
            if t % k == 0 and t // k >= texel:
                return (t // k) / texel, k
    return t_max / texel, 1


def render(skin: Skin, settings: RenderSettings, size: int = EMOJI_SIZE) -> RenderResult:
    rig = Rig(skin, settings)
    style = rig.style
    poses, fps = rig.poses()
    frames = [rig.frame(p) for p in poses]
    outline = _outline_mode(settings, style)
    ow = style.outline_width if outline != "none" else 0

    # union bounding box at scale 1 (screen space)
    cam = rig.cam
    minx = miny = math.inf
    maxx = maxy = -math.inf
    for fr in frames:
        c = fr.corners
        sx = c[:, 0] + cam.a * c[:, 2]
        sy = -c[:, 1] + cam.b * c[:, 2]
        top = float(sy.min())
        bot = float(sy.max()) if fr.cut is None else min(float(sy.max()), fr.cut)
        minx, maxx = min(minx, float(sx.min())), max(maxx, float(sx.max()))
        miny, maxy = min(miny, top), max(maxy, bot)
    ext_w = maxx - minx + 2 * ow
    ext_h = maxy - miny + 2 * ow
    extent = max(ext_w, ext_h, 1.0)

    hd = settings.mode == "hd"
    if hd:
        f = (size - 2) / extent
        ss = SUPERSAMPLE if size <= 128 else SUPERSAMPLE_LARGE
        g, k = f * ss, 1
        width = max(1, round(ow * g * 0.7)) if ow else 0
    else:
        g, k = _pick_scale(extent, style.head_texel, size)
        width = max(1, round(ow * g)) if ow else 0

    pad = width + 3
    cw = int(math.ceil(ext_w * g)) + 2 * pad
    chh = int(math.ceil(ext_h * g)) + 2 * pad
    if hd:
        cw += (-cw) % ss
        chh += (-chh) % ss
    ox = pad + (ow - minx) * g
    oy = pad + (ow - miny) * g
    if not hd:
        # snap the head box to the pixel grid so its texels land on whole pixels
        ref = rig.head_anchor()
        hx = g * (ref[0] + cam.a * ref[2]) + ox
        hy = g * (-ref[1] + cam.b * ref[2]) + oy
        ox += round(hx) - hx + 1e-3
        oy += round(hy) - hy + 1e-3

    native = []
    for fr in frames:
        cv = Canvas(cw, chh)
        for q in fr.quads:
            cv.draw(q, cam, g, ox, oy)
        img = _post(cv, style, outline, width, fr.tint)
        if fr.cut is not None:
            cut_px = int(math.floor(fr.cut * g + oy + 0.5))
            img[max(0, cut_px):] = 0
        native.append(img)

    # tight union crop over all frames
    alpha_any = np.zeros((chh, cw), bool)
    for img in native:
        alpha_any |= img[..., 3] > 0
    ys, xs = np.nonzero(alpha_any)
    if len(xs) == 0:
        raise RenderError("пустой кадр — нечего рисовать")
    if hd:
        s = ss
        x0, x1 = (xs.min() // s) * s, ((xs.max() // s) + 1) * s
        y0, y1 = (ys.min() // s) * s, ((ys.max() // s) + 1) * s
    else:
        x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1

    out_frames = []
    for img in native:
        crop = img[y0:y1, x0:x1]
        if hd:
            crop = _downsample(crop, ss)
        elif k > 1:
            crop = np.repeat(np.repeat(crop, k, 0), k, 1)
        out_frames.append(_place(crop, size, smooth=hd))
    return RenderResult(out_frames, fps, settings.animated, rig.slim)


def _downsample(img: np.ndarray, s: int) -> np.ndarray:
    h, w = img.shape[:2]
    f = img.astype(np.float32) / 255.0
    a = f[..., 3:4]
    pm = np.concatenate([f[..., :3] * a, a], -1)
    pm = pm.reshape(h // s, s, w // s, s, 4).mean(axis=(1, 3))
    a2 = pm[..., 3:4]
    rgb = pm[..., :3] / np.maximum(a2, 1e-6)
    out = np.concatenate([rgb, a2], -1)
    return np.clip(out * 255 + 0.5, 0, 255).astype(np.uint8)


def _place(img: np.ndarray, size: int, smooth: bool) -> np.ndarray:
    h, w = img.shape[:2]
    if h > size or w > size:
        from PIL import Image
        sc = min(size / w, size / h)
        nw, nh = max(1, int(w * sc)), max(1, int(h * sc))
        im = Image.fromarray(img, "RGBA").resize((nw, nh), Image.LANCZOS if smooth else Image.NEAREST)
        img = np.array(im)
        h, w = img.shape[:2]
    out = np.zeros((size, size, 4), np.uint8)
    y0, x0 = (size - h) // 2, (size - w) // 2
    out[y0:y0 + h, x0:x0 + w] = img
    return out


def effective_animation(settings: RenderSettings) -> RenderSettings:
    """Poses and animations are exclusive; the all-fours clips use the all-fours stance."""
    if settings.animated and settings.anim in FOURS_ANIMATIONS:
        return settings.copy(pose="fours")
    return settings
