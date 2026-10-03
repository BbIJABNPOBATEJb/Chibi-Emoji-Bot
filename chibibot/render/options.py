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

# ---------------------------------------------------------------- horses (see horse.py)

MOUNTS = [
    Opt("none", "Без лошади", "No horse", "🚶"),
    Opt("ride", "Верхом", "Riding", "🏇"),
    Opt("solo", "Только лошадь", "Horse only", "🐴"),
]

COATS = [
    Opt("white", "Белая", "White", "🤍"),
    Opt("creamy", "Кремовая", "Creamy", "🍦"),
    Opt("chestnut", "Рыжая", "Chestnut", "🦊"),
    Opt("brown", "Коричневая", "Brown", "🤎"),
    Opt("black", "Чёрная", "Black", "🖤"),
    Opt("gray", "Серая", "Gray", "🐘"),
    Opt("darkbrown", "Тёмно-коричневая", "Dark Brown", "🟫"),
    Opt("donkey", "Ослик", "Donkey", "🐴"),
    Opt("mule", "Мул", "Mule", "🐴"),
    Opt("skeleton", "Скелет", "Skeleton", "💀"),
    Opt("zombie", "Зомби", "Zombie", "🧟"),
]
# coats that take markings (donkeys, mules and undead horses have none in the game)
MARKED_COATS = {"white", "creamy", "chestnut", "brown", "black", "gray", "darkbrown"}

MARKS = [
    Opt("none", "Без отметин", "None", "⬜"),
    Opt("white", "Носочки и проточина", "White socks", "🧦"),
    Opt("whitefield", "Белые пятна", "White field", "🐄"),
    Opt("whitedots", "Белые крапинки", "White dots", "❄️"),
    Opt("blackdots", "Чёрные пятна", "Black dots", "⚫"),
]

TACKS = [
    Opt("none", "Без седла", "Nothing", "🐎"),
    Opt("saddle", "Седло", "Saddle", "🪑"),
    Opt("chest", "Седло и сундуки", "Saddle & chests", "📦"),
    Opt("leather", "Кожаная броня", "Leather armor", "🟤"),
    Opt("iron", "Железная броня", "Iron armor", "⚪"),
    Opt("gold", "Золотая броня", "Gold armor", "🟡"),
    Opt("diamond", "Алмазная броня", "Diamond armor", "💎"),
]

HORSE_SIZES = [
    Opt("foal", "Жеребёнок", "Foal", "🐣"),
    Opt("normal", "Обычная", "Normal", "🐴"),
    Opt("big", "Большая", "Big", "🏔"),
]

# what the rider does in the static picture (legs always ride)
RIDER_POSES = [
    Opt("stand", "Держит поводья", "Reins", "🏇"),
    Opt("wave", "Машет", "Waving", "👋"),
    Opt("hand", "Тянет руку", "Reaching", "🤝"),
    Opt("zombie", "Зомби", "Zombie", "🧟"),
    Opt("tpose", "Руки в стороны", "Arms out", "✝️"),
    Opt("dab", "Дэб", "Dab", "😎"),
    Opt("plead", "Умоляет", "Pleading", "🥺"),
]

HORSE_ANIMATIONS = [
    Opt("none", "Без анимации", "None", "⏹"),
    Opt("hidle", "Стоит", "Idle", "😌"),
    Opt("hwalk", "Шагом", "Walk", "🚶"),
    Opt("htrot", "Рысью", "Trot", "🐎"),
    Opt("hgallop", "Галопом", "Gallop", "🏇"),
    Opt("hrear", "На дыбы", "Rear", "🦄"),
    Opt("hjump", "Прыжок", "Jump", "⤴️"),
    Opt("heat", "Щиплет траву", "Graze", "🌿"),
    Opt("hshake", "Трясёт гривой", "Mane shake", "💫"),
    Opt("hbuck", "Брыкается", "Buck", "💥"),
    Opt("hwave", "Привет", "Hello", "👋"),
    Opt("hspin", "Кружится", "Spin", "🌀"),
]
HORSE_ANIM_KEYS = {o.key for o in HORSE_ANIMATIONS}
# a player animation -> the closest horse one (when the horse is switched on), and back
TO_HORSE = {"none": "none", "idle": "hidle", "walk": "hwalk", "run": "hgallop", "gallop": "hgallop",
            "skip": "htrot", "jump": "hjump", "wave": "hwave", "spin": "hspin", "turn": "hspin",
            "shake": "hshake", "cheer": "hrear", "joy": "hbuck", "bounce": "hbuck"}
