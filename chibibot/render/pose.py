"""Pose presets and animation clips.

A Pose holds raw Euler angles per joint (degrees, applied Z then X then Y) plus a few
whole-figure extras. The helpers below speak in friendlier terms:

* limb(side, fwd, out): fwd > 0 swings a hanging arm/leg forward, out > 0 lifts it sideways.
* upr(nod, turn, tilt): nod > 0 tips the head/torso forward, turn > 0 turns it to the
  character's left, tilt > 0 leans it towards the right shoulder.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

from .model import Style

Rot = tuple[float, float, float]


@dataclass
class Pose:
    rot: dict[str, Rot] = field(default_factory=dict)
    off: dict[str, tuple[float, float, float]] = field(default_factory=dict)
    lift: float = 0.0      # px above the ground, added after grounding
    spin: float = 0.0      # extra yaw of the whole figure (deg)
    tint: float = 0.0      # 0..1 red "hurt" flash
    ground: bool = True


def limb(side: str, fwd: float = 0, out: float = 0, twist: float = 0) -> Rot:
    return (-fwd, twist, -out if side == "r" else out)


def upr(nod: float = 0, turn: float = 0, tilt: float = 0) -> Rot:
    return (nod, turn, tilt)


def arms(p: Pose, fwd_r=0, out_r=0, fwd_l=0, out_l=0, tw_r=0, tw_l=0) -> Pose:
    p.rot["rarm"] = limb("r", fwd_r, out_r, tw_r)
    p.rot["larm"] = limb("l", fwd_l, out_l, tw_l)
    return p


def legs(p: Pose, fwd_r=0, fwd_l=0, out_r=0, out_l=0) -> Pose:
    p.rot["rleg"] = limb("r", fwd_r, out_r)
    p.rot["lleg"] = limb("l", fwd_l, out_l)
    return p


def up_out(style: Style, angle: float) -> float:
    """Sideways arm lift, capped for Live where a raised hand would vanish behind the head."""
    if style.key == "live":
        return min(angle, 95.0) if angle <= 140 else 95.0 - (angle - 140) * 0.3
    return angle


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def ease(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return t * t * (3 - 2 * t)


def seg(t: float, a: float, b: float) -> float:
    """0..1 progress of t through [a, b], clamped."""
    if b <= a:
        return 1.0 if t >= b else 0.0
    return min(1.0, max(0.0, (t - a) / (b - a)))


def wave(t: float, n: float = 1.0, phase: float = 0.0) -> float:
    return math.sin(2 * math.pi * (n * t + phase))


# ---------------------------------------------------------------- static poses

def fours_lean(style: Style) -> float:
    """Torso pitch that puts hands and feet on the same floor."""
    reach = style.shoulder_y - style.hip_y
    c = (style.arm_len - style.hip_y) / reach if reach else 0
    return math.degrees(math.acos(min(1.0, max(0.0, c))))


def pose_fours(style: Style, extra_lean: float = 0.0) -> Pose:
    lean = fours_lean(style) + extra_lean
    p = Pose()
    p.rot["body"] = upr(nod=lean)
    p.rot["head"] = upr(nod=-lean + 8)
    arms(p, fwd_r=lean, fwd_l=lean)
    return p


def static_pose(key: str, style: Style) -> Pose:
    # Static poses keep the head square to the camera wherever they can: a rotated
    # 8x8 face stops being crisp pixel art.
    p = Pose()
    if key == "wave":
        arms(p, out_l=up_out(style, 118), fwd_l=12, out_r=4)
    elif key == "hand":
        arms(p, fwd_r=80, out_r=-6)
    elif key == "zombie":
        arms(p, fwd_r=90, fwd_l=90)
    elif key == "tpose":
        arms(p, out_r=90, out_l=90)
    elif key == "sit":
        legs(p, fwd_r=90, fwd_l=90)
        arms(p, fwd_r=25, fwd_l=25)
    elif key == "dab":
        arms(p, out_r=up_out(style, 125), fwd_r=15, fwd_l=100, out_l=-62)
        p.rot["head"] = upr(nod=30)
    elif key == "crouch":
        p.rot["body"] = upr(nod=28)
        p.rot["head"] = upr(nod=-28)
        arms(p, fwd_r=18, fwd_l=18)
        legs(p, fwd_r=-12, fwd_l=-12)
    elif key == "fours":
        p = pose_fours(style)
    elif key == "plead":
        arms(p, fwd_r=62, out_r=-30, fwd_l=62, out_l=-30)
    return p


# ---------------------------------------------------------------- animations

def a_idle(t, s):
    b = wave(t)
    p = arms(Pose(), out_r=3 + 2 * b, out_l=3 + 2 * b)
    p.off["body"] = (0, 0.5 + 0.5 * b, 0)
    p.rot["head"] = upr(nod=2 * wave(t, 1, 0.25))
    return p


def a_walk(t, s):
    w = wave(t)
    p = legs(arms(Pose(), fwd_r=-28 * w, fwd_l=28 * w), fwd_r=30 * w, fwd_l=-30 * w)
    p.rot["head"] = upr(nod=-2 * abs(w))
    p.off["body"] = (0, 0.4 * abs(wave(t, 1, 0.25)), 0)
    return p


def a_run(t, s):
    w = wave(t)
    p = legs(arms(Pose(), fwd_r=-60 * w + 10, fwd_l=60 * w + 10, out_r=6, out_l=6), fwd_r=50 * w, fwd_l=-50 * w)
    p.rot["body"] = upr(nod=12)
    p.rot["head"] = upr(nod=-10)
    p.lift = 1.2 * abs(wave(t, 1, 0.25))
    return p


def a_skip(t, s):
    w = wave(t)
    p = legs(arms(Pose(), fwd_r=-40 * w, fwd_l=40 * w, out_r=10, out_l=10),
             fwd_r=max(0.0, 55 * w), fwd_l=max(0.0, -55 * w))
    p.lift = 2.5 * abs(wave(t, 1))
    p.rot["head"] = upr(tilt=5 * w)
    return p


def a_jump(t, s):
    crouch = ease(seg(t, 0.0, 0.18)) - ease(seg(t, 0.18, 0.28)) + ease(seg(t, 0.72, 0.8)) - ease(seg(t, 0.8, 0.95))
    air = seg(t, 0.28, 0.72)
    h = 4 * air * (1 - air) * 9 if 0 < air < 1 else 0.0
    arms_up = ease(seg(t, 0.2, 0.32)) - ease(seg(t, 0.62, 0.78))
    p = arms(Pose(), fwd_r=-25 * crouch + 20 * arms_up, fwd_l=-25 * crouch + 20 * arms_up,
             out_r=up_out(s, 115) * arms_up, out_l=up_out(s, 115) * arms_up)
    legs(p, fwd_r=-15 * crouch + 25 * arms_up, fwd_l=-15 * crouch + 10 * arms_up)
    p.rot["body"] = upr(nod=18 * crouch)
    p.rot["head"] = upr(nod=-14 * crouch - 8 * arms_up)
    p.off["body"] = (0, -1.2 * crouch, 0)
    p.lift = h
    return p


def a_wave(t, s):
    p = arms(Pose(), out_l=up_out(s, 108) + 20 * wave(t, 2), fwd_l=12, out_r=4)
    p.rot["head"] = upr(tilt=-7 * wave(t, 1), turn=6)
    return p


def a_nod(t, s):
    p = Pose()
    k = 0.5 - 0.5 * math.cos(4 * math.pi * t)
    p.rot["head"] = upr(nod=22 * k)
    p.off["body"] = (0, -0.3 * k, 0)
    return p


def a_shake(t, s):
    p = Pose()
    p.rot["head"] = upr(turn=32 * wave(t, 2))
    return p


def a_clap(t, s):
    k = 0.5 + 0.5 * math.cos(2 * math.pi * t)  # 1 = apart, 0 = together
    inward = lerp(-24, 8, k)
    p = arms(Pose(), fwd_r=72, fwd_l=72, out_r=inward, out_l=inward)
    p.rot["head"] = upr(nod=-4 + 4 * (1 - k))
    return p


def a_cheer(t, s):
    w = wave(t, 2)
    p = arms(Pose(), out_r=up_out(s, 118) + 14 * w, out_l=up_out(s, 118) - 14 * w, fwd_r=12, fwd_l=12)
    p.lift = 1.8 * abs(wave(t, 1))
    p.rot["head"] = upr(nod=-10)
    return p


def a_sulk(t, s):
    w = wave(t)
    p = arms(Pose(), fwd_r=6, fwd_l=6, out_r=-6, out_l=-6)
    p.rot["head"] = upr(nod=30 + 3 * w, turn=6 * wave(t, 1, 0.25))
    p.rot["body"] = upr(nod=10, tilt=2 * w)
    p.off["body"] = (0, -0.5, 0)
    return p


def a_joy(t, s):
    w = wave(t)
    up = abs(wave(t, 1))
    top = up_out(s, 120)
    p = arms(Pose(), out_r=lerp(80, top, up), out_l=lerp(80, top, up), fwd_r=12 + 12 * w, fwd_l=12 - 12 * w)
    legs(p, fwd_r=12 * up, fwd_l=-8 * up)
    p.lift = 4.5 * up
    p.rot["head"] = upr(tilt=10 * w, nod=-8)
    return p


def a_surprise(t, s):
    k = ease(seg(t, 0.0, 0.08)) - ease(seg(t, 0.62, 0.9))
    jolt = math.sin(math.pi * seg(t, 0.0, 0.16))
    p = arms(Pose(), out_r=70 * k, out_l=70 * k, fwd_r=20 * k, fwd_l=20 * k)
    p.rot["head"] = upr(nod=-16 * k)
    p.rot["body"] = upr(nod=-8 * k)
    p.lift = 2.5 * jolt
    return p


def a_bow(t, s):
    k = ease(seg(t, 0.05, 0.38)) - ease(seg(t, 0.62, 0.95))
    p = arms(Pose(), fwd_r=8 * k, fwd_l=8 * k, out_r=-4 * k, out_l=-4 * k)
    p.rot["body"] = upr(nod=48 * k)
    p.rot["head"] = upr(nod=12 * k)
    return p


def a_bounce(t, s):
    k = math.sin(math.pi * t)
    squat = max(0.0, math.cos(math.pi * t)) ** 6
    p = arms(Pose(), out_r=8 + 18 * k, out_l=8 + 18 * k)
    p.lift = 4.0 * k
    p.off["body"] = (0, -1.0 * squat, 0)
    return p


def a_dance(t, s):
    beat = wave(t, 2)
    side = wave(t, 1)
    hi = up_out(s, 150) - 90
    p = arms(Pose(), out_r=90 + hi * max(0.0, side), out_l=90 + hi * max(0.0, -side),
             fwd_r=20 * beat, fwd_l=-20 * beat)
    p.rot["body"] = upr(tilt=8 * side)
    p.rot["head"] = upr(tilt=-10 * side, nod=5 * beat)
    legs(p, fwd_r=10 * side, fwd_l=-10 * side, out_r=6, out_l=6)
    p.lift = 1.2 * abs(beat)
    return p


def a_shiver(t, s):
    j = 1 if int(t * 16) % 2 == 0 else -1
    p = arms(Pose(), fwd_r=45, fwd_l=45, out_r=-35, out_l=-35)
    p.rot["head"] = upr(nod=8, turn=4 * j)
    p.off["root"] = (0.6 * j, 0, 0)
    return p


def a_turn(t, s):
    k = ease(seg(t, 0.05, 0.35)) - ease(seg(t, 0.6, 0.9))
    p = Pose(spin=-180 * k)
    p.rot["head"] = upr(turn=-15 * math.sin(math.pi * k))
    return p


def a_spin(t, s):
    p = arms(Pose(spin=-360 * t), out_r=25, out_l=25)
    return p


def a_stab(t, s):
    wind = ease(seg(t, 0.0, 0.3)) - ease(seg(t, 0.3, 0.38))
    thrust = ease(seg(t, 0.3, 0.4)) - ease(seg(t, 0.6, 0.9))
    p = arms(Pose(), fwd_r=-35 * wind + 95 * thrust, out_r=-6 * thrust, fwd_l=-10 * thrust)
    p.rot["body"] = upr(nod=10 * thrust - 4 * wind, turn=-10 * thrust)
    legs(p, fwd_r=15 * thrust, fwd_l=-8 * thrust)
    return p


def a_death(t, s):
    fall = ease(seg(t, 0.08, 0.4)) - ease(seg(t, 0.86, 1.0))
    hurt = 1.0 if t < 0.5 else max(0.0, 1 - (t - 0.5) / 0.2)
    stagger = math.sin(math.pi * seg(t, 0.0, 0.1))
    p = arms(Pose(), out_r=10 + 20 * fall, out_l=10 + 20 * fall)
    p.rot["root"] = (0, 0, -90 * fall)
    p.rot["head"] = upr(nod=-10 * stagger)
    p.tint = 0.75 * hurt if t < 0.86 else 0.0
    return p


def _fours_walk(t, s, amp, lean_extra=0.0, bob=0.0):
    w = wave(t)
    p = pose_fours(s, lean_extra)
    lean = fours_lean(s) + lean_extra
    arms(p, fwd_r=lean + amp * w, fwd_l=lean - amp * w)
    legs(p, fwd_r=-amp * w, fwd_l=amp * w)
    p.lift = bob * abs(wave(t, 1, 0.25))
    return p


def a_prowl(t, s):
    p = _fours_walk(t, s, 16, lean_extra=6)
    p.rot["head"] = upr(nod=-fours_lean(s) + 18, turn=8 * wave(t, 1))
    return p


def a_crawl(t, s):
    return _fours_walk(t, s, 28, bob=0.4)


def a_gallop(t, s):
    w = wave(t)
    p = pose_fours(s)
    lean = fours_lean(s)
    arms(p, fwd_r=lean + 35 * w, fwd_l=lean + 30 * w)
    legs(p, fwd_r=-35 * w, fwd_l=-30 * w)
    p.rot["body"] = upr(nod=lean + 8 * w)
    p.rot["head"] = upr(nod=-lean + 8 - 8 * w)
    p.lift = 2.0 * max(0.0, wave(t, 1, 0.25))
    return p


def a_pounce(t, s):
    crouch = ease(seg(t, 0.0, 0.3)) - ease(seg(t, 0.3, 0.4))
    air = seg(t, 0.36, 0.66)
    h = 4 * air * (1 - air) * 8 if 0 < air < 1 else 0.0
    reach = math.sin(math.pi * air) if 0 < air < 1 else 0.0
    p = pose_fours(s, 6 * crouch)
    lean = fours_lean(s)
    arms(p, fwd_r=lean + 6 * crouch + 60 * reach, fwd_l=lean + 6 * crouch + 60 * reach)
    legs(p, fwd_r=-35 * reach, fwd_l=-35 * reach)
    p.off["root"] = (0, -1.5 * crouch, 2.5 * reach)
    p.lift = h
    return p


ANIMS: dict[str, tuple[float, Callable[[float, Style], Pose]]] = {
    "idle": (2.0, a_idle),
    "walk": (1.0, a_walk),
    "run": (0.7, a_run),
    "skip": (1.0, a_skip),
    "jump": (1.3, a_jump),
    "wave": (1.2, a_wave),
    "nod": (1.4, a_nod),
    "shake": (1.2, a_shake),
    "clap": (0.6, a_clap),
    "cheer": (1.2, a_cheer),
    "sulk": (2.4, a_sulk),
    "joy": (1.0, a_joy),
    "surprise": (2.2, a_surprise),
    "bow": (2.2, a_bow),
    "bounce": (0.8, a_bounce),
    "dance": (1.6, a_dance),
    "shiver": (1.0, a_shiver),
    "turn": (2.6, a_turn),
    "spin": (1.6, a_spin),
    "stab": (1.1, a_stab),
    "death": (2.8, a_death),
    "prowl": (1.8, a_prowl),
    "crawl": (1.2, a_crawl),
    "gallop": (0.8, a_gallop),
    "pounce": (1.8, a_pounce),
}


def anim_pose(key: str, t: float, style: Style) -> Pose:
    return ANIMS[key][1](t % 1.0, style)
