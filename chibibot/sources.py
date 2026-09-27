"""Where skins come from: Minecraft usernames/UUIDs, PNG files and archives."""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import re
import tarfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import aiohttp

from .render.skin import Skin, SkinError, load_skin

log = logging.getLogger(__name__)

NICK_RE = re.compile(r"^[A-Za-z0-9_]{1,16}$")
UUID_RE = re.compile(r"^[0-9a-fA-F]{32}$|^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
SPLIT_RE = re.compile(r"[,;\s]+")

MAX_ARCHIVE_FILES = 300
MAX_ARCHIVE_TOTAL = 100 * 1024 * 1024
MAX_MEMBER = 4 * 1024 * 1024


@dataclass
class SkinItem:
    label: str
    source: str          # 'nick' | 'file'
    sha1: str
    slim: bool | None    # model reported by Mojang; None = detect from pixels


class SkinStore:
    """Normalised skins on disk, addressed by the SHA-1 of their PNG."""

    def __init__(self, root: Path):
        self.root = root

    def path(self, sha1: str) -> Path:
        return self.root / f"{sha1}.png"

    def save(self, skin: Skin) -> str:
        p = self.path(skin.sha1)
        if not p.exists():
            p.write_bytes(skin.png)
        return skin.sha1

    def load_bytes(self, sha1: str) -> bytes:
        return self.path(sha1).read_bytes()


def parse_names(text: str) -> tuple[list[str], list[str]]:
    """Split user text into valid names/UUIDs and rejected tokens (deduplicated, order kept)."""
    ok, bad, seen = [], [], set()
    for tok in SPLIT_RE.split(text or ""):
        tok = tok.strip().strip("\"'`«»")
        if not tok:
            continue
        key = tok.lower().replace("-", "")
        if key in seen:
            continue
        seen.add(key)
        if NICK_RE.match(tok) or UUID_RE.match(tok):
            ok.append(tok)
        else:
            bad.append(tok)
    return ok, bad


class NotFound(Exception):
    pass


class MojangClient:
    """Username -> skin PNG, with a short cache and a couple of public fallbacks."""

    def __init__(self):
        self._session: aiohttp.ClientSession | None = None
        self._cache: dict[str, tuple[float, str, bytes, bool]] = {}
        self._sem = asyncio.Semaphore(4)

    async def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            # short per-step timeouts: a stuck connection should fall back to the mirror fast
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=12, sock_connect=5, sock_read=8),
                headers={"User-Agent": "ChibiEmojiBot/1.0 (+telegram)"},
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _json(self, url: str):
        s = await self.session()
        async with s.get(url) as r:
            if r.status in (204, 404):
                raise NotFound(url)
            r.raise_for_status()
            return json.loads(await r.text())

    async def _bytes(self, url: str) -> bytes:
        s = await self.session()
        async with s.get(url) as r:
            if r.status == 404:
                raise NotFound(url)
            r.raise_for_status()
            return await r.read()

    async def _mojang(self, name: str) -> tuple[str, bytes, bool]:
        if UUID_RE.match(name):
            uid = name.replace("-", "")
        else:
            uid = (await self._json(f"https://api.mojang.com/users/profiles/minecraft/{name}"))["id"]
        prof = await self._json(f"https://sessionserver.mojang.com/session/minecraft/profile/{uid}")
        tex = {}
        for prop in prof.get("properties", []):
            if prop.get("name") == "textures":
                tex = json.loads(base64.b64decode(prop["value"]))
        skin = tex.get("textures", {}).get("SKIN")
        if not skin:
            raise NotFound(name)
        slim = (skin.get("metadata") or {}).get("model") == "slim"
        url = skin["url"].replace("http://", "https://")
        return prof.get("name", name), await self._bytes(url), slim

    async def _mcheads(self, name: str) -> tuple[str, bytes, bool | None]:
        return name, await self._bytes(f"https://mc-heads.net/skin/{name}"), None

    async def fetch(self, name: str) -> tuple[str, bytes, bool | None]:
        key = name.lower().replace("-", "")
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < 600:
            return hit[1], hit[2], hit[3]
        async with self._sem:
            try:
                real, png, slim = await self._mojang_retry(name)
            except NotFound:
                raise  # Mojang says there is no such player; mirrors would hand out a default skin
            except Exception as exc:  # noqa: BLE001 - Mojang unreachable: try a mirror
                log.warning("Mojang lookup failed for %s: %r; trying mc-heads", name, exc)
                try:
                    real, png, slim = await self._mcheads(name)
                except Exception as exc2:  # noqa: BLE001
                    raise NotFound(name) from exc2
            self._cache[key] = (time.time(), real, png, slim)
            return real, png, slim

    async def _mojang_retry(self, name: str) -> tuple[str, bytes, bool]:
        for attempt in range(3):
            try:
                return await self._mojang(name)
            except aiohttp.ClientResponseError as exc:
                if exc.status == 429 and attempt < 2:  # rate limited: back off and retry
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                raise
            except (aiohttp.ClientConnectionError, asyncio.TimeoutError):
                if attempt < 1:  # one quick retry, then the caller falls back to the mirror
                    await asyncio.sleep(1)
                    continue
                raise
        raise NotFound(name)


