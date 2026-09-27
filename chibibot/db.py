"""SQLite storage: users, packs, emojis in packs and an audit log of every change."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from .kinds import EMOJI, Kind, kind_of

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS users(
    id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    is_admin INTEGER NOT NULL DEFAULT 0,
    settings TEXT,
    created_at TEXT NOT NULL,
    last_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS packs(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL REFERENCES users(id),
    name TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    tg_created INTEGER NOT NULL DEFAULT 0,
    settings TEXT,
    created_at TEXT NOT NULL,
    created_by INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS packs_owner ON packs(owner_id);
CREATE TABLE IF NOT EXISTS emojis(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pack_id INTEGER NOT NULL REFERENCES packs(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    label TEXT NOT NULL,
    source TEXT NOT NULL,
    skin_sha1 TEXT NOT NULL,
    slim INTEGER,
    settings TEXT NOT NULL,
    animated INTEGER NOT NULL,
    emoji TEXT NOT NULL,
    file_id TEXT,
    file_unique_id TEXT,
    custom_emoji_id TEXT,
    created_at TEXT NOT NULL,
    created_by INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS emojis_pack ON emojis(pack_id, position);
CREATE TABLE IF NOT EXISTS events(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pack_id INTEGER,
    pack_title TEXT,
    user_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    details TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_pack ON events(pack_id, id);
CREATE TABLE IF NOT EXISTS kv(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS usage(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    pack_id INTEGER,
    kind TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS usage_user ON usage(user_id, created_at);
"""

