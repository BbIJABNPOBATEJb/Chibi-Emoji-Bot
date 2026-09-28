"""Telegram custom emoji sets: create, add, replace, reorder, delete — and keep the DB in sync."""
from __future__ import annotations

import asyncio
import logging
import secrets

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import BufferedInputFile, InputSticker, StickerSet

from .db import Database, Emoji, Pack

log = logging.getLogger(__name__)

MAX_TITLE = 64
DEFAULT_EMOJI = "🙂"


class StickerError(RuntimeError):
    pass


def explain(exc: Exception) -> str:
    msg = str(exc)
    table = {
        "STICKERSET_INVALID": "набор не найден в Telegram (возможно, удалён через @Stickers)",
        "STICKERS_TOO_MUCH": "в наборе уже максимум (200 эмодзи или 120 стикеров)",
        "PEER_ID_INVALID": "владелец пака не запускал бота или заблокировал его",
        "USER_IS_BOT": "владельцем пака не может быть бот",
        "STICKER_VIDEO_LONG": "анимация длиннее 3 секунд",
        "STICKER_VIDEO_BIG": "видео получилось слишком тяжёлым для Telegram",
        "STICKER_PNG_DIMENSIONS": "неверный размер картинки",
        "STICKER_EMOJI_INVALID": "Telegram не принял выбранный эмодзи",
        "sticker set name is already occupied": "такое имя набора уже занято",
    }
    for k, v in table.items():
        if k.lower() in msg.lower():
            return v
    return msg


