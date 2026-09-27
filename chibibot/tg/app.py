"""Shared services for the handlers, injected by aiogram as the `app` argument."""
from __future__ import annotations

import asyncio
import html
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..config import Config
from ..db import Database, Pack, User, ago
from ..jobs import Worker
from ..sources import MojangClient, SkinStore
from ..stickers import StickerService


class MediaCache:
    """Telegram file ids of example sheets we already uploaded (bounded LRU)."""

    def __init__(self, size: int = 500):
        self.size = size
        self.data: OrderedDict[str, tuple[str, str]] = OrderedDict()

    def get(self, key: str) -> tuple[str, str] | None:
        v = self.data.get(key)
        if v:
            self.data.move_to_end(key)
        return v

    def put(self, key: str, kind: str, file_id: str) -> None:
        self.data[key] = (kind, file_id)
        self.data.move_to_end(key)
        while len(self.data) > self.size:
            self.data.popitem(last=False)


@dataclass
class Quota:
    """Where a user stands against the pack limit and the rolling 24-hour emoji quota."""
    pack_limit: int | None       # None = unlimited
    published: int
    drafts: int
    daily_limit: int | None      # None = unlimited
    used: int                    # emojis created/redrawn in the last 24 hours
    next_free: datetime | None   # when the next emoji slot frees up (quota exhausted only)

    @property
    def left(self) -> int | None:
        return None if self.daily_limit is None else max(0, self.daily_limit - self.used)


def _effective(personal: int | None, default: int) -> int | None:
    """Personal override wins; 0 means unlimited; the .env default 0 also means unlimited."""
    value = default if personal is None else personal
    return None if value <= 0 else value


@dataclass
class App:
    cfg: Config
    db: Database
    worker: Worker
    stickers: StickerService
    mojang: MojangClient
    store: SkinStore
    media: MediaCache = field(default_factory=MediaCache)
    claim_code: str | None = None
    _locks: dict[int, asyncio.Lock] = field(default_factory=dict)
    _names: dict[int, str] = field(default_factory=dict)

    def __post_init__(self):
        try:
            self.tz = ZoneInfo(self.cfg.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            self.tz = None

    def lock(self, uid: int) -> asyncio.Lock:
        return self._locks.setdefault(uid, asyncio.Lock())

    async def is_admin(self, uid: int) -> bool:
        if uid in self.cfg.admin_ids:
            return True
        u = await self.db.get_user(uid)
        return bool(u and u.is_admin)

    async def can_edit(self, uid: int, pack: Pack) -> bool:
        return pack.owner_id == uid or await self.is_admin(uid)

    def time(self, iso: str | None) -> str:
        if not iso:
            return "—"
        try:
            dt = datetime.fromisoformat(iso)
        except ValueError:
            return iso
        if self.tz:
            dt = dt.astimezone(self.tz)
        return dt.strftime("%d.%m.%Y %H:%M")

    async def who(self, uid: int | None) -> str:
        if uid is None:
            return "—"
        if uid in self._names:
            return self._names[uid]
        u = await self.db.get_user(uid)
        name = html.escape(u.display) if u else f"id{uid}"
        self._names[uid] = name
        return name

    def forget_name(self, user: User) -> None:
        self._names.pop(user.id, None)

    def batch_limit(self, admin: bool) -> int:
        return self.cfg.max_batch_admin if admin else self.cfg.max_batch_user

    # ------------------------------------------------------------------ limits
    async def quota(self, uid: int, admin: bool | None = None) -> Quota:
        if admin is None:
            admin = await self.is_admin(uid)
        u = await self.db.get_user(uid)
        published = await self.db.count_published(uid)
        drafts = await self.db.count_drafts(uid)
        if admin:
            return Quota(None, published, drafts, None, 0, None)
        pack_limit = _effective(u.pack_limit if u else None, self.cfg.max_packs_per_user)
        daily = _effective(u.daily_limit if u else None, self.cfg.daily_emoji_limit)
        since = ago(24)
        used = await self.db.usage_since(uid, since)
        next_free = None
        if daily is not None and used >= daily:
            times = await self.db.usage_times_since(uid, since)
            idx = min(len(times) - 1, used - daily)
            if times:
                next_free = datetime.fromisoformat(times[idx]) + timedelta(hours=24)
        return Quota(pack_limit, published, drafts, daily, used, next_free)

    async def quota_allows_at(self, uid: int, need: int) -> datetime | None:
        """When the rolling quota will have room for `need` more items (None if it already has)."""
        q = await self.quota(uid)
        if q.daily_limit is None or q.left >= need:
            return None
        times = await self.db.usage_times_since(uid, ago(24))
        k = max(1, q.used - q.daily_limit + need)  # this many of the oldest records must expire
        if not times:
            return None
        return datetime.fromisoformat(times[min(len(times), k) - 1]) + timedelta(hours=24)

    async def why_no_new_pack(self, uid: int, admin: bool) -> str | None:
        if admin:
            return None
        q = await self.quota(uid, admin)
        if q.pack_limit is not None and q.published >= q.pack_limit:
            return (f"😔 Непустых паков у вас уже {q.published} из {q.pack_limit}. "
                    "Добавляйте в существующие паки или удалите ненужный.")
        if q.drafts >= self.cfg.max_drafts:
            return (f"У вас уже {q.drafts} пустых пака. Добавьте что-нибудь в один из них "
                    "или удалите лишний — тогда можно будет создать новый.")
        return None

    async def why_no_publish(self, pack: Pack, actor_admin: bool) -> str | None:
        """An empty pack becomes a counted pack with its first emoji: check the owner's limit."""
        if actor_admin or pack.count > 0:
            return None
        if await self.is_admin(pack.owner_id):
            return None
        q = await self.quota(pack.owner_id, False)
        published = await self.db.count_published(pack.owner_id, exclude_pack=pack.id)
        if q.pack_limit is not None and published >= q.pack_limit:
            return (f"😔 Непустых паков уже {published} из {q.pack_limit}. Чтобы наполнить этот пак, "
                    "удалите один из заполненных — или добавляйте в существующие.")
        return None

    def when(self, dt: datetime | None) -> str:
        if dt is None:
            return "позже"
        local = dt.astimezone(self.tz) if self.tz else dt
        today = (datetime.now(self.tz) if self.tz else datetime.now(local.tzinfo)).date()
        prefix = "" if local.date() == today else "завтра " if local.date() == today + timedelta(days=1) else local.strftime("%d.%m ")
        return f"{prefix}в {local.strftime('%H:%M')}"

    def quota_exhausted_text(self, q: Quota) -> str:
        return (f"⏳ Дневной лимит исчерпан: {q.daily_limit} эмодзи и стикеров за 24 часа. "
                f"Следующее можно будет добавить {self.when(q.next_free)}.")

    def limits_text(self, q: Quota) -> str:
        packs = f"{q.published}" if q.pack_limit is None else f"{q.published}/{q.pack_limit}"
        line = f"📦 Непустых паков: <b>{packs}</b>"
        if q.drafts:
            line += f" (+{q.drafts} пустых)"
        if q.daily_limit is None:
            return line + "\n🎨 Эмодзи и стикеры: <b>без лимита</b>"
        line += f"\n🎨 Эмодзи и стикеров за 24 ч: осталось <b>{q.left}</b> из {q.daily_limit}"
        if q.left == 0:
            line += f" (снова можно {self.when(q.next_free)})"
        return line
