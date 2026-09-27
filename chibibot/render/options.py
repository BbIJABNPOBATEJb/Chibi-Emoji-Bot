"""Every user-facing render option, its key, and its labels.

Keys are short and stable: they are stored in the database and packed into
Telegram callback data (64 bytes max), so never rename an existing key.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields


@dataclass(frozen=True)
class Opt:
    key: str
    ru: str
    en: str
    icon: str = ""


STYLES = [
    Opt("classic", "Классика", "Classic", "🧸"),
    Opt("live", "Minecraft Live", "Minecraft Live", "📺"),
]

BODIES = [
    Opt("auto", "Авто", "Auto", "🪄"),
    Opt("steve", "Стив", "Steve", "💪"),
    Opt("alex", "Алекс", "Alex", "🤏"),
]

POSES = [
    Opt("stand", "Стоит", "Standing", "🧍"),
    Opt("wave", "Машет", "Waving", "👋"),
    Opt("hand", "Рукопожатие", "Handshake", "🤝"),
    Opt("zombie", "Зомби", "Zombie", "🧟"),
    Opt("tpose", "T-поза", "T-Pose", "✝️"),
    Opt("sit", "Сидит", "Sitting", "🪑"),
    Opt("dab", "Дэб", "Dab", "😎"),
    Opt("crouch", "Присел", "Crouching", "🥷"),
    Opt("fours", "На четвереньках", "All Fours", "🐾"),
    Opt("plead", "Умоляет", "Pleading", "🥺"),
]

BUSTS = [
    Opt("full", "Во весь рост", "Full Body", "🧍"),
    Opt("half", "По пояс", "Half Body", "👕"),
    Opt("portrait", "Портрет", "Portrait", "🖼"),
    Opt("head", "Голова", "Head", "🙂"),
]

ANIMATIONS = [
    Opt("none", "Без анимации", "None", "⏹"),
    Opt("idle", "Дыхание", "Idle", "😌"),
    Opt("walk", "Ходьба", "Walk", "🚶"),
    Opt("run", "Бег", "Run", "🏃"),
    Opt("skip", "Вприпрыжку", "Skip", "🦘"),
    Opt("jump", "Прыжок", "Jump", "⤴️"),
    Opt("wave", "Привет", "Wave", "👋"),
    Opt("nod", "Кивок", "Nod", "🙆"),
    Opt("shake", "Нет-нет", "Head shake", "🙅"),
    Opt("clap", "Хлопки", "Clap", "👏"),
    Opt("cheer", "Ура", "Cheer", "🙌"),
    Opt("sulk", "Грусть", "Sulk", "😔"),
    Opt("joy", "Радость", "Joy", "🥳"),
    Opt("surprise", "Удивление", "Surprised", "😲"),
    Opt("bow", "Поклон", "Bow", "🙇"),
    Opt("bounce", "Пружинка", "Bounce", "🏀"),
    Opt("dance", "Танец", "Dance", "💃"),
    Opt("shiver", "Дрожь", "Shiver", "🥶"),
    Opt("turn", "Разворот", "Turn around", "↩️"),
    Opt("spin", "Вращение", "Spin", "🌀"),
    Opt("stab", "Удар", "Stab", "🗡"),
    Opt("death", "Смерть", "Death", "💀"),
    Opt("prowl", "Крадётся", "Prowl", "🐈"),
    Opt("crawl", "Ползёт", "Crawl", "🐛"),
    Opt("gallop", "Галоп", "Gallop", "🐎"),
    Opt("pounce", "Бросок", "Pounce", "🐅"),
]

# Clips that stand the figure on all fours instead of returning it to Standing.
FOURS_ANIMATIONS = {"prowl", "crawl", "gallop", "pounce"}

CAMERAS = [
    Opt("34r", "3/4 справа", "3/4 Right", "↗️"),
    Opt("front", "Спереди", "Front", "⬆️"),
    Opt("34l", "3/4 слева", "3/4 Left", "↖️"),
    Opt("sider", "Профиль справа", "Side Right", "➡️"),
    Opt("back", "Сзади", "Back", "⬇️"),
    Opt("sidel", "Профиль слева", "Side Left", "⬅️"),
    Opt("b34r", "3/4 сзади справа", "3/4 Back R", "↘️"),
    Opt("b34l", "3/4 сзади слева", "3/4 Back L", "↙️"),
    Opt("top", "Сверху", "Top", "🔼"),
    Opt("bottom", "Снизу", "Bottom", "🔽"),
]

PARTS = [
    Opt("head", "Голова", "Head", "🙂"),
    Opt("hat", "Слой шляпы", "Hat layer", "🎩"),
    Opt("body", "Тело", "Body", "👕"),
    Opt("jacket", "Слой куртки", "Body layer", "🧥"),
    Opt("rarm", "Правая рука", "Right Arm", "💪"),
    Opt("rsleeve", "Слой пр. рукава", "Right Arm layer", "🧤"),
    Opt("larm", "Левая рука", "Left Arm", "💪"),
    Opt("lsleeve", "Слой лев. рукава", "Left Arm layer", "🧤"),
    Opt("rleg", "Правая нога", "Right Leg", "🦵"),
    Opt("rpants", "Слой пр. штанины", "Right Leg layer", "👖"),
    Opt("lleg", "Левая нога", "Left Leg", "🦵"),
    Opt("lpants", "Слой лев. штанины", "Left Leg layer", "👖"),
]
PART_KEYS = [p.key for p in PARTS]

RENDER_MODES = [
    Opt("pixel", "Пиксель-арт", "Pixel art", "🟫"),
    Opt("hd", "Сглаженный", "Smooth", "🔵"),
]

OUTLINES = [
    Opt("auto", "По стилю", "Style default", "🪄"),
    Opt("none", "Без обводки", "None", "⬜"),
    Opt("figure", "Контур фигуры", "Whole figure", "⭕"),
    Opt("parts", "Каждая часть", "Each part", "🔲"),
]

SPEEDS = [
    Opt("0.5", "0.5×", "0.5x", "🐢"),
    Opt("1", "1×", "1x", "▶️"),
    Opt("1.5", "1.5×", "1.5x", "⏩"),
    Opt("2", "2×", "2x", "⚡"),
]

QUICK_EMOJI = ["🙂", "😀", "😎", "🥰", "😂", "😭", "😡", "👍", "❤️", "🔥", "🎉", "💀"]

GROUPS: dict[str, list[Opt]] = {
    "style": STYLES,
    "body": BODIES,
    "pose": POSES,
    "bust": BUSTS,
    "anim": ANIMATIONS,
    "cam": CAMERAS,
    "mode": RENDER_MODES,
    "outline": OUTLINES,
    "speed": SPEEDS,
}


def opt(group: str, key: str) -> Opt:
    for o in GROUPS[group]:
        if o.key == key:
            return o
    return GROUPS[group][0]


@dataclass
class RenderSettings:
    style: str = "classic"
    body: str = "auto"
    pose: str = "stand"
    bust: str = "full"
    anim: str = "none"                               # the variant being drawn
    cam: str = "34r"
    hidden: list[str] = field(default_factory=list)  # part keys that are switched off
    mode: str = "hd"
    outline: str = "auto"
    speed: str = "1"
    emoji: str = "🙂"
    # Every variant picked in the wizard: each skin becomes one emoji/sticker per entry
    # ("none" = the static pose). Stored per item it is always just [anim].
    anims: list[str] = field(default_factory=lambda: ["none"])

    @property
    def animated(self) -> bool:
        return self.anim != "none"

    @property
    def any_animated(self) -> bool:
        return any(a != "none" for a in self.anims)

    def variant(self, anim: str) -> "RenderSettings":
        """Settings for one concrete item."""
        return self.copy(anim=anim, anims=[anim])

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> "RenderSettings":
        s = cls()
        if not d:
            return s
        names = {f.name for f in fields(cls)}
        for k, v in d.items():
            if k in names:
                setattr(s, k, v)
        # sanitise anything stale or tampered with
        defaults = cls()
        for g in ("style", "body", "pose", "bust", "anim", "cam", "mode", "outline", "speed"):
            if getattr(s, g) not in {o.key for o in GROUPS[g]}:
                setattr(s, g, getattr(defaults, g))
        s.hidden = [p for p in (s.hidden or []) if p in PART_KEYS]
        # settings saved before multi-select had no "anims": they meant just their one anim
        chosen = set(d.get("anims") or []) if isinstance(d.get("anims"), list) else set()
        s.anims = [o.key for o in ANIMATIONS if o.key in chosen] or [s.anim]
        if not isinstance(s.emoji, str) or not s.emoji or len(s.emoji) > 16:
            s.emoji = "🙂"
        return s

    def copy(self, **changes) -> "RenderSettings":
        d = self.to_dict()
        d.update(changes)
        return RenderSettings.from_dict(d)

    def summary_ru(self) -> str:
        parts = [
            f"Стиль: {opt('style', self.style).ru}",
            f"Тело: {opt('body', self.body).ru}",
        ]
        if self.animated:
            parts.append(f"Анимация: {opt('anim', self.anim).ru}")
        else:
            parts.append(f"Поза: {opt('pose', self.pose).ru}")
        parts.append(f"Кадр: {opt('bust', self.bust).ru}")
        parts.append(f"Камера: {opt('cam', self.cam).ru}")
        return ", ".join(parts)
