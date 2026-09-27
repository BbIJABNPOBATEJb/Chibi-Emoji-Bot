"""The two kinds of packs the bot builds: custom emoji and regular stickers.

Everything that differs between them lives here; the rest of the bot is shared.
"""
from __future__ import annotations

from dataclasses import dataclass

EMOJI = "emoji"
STICKERS = "stickers"


def _plural(n: int, one: str, few: str, many: str) -> str:
    n10, n100 = n % 10, n % 100
    if n10 == 1 and n100 != 11:
        return one
    if 2 <= n10 <= 4 and not 12 <= n100 <= 14:
        return few
    return many


@dataclass(frozen=True)
class Kind:
    key: str
    sticker_type: str      # Telegram sticker_type
    size: int              # rendered side, px (Telegram: 100 for emoji, 512 for stickers)
    max_items: int         # Telegram's per-set limit
    link_prefix: str       # t.me/<prefix>/<set name>
    icon: str
    name: str              # "эмодзи-пак"
    items: str             # "Эмодзи" (heading)
    forms: tuple[str, str, str]   # 1 / 2 / 5 items
    about: str

    def count(self, n: int) -> str:
        return f"{n} {_plural(n, *self.forms)}"

    def link(self, set_name: str) -> str:
        return f"https://t.me/{self.link_prefix}/{set_name}"


KINDS: dict[str, Kind] = {
    EMOJI: Kind(
        key=EMOJI, sticker_type="custom_emoji", size=100, max_items=200, link_prefix="addemoji", icon="😀",
        name="эмодзи-пак", items="Эмодзи", forms=("эмодзи", "эмодзи", "эмодзи"),
        about="маленькие эмодзи прямо в тексте сообщений; отправлять могут пользователи Telegram Premium",
    ),
    STICKERS: Kind(
        key=STICKERS, sticker_type="regular", size=512, max_items=120, link_prefix="addstickers", icon="🖼",
        name="стикер-пак", items="Стикеры", forms=("стикер", "стикера", "стикеров"),
        about="обычные стикеры 512×512 — работают у всех, Premium не нужен",
    ),
}


def kind_of(key: str | None) -> Kind:
    return KINDS.get(key or EMOJI, KINDS[EMOJI])