# columns added after the first release: (table, column, declaration)
MIGRATIONS = [
    ("users", "pack_limit", "INTEGER"),    # NULL = default from .env, 0 = unlimited
    ("users", "daily_limit", "INTEGER"),   # NULL = default from .env, 0 = unlimited
    ("packs", "kind", "TEXT NOT NULL DEFAULT 'emoji'"),   # emoji | stickers (see kinds.py)
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ago(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")


@dataclass
class User:
    id: int
    username: str | None
    first_name: str | None
    last_name: str | None
    is_admin: bool
    settings: dict | None
    created_at: str
    last_seen: str
    pack_limit: int | None = None     # personal override; 0 = unlimited
    daily_limit: int | None = None    # personal override; 0 = unlimited

    @property
    def display(self) -> str:
        if self.username:
            return "@" + self.username
        name = " ".join(x for x in (self.first_name, self.last_name) if x)
        return name or f"id{self.id}"


@dataclass
class Pack:
    id: int
    owner_id: int
    name: str
    title: str
    tg_created: bool
    settings: dict | None
    created_at: str
    created_by: int
    updated_at: str
    updated_by: int
    count: int = 0
    animated_count: int = 0
    kind: str = EMOJI

    @property
    def k(self) -> Kind:
        return kind_of(self.kind)

    @property
    def link(self) -> str:
        return self.k.link(self.name)


@dataclass
class Emoji:
    id: int
    pack_id: int
    position: int
    label: str
    source: str
    skin_sha1: str
    slim: bool | None
    settings: dict
    animated: bool
    emoji: str
    file_id: str | None
    file_unique_id: str | None
    custom_emoji_id: str | None
    created_at: str
    created_by: int
    updated_at: str
    updated_by: int


@dataclass
class Event:
    id: int
    pack_id: int | None
    pack_title: str | None
    user_id: int
    action: str
    details: str | None
    created_at: str


def _j(v: Any) -> str | None:
    return None if v is None else json.dumps(v, ensure_ascii=False)


def _uj(v: str | None) -> Any:
    return None if not v else json.loads(v)


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.conn: aiosqlite.Connection | None = None

    async def open(self) -> None:
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.executescript(SCHEMA)
        for table, col, decl in MIGRATIONS:
            async with self.conn.execute(f"PRAGMA table_info({table})") as cur:
                have = {r["name"] for r in await cur.fetchall()}
            if col not in have:
                await self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        await self.conn.commit()
        await self._migrate_default_smooth()

    async def _migrate_default_smooth(self) -> None:
        """Once: the default render mode became "hd". Saved "last used" settings of users and
        packs still carried the old default, so flip them too (items already made keep theirs)."""
        if await self.kv_get("migr_default_hd"):
            return
        for table in ("users", "packs"):
            async with self.c.execute(f"SELECT id, settings FROM {table} WHERE settings IS NOT NULL") as cur:
                rows = await cur.fetchall()
            for r in rows:
                d = _uj(r["settings"])
                if isinstance(d, dict) and d.get("mode") == "pixel":
                    d["mode"] = "hd"
                    await self.c.execute(f"UPDATE {table} SET settings=? WHERE id=?", (_j(d), r["id"]))
        await self.kv_set("migr_default_hd", "1")

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()

    @property
    def c(self) -> aiosqlite.Connection:
        assert self.conn is not None
        return self.conn

    # ---------------------------------------------------------------- users
    async def touch_user(self, uid: int, username: str | None, first: str | None, last: str | None,
                         admin: bool = False) -> User:
        ts = now()
        await self.c.execute(
            """INSERT INTO users(id, username, first_name, last_name, is_admin, created_at, last_seen)
               VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name,
               last_name=excluded.last_name, last_seen=excluded.last_seen,
               is_admin=MAX(users.is_admin, excluded.is_admin)""",
            (uid, username, first, last, int(admin), ts, ts),
        )
        await self.c.commit()
        u = await self.get_user(uid)
        assert u is not None
        return u

    @staticmethod
    def _user(r) -> User:
        return User(r["id"], r["username"], r["first_name"], r["last_name"], bool(r["is_admin"]),
                    _uj(r["settings"]), r["created_at"], r["last_seen"], r["pack_limit"], r["daily_limit"])

    async def get_user(self, uid: int) -> User | None:
        async with self.c.execute("SELECT * FROM users WHERE id=?", (uid,)) as cur:
            r = await cur.fetchone()
        return self._user(r) if r else None

    async def set_limits(self, uid: int, pack_limit: int | None, daily_limit: int | None) -> None:
        await self.c.execute(
            "INSERT INTO users(id, created_at, last_seen, pack_limit, daily_limit) VALUES(?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET pack_limit=excluded.pack_limit, daily_limit=excluded.daily_limit",
            (uid, now(), now(), pack_limit, daily_limit),
        )
        await self.c.commit()

    async def set_admin(self, uid: int, flag: bool) -> None:
        await self.c.execute(
            "INSERT INTO users(id, is_admin, created_at, last_seen) VALUES(?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET is_admin=excluded.is_admin",
            (uid, int(flag), now(), now()),
        )
        await self.c.commit()

    async def admin_ids(self) -> list[int]:
        async with self.c.execute("SELECT id FROM users WHERE is_admin=1") as cur:
            return [r[0] for r in await cur.fetchall()]

    async def save_user_settings(self, uid: int, settings: dict) -> None:
        await self.c.execute("UPDATE users SET settings=? WHERE id=?", (_j(settings), uid))
        await self.c.commit()

    async def list_users(self, limit: int = 50, offset: int = 0) -> list[tuple[User, int]]:
        async with self.c.execute(
            """SELECT u.*, (SELECT COUNT(*) FROM packs p WHERE p.owner_id=u.id) AS packs
               FROM users u ORDER BY u.last_seen DESC LIMIT ? OFFSET ?""", (limit, offset)) as cur:
            rows = await cur.fetchall()
        return [(self._user(r), r["packs"]) for r in rows]

    async def count_users(self) -> int:
        async with self.c.execute("SELECT COUNT(*) FROM users") as cur:
            return (await cur.fetchone())[0]

    # ---------------------------------------------------------------- packs
    _PACK_SELECT = """SELECT p.*,
        (SELECT COUNT(*) FROM emojis e WHERE e.pack_id=p.id) AS cnt,
        (SELECT COUNT(*) FROM emojis e WHERE e.pack_id=p.id AND e.animated=1) AS acnt
        FROM packs p"""

    @staticmethod
    def _pack(r) -> Pack:
        return Pack(r["id"], r["owner_id"], r["name"], r["title"], bool(r["tg_created"]), _uj(r["settings"]),
                    r["created_at"], r["created_by"], r["updated_at"], r["updated_by"], r["cnt"], r["acnt"],
                    r["kind"] or EMOJI)

    async def create_pack(self, owner_id: int, name: str, title: str, by: int, settings: dict | None,
                          kind: str = EMOJI) -> Pack:
        ts = now()
        cur = await self.c.execute(
            "INSERT INTO packs(owner_id, name, title, settings, created_at, created_by, updated_at, updated_by, kind)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (owner_id, name, title, _j(settings), ts, by, ts, by, kind),
        )
        await self.c.commit()
        pack = await self.get_pack(cur.lastrowid)
        assert pack is not None
        return pack

    async def get_pack(self, pack_id: int) -> Pack | None:
        async with self.c.execute(self._PACK_SELECT + " WHERE p.id=?", (pack_id,)) as cur:
            r = await cur.fetchone()
        return self._pack(r) if r else None

    async def user_packs(self, owner_id: int) -> list[Pack]:
        async with self.c.execute(self._PACK_SELECT + " WHERE p.owner_id=? ORDER BY p.id", (owner_id,)) as cur:
            return [self._pack(r) for r in await cur.fetchall()]

    async def count_user_packs(self, owner_id: int) -> int:
        async with self.c.execute("SELECT COUNT(*) FROM packs WHERE owner_id=?", (owner_id,)) as cur:
            return (await cur.fetchone())[0]

    async def count_published(self, owner_id: int, exclude_pack: int | None = None) -> int:
        """Packs that hold at least one emoji (empty drafts do not count towards the limit)."""
        async with self.c.execute(
            "SELECT COUNT(*) FROM packs p WHERE p.owner_id=? AND p.id<>? "
            "AND EXISTS(SELECT 1 FROM emojis e WHERE e.pack_id=p.id)", (owner_id, exclude_pack or 0)) as cur:
            return (await cur.fetchone())[0]

    async def count_drafts(self, owner_id: int) -> int:
        async with self.c.execute(
            "SELECT COUNT(*) FROM packs p WHERE p.owner_id=? "
            "AND NOT EXISTS(SELECT 1 FROM emojis e WHERE e.pack_id=p.id)", (owner_id,)) as cur:
            return (await cur.fetchone())[0]

    async def all_packs(self, limit: int, offset: int) -> list[Pack]:
        async with self.c.execute(self._PACK_SELECT + " ORDER BY p.updated_at DESC, p.id DESC LIMIT ? OFFSET ?",
                                  (limit, offset)) as cur:
            return [self._pack(r) for r in await cur.fetchall()]

    async def count_packs(self) -> int:
        async with self.c.execute("SELECT COUNT(*) FROM packs") as cur:
            return (await cur.fetchone())[0]

    async def touch_pack(self, pack_id: int, by: int, **fields) -> None:
        sets = ["updated_at=?", "updated_by=?"]
        vals: list[Any] = [now(), by]
        for k, v in fields.items():
            sets.append(f"{k}=?")
            vals.append(_j(v) if k == "settings" else v)
        vals.append(pack_id)
        await self.c.execute(f"UPDATE packs SET {', '.join(sets)} WHERE id=?", vals)
        await self.c.commit()

    async def delete_pack(self, pack_id: int) -> None:
        await self.c.execute("DELETE FROM emojis WHERE pack_id=?", (pack_id,))
        await self.c.execute("DELETE FROM packs WHERE id=?", (pack_id,))
        await self.c.commit()

    # ---------------------------------------------------------------- emojis
    @staticmethod
    def _emoji(r) -> Emoji:
        return Emoji(r["id"], r["pack_id"], r["position"], r["label"], r["source"], r["skin_sha1"],
                     None if r["slim"] is None else bool(r["slim"]), _uj(r["settings"]) or {}, bool(r["animated"]),
                     r["emoji"], r["file_id"], r["file_unique_id"], r["custom_emoji_id"], r["created_at"],
                     r["created_by"], r["updated_at"], r["updated_by"])

    async def add_emoji(self, pack_id: int, label: str, source: str, skin_sha1: str, slim: bool | None,
                        settings: dict, animated: bool, emoji: str, file_id: str | None, file_unique_id: str | None,
                        custom_emoji_id: str | None, by: int) -> Emoji:
        ts = now()
        async with self.c.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM emojis WHERE pack_id=?", (pack_id,)) as cur:
            pos = (await cur.fetchone())[0]
        cur = await self.c.execute(
            """INSERT INTO emojis(pack_id, position, label, source, skin_sha1, slim, settings, animated, emoji,
               file_id, file_unique_id, custom_emoji_id, created_at, created_by, updated_at, updated_by)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (pack_id, pos, label, source, skin_sha1, None if slim is None else int(slim), _j(settings), int(animated),
             emoji, file_id, file_unique_id, custom_emoji_id, ts, by, ts, by),
        )
        await self.c.commit()
        e = await self.get_emoji(cur.lastrowid)
        assert e is not None
        return e

    async def get_emoji(self, emoji_id: int) -> Emoji | None:
        async with self.c.execute("SELECT * FROM emojis WHERE id=?", (emoji_id,)) as cur:
            r = await cur.fetchone()
        return self._emoji(r) if r else None

    async def pack_emojis(self, pack_id: int) -> list[Emoji]:
        async with self.c.execute("SELECT * FROM emojis WHERE pack_id=? ORDER BY position, id", (pack_id,)) as cur:
            return [self._emoji(r) for r in await cur.fetchall()]

    async def update_emoji(self, emoji_id: int, by: int, **fields) -> None:
        sets = ["updated_at=?", "updated_by=?"]
        vals: list[Any] = [now(), by]
        for k, v in fields.items():
            sets.append(f"{k}=?")
            if k == "settings":
                v = _j(v)
            elif isinstance(v, bool):
                v = int(v)
            vals.append(v)
        vals.append(emoji_id)
        await self.c.execute(f"UPDATE emojis SET {', '.join(sets)} WHERE id=?", vals)
        await self.c.commit()

    async def set_positions(self, order: list[tuple[int, int, str | None]]) -> None:
        """[(emoji_id, position, fresh file_id)]"""
        await self.c.executemany("UPDATE emojis SET position=?, file_id=COALESCE(?, file_id) WHERE id=?",
                                 [(pos, fid, eid) for eid, pos, fid in order])
        await self.c.commit()

    async def delete_emoji(self, emoji_id: int) -> None:
        await self.c.execute("DELETE FROM emojis WHERE id=?", (emoji_id,))
        await self.c.commit()

    async def delete_emojis(self, ids: list[int]) -> None:
        await self.c.executemany("DELETE FROM emojis WHERE id=?", [(i,) for i in ids])
        await self.c.commit()

    async def stats(self) -> dict[str, int]:
        out = {}
        for key, sql in (("users", "SELECT COUNT(*) FROM users"), ("packs", "SELECT COUNT(*) FROM packs"),
                         ("emojis", "SELECT COUNT(*) FROM emojis"),
                         ("animated", "SELECT COUNT(*) FROM emojis WHERE animated=1")):
            async with self.c.execute(sql) as cur:
                out[key] = (await cur.fetchone())[0]
        return out

    # ---------------------------------------------------------------- daily quota
    async def add_usage(self, uid: int, pack_id: int | None, kind: str) -> None:
        await self.c.execute("INSERT INTO usage(user_id, pack_id, kind, created_at) VALUES(?,?,?,?)",
                             (uid, pack_id, kind, now()))
        await self.c.commit()

    async def usage_since(self, uid: int, since: str) -> int:
        async with self.c.execute("SELECT COUNT(*) FROM usage WHERE user_id=? AND created_at>?", (uid, since)) as cur:
            return (await cur.fetchone())[0]

    async def usage_times_since(self, uid: int, since: str) -> list[str]:
        async with self.c.execute("SELECT created_at FROM usage WHERE user_id=? AND created_at>? ORDER BY created_at",
                                  (uid, since)) as cur:
            return [r[0] for r in await cur.fetchall()]

    # ---------------------------------------------------------------- audit log
    async def log(self, user_id: int, action: str, pack: Pack | None = None, details: str | None = None,
                  pack_id: int | None = None, pack_title: str | None = None) -> None:
        await self.c.execute(
            "INSERT INTO events(pack_id, pack_title, user_id, action, details, created_at) VALUES(?,?,?,?,?,?)",
            (pack.id if pack else pack_id, pack.title if pack else pack_title, user_id, action, details, now()),
        )
        await self.c.commit()

    async def events(self, pack_id: int | None = None, limit: int = 20, offset: int = 0) -> list[Event]:
        if pack_id is None:
            sql, args = "SELECT * FROM events ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
        else:
            sql, args = "SELECT * FROM events WHERE pack_id=? ORDER BY id DESC LIMIT ? OFFSET ?", (pack_id, limit, offset)
        async with self.c.execute(sql, args) as cur:
            return [Event(r["id"], r["pack_id"], r["pack_title"], r["user_id"], r["action"], r["details"],
                          r["created_at"]) for r in await cur.fetchall()]

    async def count_events(self, pack_id: int | None = None) -> int:
        if pack_id is None:
            sql, args = "SELECT COUNT(*) FROM events", ()
        else:
            sql, args = "SELECT COUNT(*) FROM events WHERE pack_id=?", (pack_id,)
        async with self.c.execute(sql, args) as cur:
            return (await cur.fetchone())[0]

    # ---------------------------------------------------------------- kv cache
    async def kv_get(self, key: str) -> str | None:
        async with self.c.execute("SELECT value FROM kv WHERE key=?", (key,)) as cur:
            r = await cur.fetchone()
        return r[0] if r else None

    async def kv_set(self, key: str, value: str) -> None:
        await self.c.execute("INSERT INTO kv(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                             (key, value))
        await self.c.commit()