FROM_HORSE = {"none": "none", "hidle": "idle", "hwalk": "walk", "htrot": "skip", "hgallop": "run",
              "hrear": "cheer", "hjump": "jump", "heat": "idle", "hshake": "shake", "hbuck": "joy",
              "hwave": "wave", "hspin": "spin"}

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
    "anim": ANIMATIONS + HORSE_ANIMATIONS[1:],
    "cam": CAMERAS,
    "mode": RENDER_MODES,
    "outline": OUTLINES,
    "speed": SPEEDS,
    "mount": MOUNTS,
    "horse": COATS,
    "marks": MARKS,
    "tack": TACKS,
    "hsize": HORSE_SIZES,
}


def anim_options(mount: str) -> list[Opt]:
    """The animation list for the chosen mount: player clips on foot, horse clips with a horse."""
    return ANIMATIONS if mount == "none" else HORSE_ANIMATIONS


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
    # horses: none | ride | solo, the coat being drawn and every coat picked, markings, tack, size
    mount: str = "none"
    horse: str = "chestnut"
    horses: list[str] = field(default_factory=lambda: ["chestnut"])
    marks: str = "none"
    tack: str = "saddle"
    hsize: str = "normal"

    @property
    def animated(self) -> bool:
        return self.anim != "none"

    @property
    def any_animated(self) -> bool:
        return any(a != "none" for a in self.anims)

    @property
    def coats(self) -> list[str | None]:
        """The coats every item is made in (one None when there is no horse)."""
        return list(self.horses) if self.mount != "none" else [None]

    def variant(self, anim: str, horse: str | None = None) -> "RenderSettings":
        """Settings for one concrete item."""
        if horse is None:
            return self.copy(anim=anim, anims=[anim])
        return self.copy(anim=anim, anims=[anim], horse=horse, horses=[horse])

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
        for g in ("style", "body", "pose", "bust", "anim", "cam", "mode", "outline", "speed",
                  "mount", "horse", "marks", "tack", "hsize"):
            if getattr(s, g) not in {o.key for o in GROUPS[g]}:
                setattr(s, g, getattr(defaults, g))
        s.hidden = [p for p in (s.hidden or []) if p in PART_KEYS]
        # animations belong to the mount: player clips on foot, horse clips with a horse
        valid = anim_options(s.mount)
        keys = {o.key for o in valid}
        if s.anim not in keys:
            s.anim = (TO_HORSE if s.mount != "none" else FROM_HORSE).get(s.anim, "none")
        # settings saved before multi-select had no "anims": they meant just their one anim
        chosen = set(d.get("anims") or []) if isinstance(d.get("anims"), list) else set()
        s.anims = [o.key for o in valid if o.key in chosen] or [s.anim]
        coats = set(d.get("horses") or []) if isinstance(d.get("horses"), list) else set()
        s.horses = [o.key for o in COATS if o.key in coats] or [s.horse]
        if s.horse not in s.horses:
            s.horse = s.horses[0]
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
        if self.mount != "none":
            parts.append(f"{opt('mount', self.mount).ru}: {opt('horse', self.horse).ru.lower()}")
        if self.animated:
            parts.append(f"Анимация: {opt('anim', self.anim).ru}")
        elif self.mount != "solo":
            parts.append(f"Поза: {opt('pose', self.pose).ru}")
        if self.mount == "none":
            parts.append(f"Кадр: {opt('bust', self.bust).ru}")
        parts.append(f"Камера: {opt('cam', self.cam).ru}")
        return ", ".join(parts)