class StickerService:
    def __init__(self, bot: Bot, db: Database, title_suffix: str = "auto"):
        self.bot = bot
        self.db = db
        self.title_suffix = title_suffix
        self._username: str | None = None
        self._locks: dict[int, asyncio.Lock] = {}

    def lock(self, pack_id: int) -> asyncio.Lock:
        return self._locks.setdefault(pack_id, asyncio.Lock())

    async def username(self) -> str:
        if not self._username:
            me = await self.bot.get_me()
            self._username = me.username or "bot"
        return self._username

    # ------------------------------------------------------------------ titles
    async def suffix(self) -> str:
        """What every set title ends with: '@<bot username>' by default (TITLE_SUFFIX in .env)."""
        raw = (self.title_suffix or "").strip()
        if raw.lower() in ("", "none", "off", "0", "-"):
            return ""
        if raw.lower() == "auto":
            return "@" + await self.username()
        return raw[:40]

    async def max_title_len(self) -> int:
        """How long the user's own part of a title may be."""
        sfx = await self.suffix()
        return MAX_TITLE - (len(sfx) + 1 if sfx else 0)

    async def tg_title(self, base: str) -> str:
        """The title as shown in Telegram: the user's title plus the suffix, within 64 characters."""
        sfx = await self.suffix()
        base = " ".join((base or "").split())
        if not sfx:
            return base[:MAX_TITLE] or "Chibi"
        if base.lower().endswith(sfx.lower()):  # the user already typed it
            base = base[: -len(sfx)].rstrip()
        base = base[: MAX_TITLE - len(sfx) - 1].rstrip()
        return f"{base} {sfx}" if base else sfx

    async def fix_title(self, pack: Pack, st: StickerSet | None = None) -> bool:
        """Bring the Telegram title in line with the current suffix; True if it changed."""
        if not pack.tg_created:
            return False
        st = st or await self.get_set(pack.name)
        want = await self.tg_title(pack.title)
        if st is None or st.title == want:
            return False
        try:
            await self._call(self.bot.set_sticker_set_title, name=pack.name, title=want)
            return True
        except TelegramBadRequest as exc:
            log.warning("could not retitle %s: %s", pack.name, exc)
            return False

    async def fix_all_titles(self) -> int:
        """Startup pass: every published pack gets the suffix (existing packs included)."""
        changed, offset = 0, 0
        while True:
            packs = await self.db.all_packs(100, offset)
            if not packs:
                return changed
            offset += len(packs)
            for p in packs:
                if p.tg_created and await self.fix_title(p):
                    changed += 1
                    await asyncio.sleep(0.5)

    async def new_name(self) -> str:
        suffix = f"_by_{await self.username()}"
        return f"chibi{secrets.token_hex(4)}{suffix}"

    async def _call(self, coro_fn, *args, **kwargs):
        for attempt in range(5):
            try:
                return await coro_fn(*args, **kwargs)
            except TelegramRetryAfter as exc:
                log.warning("flood control, sleeping %ss", exc.retry_after)
                await asyncio.sleep(exc.retry_after + 1)
        return await coro_fn(*args, **kwargs)

    async def get_set(self, name: str) -> StickerSet | None:
        try:
            return await self._call(self.bot.get_sticker_set, name=name)
        except TelegramBadRequest as exc:
            if "STICKERSET_INVALID" in str(exc):
                return None
            raise

    async def _input_sticker(self, owner_id: int, fmt: str, data: bytes, emoji: str, keywords: list[str]) -> InputSticker:
        fname = "emoji.webm" if fmt == "video" else "emoji.png"
        uploaded = await self._call(self.bot.upload_sticker_file, user_id=owner_id,
                                    sticker=BufferedInputFile(data, fname), sticker_format=fmt)
        kw = []
        total = 0
        for k in keywords:
            k = k.strip().lower()[:30]
            if k and total + len(k) <= 60 and k not in kw:
                kw.append(k)
                total += len(k)
        return InputSticker(sticker=uploaded.file_id, format=fmt, emoji_list=[emoji], keywords=kw or None)

    async def add(self, pack: Pack, fmt: str, data: bytes, emoji: str, keywords: list[str]):
        """Upload one emoji; creates the Telegram set on first use. -> the new Sticker."""
        sticker = await self._input_sticker(pack.owner_id, fmt, data, emoji, keywords)
        try:
            if not pack.tg_created:
                try:
                    await self._call(self.bot.create_new_sticker_set, user_id=pack.owner_id, name=pack.name,
                                     title=await self.tg_title(pack.title), stickers=[sticker], sticker_type=pack.k.sticker_type)
                except TelegramBadRequest as exc:
                    if "occupied" not in str(exc).lower() and "NAME_INVALID" not in str(exc):
                        raise
                    # a set deleted earlier can keep its name reserved: take a fresh one
                    pack.name = await self.new_name()
                    await self.db.touch_pack(pack.id, pack.updated_by, name=pack.name)
                    await self._call(self.bot.create_new_sticker_set, user_id=pack.owner_id, name=pack.name,
                                     title=await self.tg_title(pack.title), stickers=[sticker], sticker_type=pack.k.sticker_type)
                pack.tg_created = True
                await self.db.touch_pack(pack.id, pack.updated_by, tg_created=1)
            else:
                await self._call(self.bot.add_sticker_to_set, user_id=pack.owner_id, name=pack.name, sticker=sticker)
        except TelegramBadRequest as exc:
            if pack.tg_created and "STICKERSET_INVALID" in str(exc):
                # the set was deleted outside the bot: start it again
                pack.tg_created = False
                await self.db.touch_pack(pack.id, pack.updated_by, tg_created=0)
                return await self.add(pack, fmt, data, emoji, keywords)
            if "EMOJI" in str(exc).upper() and emoji != DEFAULT_EMOJI:
                # Telegram only accepts emoji from its own list; fall back to a safe one
                return await self.add(pack, fmt, data, DEFAULT_EMOJI, keywords)
            raise StickerError(explain(exc)) from exc
        st = await self.get_set(pack.name)
        if not st or not st.stickers:
            raise StickerError("Telegram не вернул набор после добавления")
        return st.stickers[-1]

    async def sync(self, pack: Pack) -> list[Emoji]:
        """Bring DB order/file ids in line with Telegram; drop rows deleted outside the bot."""
        rows = await self.db.pack_emojis(pack.id)
        if not pack.tg_created:
            return rows
        st = await self.get_set(pack.name)
        if st is None:
            if rows:
                await self.db.delete_emojis([e.id for e in rows])
            await self.db.touch_pack(pack.id, pack.updated_by, tg_created=0)
            pack.tg_created = False
            return []
        await self.fix_title(pack, st)
        by_uid = {e.file_unique_id: e for e in rows if e.file_unique_id}
        order, seen = [], set()
        for pos, s in enumerate(st.stickers):
            e = by_uid.get(s.file_unique_id)
            if e:
                order.append((e.id, pos, s.file_id))
                seen.add(e.id)
        gone = [e.id for e in rows if e.id not in seen]
        if gone:
            await self.db.delete_emojis(gone)
        if order:
            await self.db.set_positions(order)
        return await self.db.pack_emojis(pack.id)

    async def _fresh_file_id(self, pack: Pack, emoji: Emoji) -> str:
        st = await self.get_set(pack.name)
        if st:
            for s in st.stickers:
                if s.file_unique_id == emoji.file_unique_id:
                    return s.file_id
        if emoji.file_id:
            return emoji.file_id
        raise StickerError("эмодзи не найден в наборе")

    async def delete(self, pack: Pack, emoji: Emoji) -> None:
        fid = await self._fresh_file_id(pack, emoji)
        st = await self.get_set(pack.name)
        if st and len(st.stickers) <= 1:
            # Telegram refuses to empty a set: delete the whole set instead
            await self._call(self.bot.delete_sticker_set, name=pack.name)
            await self.db.touch_pack(pack.id, pack.owner_id, tg_created=0)
            pack.tg_created = False
        else:
            try:
                await self._call(self.bot.delete_sticker_from_set, sticker=fid)
            except TelegramBadRequest as exc:
                raise StickerError(explain(exc)) from exc

    async def replace(self, pack: Pack, emoji: Emoji, fmt: str, data: bytes, emoji_char: str, keywords: list[str]):
        fid = await self._fresh_file_id(pack, emoji)
        sticker = await self._input_sticker(pack.owner_id, fmt, data, emoji_char, keywords)
        try:
            await self._call(self.bot.replace_sticker_in_set, user_id=pack.owner_id, name=pack.name,
                             old_sticker=fid, sticker=sticker)
        except TelegramBadRequest as exc:
            raise StickerError(explain(exc)) from exc
        st = await self.get_set(pack.name)
        if st and 0 <= emoji.position < len(st.stickers):
            return st.stickers[emoji.position]
        # position unknown: find the sticker we do not know yet
        known = {e.file_unique_id for e in await self.db.pack_emojis(pack.id)}
        for s in (st.stickers if st else []):
            if s.file_unique_id not in known:
                return s
        raise StickerError("не удалось найти заменённый эмодзи")

    async def move(self, pack: Pack, emoji: Emoji, position: int) -> None:
        fid = await self._fresh_file_id(pack, emoji)
        try:
            await self._call(self.bot.set_sticker_position_in_set, sticker=fid, position=position)
        except TelegramBadRequest as exc:
            raise StickerError(explain(exc)) from exc

    async def rename(self, pack: Pack, title: str) -> None:
        if not pack.tg_created:
            return
        try:
            await self._call(self.bot.set_sticker_set_title, name=pack.name, title=await self.tg_title(title))
        except TelegramBadRequest as exc:
            raise StickerError(explain(exc)) from exc

    async def delete_set(self, pack: Pack) -> None:
        if not pack.tg_created:
            return
        try:
            await self._call(self.bot.delete_sticker_set, name=pack.name)
        except TelegramBadRequest as exc:
            if "STICKERSET_INVALID" not in str(exc):
                raise StickerError(explain(exc)) from exc