def _archive_members(data: bytes, filename: str) -> list[tuple[str, bytes]]:
    """(member name, bytes) for PNG and TXT files inside a zip or tar archive."""
    out: list[tuple[str, bytes]] = []
    total = 0

    def want(name: str) -> bool:
        base = PurePosixPath(name).name
        return (not base.startswith(".") and "__MACOSX" not in name
                and base.lower().endswith((".png", ".txt")))

    if zipfile.is_zipfile(io.BytesIO(data)):
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for info in zf.infolist():
                if info.is_dir() or not want(info.filename) or info.file_size > MAX_MEMBER:
                    continue
                total += info.file_size
                if total > MAX_ARCHIVE_TOTAL or len(out) >= MAX_ARCHIVE_FILES:
                    break
                out.append((info.filename, zf.read(info)))
        return out
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tf:
            for m in tf.getmembers():
                if not m.isfile() or not want(m.name) or m.size > MAX_MEMBER:
                    continue
                total += m.size
                if total > MAX_ARCHIVE_TOTAL or len(out) >= MAX_ARCHIVE_FILES:
                    break
                fh = tf.extractfile(m)
                if fh:
                    out.append((m.name, fh.read()))
        return out
    except tarfile.TarError as exc:
        raise SkinError(f"не удалось открыть архив {filename}") from exc


def is_archive_name(filename: str) -> bool:
    return filename.lower().endswith((".zip", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz"))


def label_from_filename(name: str) -> str:
    stem = PurePosixPath(name).name
    for ext in (".png", ".PNG"):
        if stem.endswith(ext):
            stem = stem[: -len(ext)]
    return stem[:40] or "skin"


@dataclass
class Collected:
    items: list[SkinItem]
    errors: list[str]
    names: list[str]      # usernames still to fetch (found inside .txt files)


def collect_file(store: SkinStore, filename: str, data: bytes) -> Collected:
    """A PNG or an archive -> stored skins (+ usernames listed in .txt files)."""
    items: list[SkinItem] = []
    errors: list[str] = []
    names: list[str] = []
    if is_archive_name(filename) or zipfile.is_zipfile(io.BytesIO(data)):
        try:
            members = _archive_members(data, filename)
        except SkinError as exc:
            return Collected([], [str(exc)], [])
        if not members:
            return Collected([], [f"{filename}: в архиве нет PNG-скинов"], [])
        for name, blob in members:
            if name.lower().endswith(".txt"):
                ok, _ = parse_names(blob.decode("utf-8", "ignore"))
                names.extend(ok)
                continue
            try:
                skin = load_skin(blob)
            except SkinError as exc:
                errors.append(f"{PurePosixPath(name).name}: {exc}")
                continue
            items.append(SkinItem(label_from_filename(name), "file", store.save(skin), None))
        return Collected(items, errors, names)
    try:
        skin = load_skin(data)
    except SkinError as exc:
        return Collected([], [f"{filename}: {exc}"], [])
    return Collected([SkinItem(label_from_filename(filename), "file", store.save(skin), None)], [], [])


async def collect_names(client: MojangClient, store: SkinStore, names: list[str]) -> Collected:
    items: list[SkinItem] = []
    errors: list[str] = []

    async def one(name: str):
        try:
            real, png, slim = await client.fetch(name)
            skin = load_skin(png, slim)
            return SkinItem(real, "nick", store.save(skin), slim), None
        except NotFound:
            return None, f"{name}: игрок не найден"
        except SkinError as exc:
            return None, f"{name}: {exc}"
        except Exception as exc:  # noqa: BLE001
            log.exception("fetch %s", name)
            return None, f"{name}: ошибка загрузки ({exc.__class__.__name__})"

    for item, err in await asyncio.gather(*(one(n) for n in names)):
        if item:
            items.append(item)
        if err:
            errors.append(err)
    return Collected(items, errors, [])
