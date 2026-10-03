"""Chibi figures as boxes.

Units are sprite pixels at scale 1. Axes: +x is the character's left (image right when it
faces you), +y is up, +z points out of the character's chest towards the viewer.
Every part is one box with a Minecraft UV island; the body, arm and leg overlays
(jacket/sleeves/pants) are composited onto their base in skin space so the pixel art
stays aligned, while the Classic hat stays a separate, slightly bigger box.
"""
from __future__ import annotations

from dataclasses import dataclass, field

HEAD, BODY, RARM, LARM, RLEG, LLEG = range(6)
# horse parts (see horse.py): body, head with neck/ears/mane, front legs, hind legs, tail, saddle & co
H_BODY, H_HEAD, H_FRONT, H_HIND, H_TAIL, H_TACK = range(6, 12)
# Who wins an outline tie between two touching parts at the same depth.
GROUP_PRIORITY = {HEAD: 5, RARM: 4, LARM: 4, BODY: 3, RLEG: 2, LLEG: 2,
                  H_HEAD: 2, H_TACK: 2, H_BODY: 1, H_FRONT: 1, H_HIND: 1, H_TAIL: 1}

# box face -> (origin corner, U edge, V edge, outward normal); textures run along U (columns)
# and V (rows), the way Minecraft lays out a box's UV island
FACE_NAMES = ("front", "back", "right", "left", "top", "bottom")


def face_frame(name: str, lo, size):
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


@dataclass(frozen=True)
class Joint:
    parent: str | None
    pivot: tuple[float, float, float]


@dataclass(frozen=True)
class Part:
    key: str
    joint: str
    group: int
    lo: tuple[float, float, float]
    size: tuple[float, float, float]
    tdim: tuple[int, int, int]           # texel width, height, depth of the UV island
    uv: tuple[int, int] | None           # base layer island
    uv_over: tuple[int, int] | None      # second layer island
    vis_base: str | None                 # PART_KEYS entry controlling the base
    vis_over: str | None                 # PART_KEYS entry controlling the overlay


@dataclass(frozen=True)
class Style:
    key: str
    head_texel: float                    # sprite px per head texel: the pixel-art grain
    joints: dict[str, Joint]
    wide: list[Part]
    slim: list[Part]
    slim_joints: dict[str, Joint]
    shade: dict[str, float]
    far_arm: float
    far_leg: float
    outline: str                         # style default: none | figure | parts
    outline_width: float
    ink: float                           # how dark the "colour from the art" ink is
    ao: float                            # contact shadow strength under/behind parts
    hip_y: float
    neck_y: float
    body_h: float
    shoulder_y: float
    arm_len: float                       # shoulder pivot -> hand tip
    extra: dict = field(default_factory=dict)


def _limbs(arm_w, arm_x, arm_y, arm_h, arm_d, leg_w, leg_h, leg_d, slim=False):
    az, lz = -arm_d / 2, -leg_d / 2
    atw = 3 if slim else 4
    return [
        Part("rarm", "rarm", RARM, (-arm_x - arm_w, arm_y, az), (arm_w, arm_h, arm_d),
             (atw, 12, 4), (40, 16), (40, 32), "rarm", "rsleeve"),
        Part("larm", "larm", LARM, (arm_x, arm_y, az), (arm_w, arm_h, arm_d),
             (atw, 12, 4), (32, 48), (48, 48), "larm", "lsleeve"),
        Part("rleg", "rleg", RLEG, (-leg_w, 0, lz), (leg_w, leg_h, leg_d), (4, 12, 4), (0, 16), (0, 32), "rleg", "rpants"),
        Part("lleg", "lleg", LLEG, (0, 0, lz), (leg_w, leg_h, leg_d), (4, 12, 4), (16, 48), (0, 48), "lleg", "lpants"),
    ]


def _classic() -> Style:
    joints = {
        "root": Joint(None, (0, 16, 0)),
        "body": Joint("root", (0, 6, 0)),
        "head": Joint("body", (0, 16, 0)),
        "rarm": Joint("body", (-6, 14.5, 0)),
        "larm": Joint("body", (6, 14.5, 0)),
        "rleg": Joint("root", (-2, 6, 0)),
        "lleg": Joint("root", (2, 6, 0)),
    }
    slim_joints = dict(joints, rarm=Joint("body", (-5.5, 14.5, 0)), larm=Joint("body", (5.5, 14.5, 0)))
    head = [
        Part("head", "head", HEAD, (-8, 16, -8), (16, 16, 16), (8, 8, 8), (0, 0), None, "head", None),
        Part("hat", "head", HEAD, (-9, 15, -9), (18, 18, 18), (8, 8, 8), None, (32, 0), None, "hat"),
    ]
    body = [Part("body", "body", BODY, (-4, 6, -2), (8, 10, 4), (8, 12, 4), (16, 16), (16, 32), "body", "jacket")]
    return Style(
        key="classic",
        head_texel=2,
        joints=joints,
        slim_joints=slim_joints,
        wide=body + _limbs(4, 4, 6, 10, 4, 4, 6, 4) + head,
        slim=body + _limbs(3, 4, 6, 10, 4, 4, 6, 4, slim=True) + head,
        shade={"front": 1.0, "side": 0.84, "top": 1.0, "bottom": 0.72, "back": 0.9},
        far_arm=0.93,
        far_leg=0.86,
        outline="none",
        outline_width=1,
        ink=0.42,
        ao=0.2,
        hip_y=6,
        neck_y=16,
        body_h=10,
        shoulder_y=14.5,
        arm_len=8.5,
    )


def _live() -> Style:
    joints = {
        "root": Joint(None, (0, 14, 0)),
        "body": Joint("root", (0, 4, 0)),
        "head": Joint("body", (0, 9, 0)),
        "rarm": Joint("body", (-9, 8, 0)),
        "larm": Joint("body", (9, 8, 0)),
        "rleg": Joint("root", (-3, 4, 0)),
        "lleg": Joint("root", (3, 4, 0)),
    }
    slim_joints = dict(joints, rarm=Joint("body", (-8.5, 8, 0)), larm=Joint("body", (8.5, 8, 0)))
    head = [Part("head", "head", HEAD, (-12, 9, -12), (24, 24, 24), (8, 8, 8), (0, 0), (32, 0), "head", "hat")]
    body = [Part("body", "body", BODY, (-6, 4, -3), (12, 5, 6), (8, 12, 4), (16, 16), (16, 32), "body", "jacket")]
    return Style(
        key="live",
        head_texel=3,
        joints=joints,
        slim_joints=slim_joints,
        wide=body + _limbs(6, 6, 2, 7, 6, 6, 4, 6) + head,
        slim=body + _limbs(5, 6, 2, 7, 6, 6, 4, 6, slim=True) + head,
        shade={"front": 1.0, "side": 0.67, "top": 0.92, "bottom": 0.55, "back": 0.85},
        far_arm=0.808,
        far_leg=0.808,
        outline="parts",
        outline_width=1,
        ink=0.3,
        ao=0.0,
        hip_y=4,
        neck_y=9,
        body_h=5,
        shoulder_y=8,
        arm_len=6,
    )


STYLES: dict[str, Style] = {"classic": _classic(), "live": _live()}
