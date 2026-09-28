"""End-to-end smoke test without Telegram: real handlers, real rendering, fake Bot API.

The fake session keeps messages and custom emoji sets in memory, so the whole flow —
create a pack, send nicks/PNG/ZIP, walk the settings, create, replace, reorder, delete,
admin views — runs exactly as it would against Telegram.

    python tests/smoke_test.py            (needs internet for the Mojang lookups)
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import re
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aiogram import Bot  # noqa: E402
from aiogram.client.session.base import BaseSession  # noqa: E402
from aiogram.types import Update  # noqa: E402

from chibibot.main import build_dispatcher  # noqa: E402
from chibibot.config import Config  # noqa: E402
from chibibot.db import Database  # noqa: E402
from chibibot.jobs import Worker  # noqa: E402
from chibibot.render.skin import default_skin  # noqa: E402
from chibibot.sources import MojangClient, SkinStore  # noqa: E402
from chibibot.stickers import StickerService  # noqa: E402
from chibibot.tg.app import App  # noqa: E402

BOT_USER = {"id": 999, "is_bot": True, "first_name": "Chibi", "username": "chibi_test_bot"}
ADMIN_ID, USER_ID = 1001, 2002


class FakeTelegram(BaseSession):
    def __init__(self):
        super().__init__()
        self.mid = 1000
        self.messages: dict[int, dict] = {}
        self.media: dict[int, bytes] = {}
        self.sets: dict[str, dict] = {}
        self.uploads: dict[str, tuple[str, bytes]] = {}
        self.files: dict[str, bytes] = {}
        self.alerts: list[str] = []
        self.calls: list[str] = []
        self.checked: list[tuple] = []
        self.n = 0

    async def close(self):
        pass

    async def stream_content(self, url, headers=None, timeout=30, chunk_size=65536, raise_for_status=True):
        yield self.files[url.rsplit("/", 1)[-1]]

    def _id(self, p: str) -> str:
        self.n += 1
        return f"{p}{self.n}"

    def _msg(self, chat_id, **fields) -> dict:
        self.mid += 1
        raw = {"message_id": self.mid, "date": int(time.time()), "chat": {"id": chat_id, "type": "private"},
               "from": BOT_USER}
        raw.update({k: v for k, v in fields.items() if v is not None})
        self.messages[self.mid] = raw
        return raw

    @staticmethod
    def _markup(m):
        return m.model_dump(exclude_none=True) if m is not None else None

    def _media_fields(self, kind: str, media, mid: int | None = None) -> dict:
        data = getattr(media, "data", None)
        fid = media if isinstance(media, str) else self._id("f")
        if data is not None and mid is not None:
            self.media[mid] = data
        if kind == "photo":
            return {"photo": [{"file_id": fid, "file_unique_id": "u" + fid, "width": 100, "height": 100}]}
        return {"animation": {"file_id": fid, "file_unique_id": "u" + fid, "width": 100, "height": 100,
                              "duration": 1}}

    def _err(self, bot, method, text):
        return self.check_response(bot, method, 400, json.dumps({"ok": False, "error_code": 400,
                                                                  "description": f"Bad Request: {text}"}))

    def _sticker_json(self, name, st, stype="custom_emoji"):
        side = 100 if stype == "custom_emoji" else 512
        out = {"file_id": st["file_id"], "file_unique_id": st["uid"], "type": stype, "width": side,
               "height": side, "is_animated": False, "is_video": st["fmt"] == "video",
               "emoji": st["emoji"], "set_name": name}
        if stype == "custom_emoji":
            out["custom_emoji_id"] = st["ceid"]
        return out

    def _too_big(self, stype, inp) -> bool:
        """Like the real Telegram: custom emoji videos above ~64 KB are refused."""
        fmt, data = self.uploads[inp.sticker]
        return fmt == "video" and stype == "custom_emoji" and len(data) > 64 * 1024

    def _check_media(self, stype, inp):
        """Telegram's size rules: custom emoji 100x100, stickers with one side of exactly 512."""
        fmt, data = self.uploads[inp.sticker]
        if fmt == "static":
            from PIL import Image
            w, h = Image.open(io.BytesIO(data)).size
        else:
            import subprocess
            with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as fh:
                fh.write(data)
            probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "json",
                                    fh.name], capture_output=True, text=True)
            os.remove(fh.name)
            st = json.loads(probe.stdout)["streams"][0]
            w, h = st["width"], st["height"]
        if stype == "custom_emoji":
            assert (w, h) == (100, 100), f"custom emoji must be 100x100, got {w}x{h}"
        else:
            assert max(w, h) == 512 and min(w, h) <= 512, f"sticker needs a 512 px side, got {w}x{h}"
        self.checked.append((stype, fmt, w, h))

    def _new_sticker(self, inp):
        fmt, data = self.uploads[inp.sticker]
        return {"file_id": self._id("st"), "uid": self._id("su"), "ceid": self._id("ce"), "fmt": fmt,
                "emoji": inp.emoji_list[0], "data": data}

    async def make_request(self, bot, method, timeout=None):
        name = type(method).__name__
        self.calls.append(name)
        m = method
        r: object = True
        if name == "GetMe":
            r = BOT_USER
        elif name in ("SetMyCommands", "SendChatAction", "DeleteMessage"):
            r = True
        elif name == "AnswerCallbackQuery":
            if m.text:
                self.alerts.append(m.text)
        elif name == "SendMessage":
            r = self._msg(m.chat_id, text=m.text, reply_markup=self._markup(m.reply_markup))
        elif name in ("SendPhoto", "SendAnimation"):
            kind = "photo" if name == "SendPhoto" else "animation"
            media = m.photo if kind == "photo" else m.animation
            r = self._msg(m.chat_id, caption=m.caption, reply_markup=self._markup(m.reply_markup))
            r.update(self._media_fields(kind, media, r["message_id"]))
        elif name == "EditMessageText":
            r = self.messages[m.message_id]
            r["text"] = m.text
            r["reply_markup"] = self._markup(m.reply_markup)
        elif name == "EditMessageCaption":
            r = self.messages[m.message_id]
            r["caption"] = m.caption
            r["reply_markup"] = self._markup(m.reply_markup)
        elif name == "EditMessageReplyMarkup":
            r = self.messages[m.message_id]
            r["reply_markup"] = self._markup(m.reply_markup)
        elif name == "EditMessageMedia":
            r = self.messages[m.message_id]
            kind = "photo" if m.media.type == "photo" else "animation"
            r.pop("photo", None)
            r.pop("animation", None)
            r.update(self._media_fields(kind, m.media.media, m.message_id))
            r["caption"] = m.media.caption
            r["reply_markup"] = self._markup(m.reply_markup)
        elif name == "GetFile":
            r = {"file_id": m.file_id, "file_unique_id": "u" + m.file_id, "file_path": m.file_id}
        elif name == "UploadStickerFile":
            fid = self._id("up")
            self.uploads[fid] = (m.sticker_format, m.sticker.data)
            fmt = m.sticker_format
            data = m.sticker.data
            assert (fmt == "static" and data[:8] == b"\x89PNG\r\n\x1a\n") or (fmt == "video" and data[:4] == b"\x1a\x45\xdf\xa3"), fmt
            assert len(data) <= 256 * 1024
            r = {"file_id": fid, "file_unique_id": "u" + fid}
        elif name == "CreateNewStickerSet":
            if m.name in self.sets:
                return self._err(bot, m, "sticker set name is already occupied")
            assert m.name.endswith("_by_" + BOT_USER["username"])
            assert m.sticker_type in ("custom_emoji", "regular"), m.sticker_type
            assert len(m.title) <= 64
            for inp in m.stickers:
                self._check_media(m.sticker_type, inp)
                if self._too_big(m.sticker_type, inp):
                    return self._err(bot, m, "STICKER_VIDEO_BIG")
            self.sets[m.name] = {"title": m.title, "owner": m.user_id, "type": m.sticker_type,
                                 "stickers": [self._new_sticker(s) for s in m.stickers]}
        elif name == "AddStickerToSet":
            s = self.sets.get(m.name)
            if not s:
                return self._err(bot, m, "STICKERSET_INVALID")
            assert s["owner"] == m.user_id, "must add with the owner's id"
            if len(s["stickers"]) >= (200 if s["type"] == "custom_emoji" else 120):
                return self._err(bot, m, "STICKERS_TOO_MUCH")
            if self._too_big(s["type"], m.sticker):
                return self._err(bot, m, "STICKER_VIDEO_BIG")
            self._check_media(s["type"], m.sticker)
            s["stickers"].append(self._new_sticker(m.sticker))
        elif name == "GetStickerSet":
            s = self.sets.get(m.name)
            if not s:
                return self._err(bot, m, "STICKERSET_INVALID")
            r = {"name": m.name, "title": s["title"], "sticker_type": s["type"],
                 "stickers": [self._sticker_json(m.name, st, s["type"]) for st in s["stickers"]]}
        elif name in ("DeleteStickerFromSet", "SetStickerPositionInSet"):
            for sname, s in self.sets.items():
                for i, st in enumerate(s["stickers"]):
                    if st["file_id"] == m.sticker:
                        s["stickers"].pop(i)
                        if name == "SetStickerPositionInSet":
                            s["stickers"].insert(m.position, st)
                        break
        elif name == "ReplaceStickerInSet":
            s = self.sets[m.name]
            self._check_media(s["type"], m.sticker)
            idx = next(i for i, st in enumerate(s["stickers"]) if st["file_id"] == m.old_sticker)
            s["stickers"][idx] = self._new_sticker(m.sticker)
        elif name == "SetStickerSetTitle":
            self.sets[m.name]["title"] = m.title
        elif name == "DeleteStickerSet":
            self.sets.pop(m.name, None)
        else:
            raise AssertionError(f"unexpected API call {name}")
        return self.check_response(bot, m, 200, json.dumps({"ok": True, "result": r})).result


class Chat:
    """One Telegram user talking to the bot."""

    def __init__(self, bot, dp, fake: FakeTelegram, uid: int, name: str):
        self.bot, self.dp, self.fake, self.uid, self.name = bot, dp, fake, uid, name
        self.upd = uid * 100000

    def _user(self):
        return {"id": self.uid, "is_bot": False, "first_name": self.name, "username": self.name.lower()}

    async def _feed(self, raw):
        self.upd += 1
        raw["update_id"] = self.upd
        await self.dp.feed_update(self.bot, Update.model_validate(raw, context={"bot": self.bot}))

    def _user_msg(self, **fields):
        self.fake.mid += 1
        return {"message_id": self.fake.mid, "date": int(time.time()),
                "chat": {"id": self.uid, "type": "private"}, "from": self._user(), **fields}

    async def text(self, text: str):
        f = {"text": text}
        if text.startswith("/"):
            f["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
        await self._feed({"message": self._user_msg(**f)})

    async def doc(self, filename: str, data: bytes):
        fid = self.fake._id("doc")
        self.fake.files[fid] = data
        await self._feed({"message": self._user_msg(document={"file_id": fid, "file_unique_id": "u" + fid,
                                                              "file_name": filename, "file_size": len(data)})})

    def mine(self):
        return [m for m in self.fake.messages.values() if m["chat"]["id"] == self.uid]

    def last(self):
        return self.mine()[-1]

    def find_button(self, sub: str):
        for m in reversed(self.mine()):
            for row in (m.get("reply_markup") or {}).get("inline_keyboard", []):
                for b in row:
                    if sub in b["text"] and b.get("callback_data"):
                        return m, b
        raise AssertionError(f"no button containing {sub!r}; last message: {self.show(self.last())}")

    async def click(self, sub: str):
        m, b = self.find_button(sub)
        await self._feed({"callback_query": {"id": str(self.upd), "from": self._user(), "chat_instance": "ci",
                                             "message": m, "data": b["callback_data"]}})

    @staticmethod
    def show(m) -> str:
        body = m.get("text") or m.get("caption") or ""
        kind = "photo" if "photo" in m else "anim" if "animation" in m else "text"
        btns = [b["text"] for row in (m.get("reply_markup") or {}).get("inline_keyboard", []) for b in row]
        return f"[{m['message_id']} {kind}] {body}\n   buttons: {btns}"


def skin_zip(skins: dict[str, bytes], txt: str | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n, b in skins.items():
            zf.writestr(f"skins/{n}", b)
        if txt:
            zf.writestr("nicks.txt", txt)
    return buf.getvalue()


CHATS: list = []


async def pick_anims(chat: "Chat", wanted: set[str]) -> None:
    """Tick exactly `wanted` (Russian labels) on the multi-select animation step."""
    for turn_on in (True, False):  # switch on first so the last ticked one is never removed
        for _ in range(40):
            rows = chat.last()["reply_markup"]["inline_keyboard"]
            toggles = [b for row in rows for b in row if re.match(r"^(✅|▫️) \d+\. ", b["text"])]
            todo = next((b for b in toggles
                         if (b["text"].split(". ", 1)[1] in wanted) == turn_on
                         and b["text"].startswith("✅") != turn_on), None)
            if todo is None:
                break
            await chat.click(todo["text"])


def check(cond, what):
    print(("  ok  " if cond else "  FAIL") + " " + what)
    if not cond:
        for c in CHATS:
            print(f"--- last messages of {c.name}:")
            for m in c.mine()[-3:]:
                print(c.show(m))
        sys.stdout.flush()
        os._exit(1)  # the render pool would otherwise keep the interpreter alive


async def main():
    tmp = Path(tempfile.mkdtemp(prefix="chibi_smoke_"))
    out = tmp / "out"
    out.mkdir()
    # small limits so the test can hit them: 2 packs with emoji, 2 empty drafts, 12 emoji per 24 h
    cfg = Config(token="42:TEST", admin_ids={ADMIN_ID}, max_packs_per_user=2, daily_emoji_limit=12, max_drafts=2,
                 max_batch_user=50, max_batch_admin=200, data_dir=tmp, timezone="Europe/Moscow", render_workers=2)
    for d in (cfg.skins_dir, cfg.thumbs_dir):
        d.mkdir(parents=True, exist_ok=True)
    fake = FakeTelegram()
    bot = Bot(cfg.token, session=fake)
    db = Database(cfg.db_path)
    await db.open()
    worker = Worker(2)
    mojang = MojangClient()
    app = App(cfg, db, worker, StickerService(bot, db), mojang, SkinStore(cfg.skins_dir))
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode
    bot.default = DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True)
    dp = build_dispatcher(app)
    user = Chat(bot, dp, fake, USER_ID, "Player")
    adm = Chat(bot, dp, fake, ADMIN_ID, "Boss")
    CHATS.extend([user, adm])
    t0 = time.time()

    print("== user: menu and new pack")
    await user.text("/start")
    txt = user.last()["text"]
    check("Chibi Emoji" in txt and "0/2" in txt and "12</b> из 12" in txt, "menu shows packs 0/2 and 12 of 12 emoji")
    await user.click("Новый пак")
    check("Какой пак создаём" in user.last()["text"], "asks emoji or sticker pack")
    await user.click("😀 Эмодзи-пак")
    await user.text("Мой первый пак")
    check("Добавление в «Мой первый пак»" in user.last()["text"], "collect screen opened")

    print("== user: sending nicks, PNG and ZIP")
    await user.text("Notch, jeb_; Dinnerbone\nzq9xk2pw7vbn3q")
    txt = user.last()["text"]
    check("3 скина" in txt and "не найден" in txt, "3 nicks found, 1 reported missing")
    await user.text("not a nick!!")
    await user.doc("my_skin.png", default_skin().png)
    grian = (ROOT / "tests" / "skins" / "Grian.png")
    extra = {"slim_skin.png": default_skin(slim=True).png, "broken.png": b"not png"}
    if grian.exists():
        extra["Grian.png"] = grian.read_bytes()
    await user.doc("pack.zip", skin_zip(extra, "Dream\n"))
    txt = user.last()["text"]
    print("   ", txt.splitlines()[-4:])
    check("В очереди" in txt and "broken.png" in txt, "zip parsed, broken file reported")
    n_items = int(txt.split("В очереди: <b>")[1].split()[0])
    check(n_items >= 6, f"queue has {n_items} skins")

    print("== user: settings wizard")
    await user.click("Далее: настройка")
    check("photo" in user.last() and "Стиль" in user.last()["caption"], "style step with example sheet")
    (out / "step_style.png").write_bytes(fake.media[user.last()["message_id"]])
    await user.click("2. Minecraft Live")
    check("Тип тела" in user.last()["caption"], "body step")
    await user.click("1. Авто")
    check("Итог" in user.last()["caption"], "hub after body")
    await user.click("Настроить дальше")
    check("Поза" in user.last()["caption"], "pose step")
    (out / "step_pose.png").write_bytes(fake.media[user.last()["message_id"]])
    await user.click("2. Машет")
    check("Кадр" in user.last()["caption"], "bust step")
    await user.click("1. Во весь рост")
    check("Анимации" in user.last()["caption"] and "animation" in user.last(), "animation step is animated")
    (out / "step_anim.mp4").write_bytes(fake.media[user.last()["message_id"]])
    await pick_anims(user, {"Ходьба"})
    await user.click("Далее")
    check("Камера" in user.last()["caption"], "camera step")
    await user.click("3. 3/4 слева")
    check("Части тела" in user.last()["caption"], "parts step")
    await user.click("Слой шляпы")
    check("🚫 Слой шляпы" in json.dumps(user.last()["reply_markup"], ensure_ascii=False), "hat hidden")
    await user.click("Показать всё")
    await user.click("Далее")
    check("Вид" in user.last()["caption"], "extra step")
    await user.click("😎")
    await user.text("🔥")
    check("привязка 🔥" in user.last()["caption"], "emoji picked by message")
    await user.click("Далее")
    check("Итог" in user.last()["caption"] and "Ходьба" in user.last()["caption"], "hub shows summary")
    (out / "hub_preview.mp4").write_bytes(fake.media[user.last()["message_id"]])

    print("== user: create")
    t1 = time.time()
    await user.click("Создать")
    txt = user.last()["text"]
    print("   ", txt.replace("\n", " | ")[:300])
    pack = (await db.user_packs(USER_ID))[0]
    tg = fake.sets[pack.name]
    check(len(tg["stickers"]) == n_items and all(s["fmt"] == "video" for s in tg["stickers"]),
          f"{n_items} animated emoji in the Telegram set ({time.time() - t1:.1f}s)")
    check(all(s["emoji"] == "🔥" for s in tg["stickers"]), "emoji attached")
    check(tg["title"] == "Мой первый пак @chibi_test_bot", f"bot username appended to the title ({tg['title']})")
    (out / "emoji_0.webm").write_bytes(tg["stickers"][0]["data"])

    print("== user: second batch, static, into the same pack")
    await user.click("Добавить ещё")
    await user.text("Technoblade")
    await user.click("Далее: настройка")
    check("✅ 2. Minecraft Live" in json.dumps(user.last()["reply_markup"], ensure_ascii=False),
          "pack remembers last settings")
    await user.click("1. Классика")
    await user.click("2. Стив")
    await user.click("🎬 Анимация")
    await pick_anims(user, {"Без анимации"})
    await user.click("К итогу")
    check("Итог" in user.last()["caption"], "jump from hub returns to hub")
    await user.click("Создать")
    tg = fake.sets[pack.name]
    check(tg["stickers"][-1]["fmt"] == "static", "static emoji added (mixed pack)")
    (out / "emoji_static.png").write_bytes(tg["stickers"][-1]["data"])

    print("== user: pack card, list, redo, move, delete, overview, history, rename")
    await user.click("К паку")
    check("t.me/addemoji/" in user.last()["text"], "card has addemoji link")
    await user.click("Эмодзи (")
    await user.click("2. ")
    check("photo" in user.last() or "animation" in user.last(), "emoji card with preview")
    await user.click("Перерисовать")
    check("перерисовка" in user.last()["caption"], "replace wizard opened at hub")
    await user.click("🤸 Поза")
    await user.click("4. Зомби")
    before = [s["uid"] for s in fake.sets[pack.name]["stickers"]]
    await user.click("✅ Заменить")
    after = [s["uid"] for s in fake.sets[pack.name]["stickers"]]
    check(len(before) == len(after) and before[1] != after[1] and before[0] == after[0], "replaced in place")
    await user.click("К паку")
    await user.click("Эмодзи (")
    await user.click("3. ")
    await user.click("Сделать первым")
    first = await db.pack_emojis(pack.id)
    check(first[0].position == 0 and fake.sets[pack.name]["stickers"][0]["uid"] == first[0].file_unique_id,
          "moved to first, DB in sync")
    await user.click("Эмодзи (")
    await user.click("1. ")
    await user.click("Удалить")
    await user.click("🗑 Удалить")
    check(len(fake.sets[pack.name]["stickers"]) == len(after) - 1, "deleted from Telegram set")
    await user.click("Обзор")
    ov = [m for m in user.mine() if "photo" in m][-1]
    (out / "overview.png").write_bytes(fake.media[ov["message_id"]])
    check(True, "overview sheet sent")
    await user.click("К паку")
    await user.click("История")
    check("➕ добавил" in user.last()["text"], "history lists actions")
    await user.click("К паку")
    await user.click("Название")
    await user.text("Переименованный @chibi_test_bot")
    check(fake.sets[pack.name]["title"] == "Переименованный @chibi_test_bot", "renamed, suffix not duplicated")
    check((await db.get_pack(pack.id)).title == "Переименованный", "DB keeps the user's own part")
    fake.sets[pack.name]["title"] = "Старое название без суффикса"
    check(await app.stickers.fix_all_titles() == 1 and fake.sets[pack.name]["title"].endswith("@chibi_test_bot"),
          "existing packs get the suffix at startup")
    long = "Очень длинное название пака " * 4
    await user.click("Название")
    await user.text(long)
    t = fake.sets[pack.name]["title"]
    check(len(t) <= 64 and t.endswith(" @chibi_test_bot"), f"long title cut to fit 64 ({len(t)})")
    await user.click("Название")
    await user.text("Переименованный")

    print("== user: empty drafts do not count, but are capped")
    for i in range(2):
        await user.text("/new")
        await user.click("😀 Эмодзи-пак")
        await user.text(f"Пак {i + 2}")
        check(f"«Пак {i + 2}» создан" in user.last()["text"], f"draft «Пак {i + 2}» created")
    await user.text("/new")
    check("пустых" in user.last()["text"], "third empty draft refused")

    print("== quick add: skins sent outside the wizard")
    await user.text("/menu")
    check("Пак 2" in json.dumps(user.last()["reply_markup"], ensure_ascii=False), "packs listed in the main menu")
    await user.text("Notch")
    check("В какой пак" in user.last()["text"], "asks which pack")
    await user.doc("x.png", default_skin().png)
    check("2 сообщ" in user.last()["text"], "remembers several inputs")
    await user.click("Пак 2")
    txt = user.last()["text"]
    check("2 скина" in txt and "«Пак 2»" in txt, "pending skins applied to the chosen pack")
    await user.click("Отмена")

    print("== stale wizard button after a restart")
    from chibibot.tg.ui import Wiz
    await user._feed({"callback_query": {"id": "s", "from": user._user(), "chat_instance": "ci",
                                         "message": user.last(), "data": Wiz(act="make").pack()}})
    check("устарела" in fake.alerts[-1], "stale button explained")

    print("== admin")
    await adm.text("/start")
    check("администратор" in adm.last()["text"], "admin menu")
    await adm.click("Все паки")
    check("Переименованный" in adm.last()["text"] and "@player" in adm.last()["text"], "admin sees all packs + owner")
    await adm.click("Переименованный")
    card = adm.last()["text"]
    check("Владелец" in card and "Создан" in card and "Изменён" in card, "admin sees who/when")
    await adm.click("Добавить эмодзи")
    await adm.text("jeb_")
    await adm.click("Далее: настройка")
    await adm.click("1. Классика")
    await adm.click("1. Авто")
    await adm.click("Создать")
    check(fake.sets[pack.name]["owner"] == USER_ID, "admin added into the user's pack with owner id")
    await adm.click("К паку")
    await adm.click("История")
    check("@boss" in adm.last()["text"], "history shows the admin as editor")
    await adm.text("/start")
    await adm.click("Журнал")
    check("создал пак" in adm.last()["text"], "global log")
    await adm.text("/start")
    await adm.click("Пользователи")
    check("Player" in adm.last()["text"] or "@player" in adm.last()["text"], "users list")

    print("== several animations per skin at once (admin)")
    await adm.text("/new")
    await adm.click("😀 Эмодзи-пак")
    await adm.text("Мульти")
    await adm.text("Notch, jeb_")
    await adm.click("Далее: настройка")
    await adm.click("1. Классика")
    await adm.click("1. Авто")
    await adm.click("🎬 Анимация")
    await pick_anims(adm, {"Без анимации", "Ходьба", "Танец"})
    marks = json.dumps(adm.last()["reply_markup"], ensure_ascii=False)
    check("✅ 1. Без анимации" in marks and "✅ 3. Ходьба" in marks and "✅ 17. Танец" in marks,
          "three variants ticked, step stays open")
    await adm.click("К итогу")
    hub = adm.last()
    check("2 скина × 3 = 6 эмодзи" in hub["caption"] and "Создать 6 эмодзи" in json.dumps(hub["reply_markup"], ensure_ascii=False),
          "hub counts skins × variants")
    check("animation" in hub, "hub preview shows every variant (animated sheet)")
    await adm.click("Создать")
    mpack = next(p for p in await db.user_packs(ADMIN_ID) if p.title == "Мульти")
    mset = fake.sets[mpack.name]
    rows = await db.pack_emojis(mpack.id)
    check([e.label for e in rows] == ["Notch · Без анимации", "Notch · Ходьба", "Notch · Танец",
                                      "jeb_ · Без анимации", "jeb_ · Ходьба", "jeb_ · Танец"], "items grouped by skin")
    check([st["fmt"] for st in mset["stickers"]] == ["static", "video", "video"] * 2, "static + two animations each")
    check([e.settings["anim"] for e in rows] == ["none", "walk", "dance"] * 2
          and all(e.settings["anims"] == [e.settings["anim"]] for e in rows), "each item stores its own variant")
    check(all(e.settings["mode"] == "hd" for e in rows), "smooth rendering is the default")
    await adm.click("Добавить ещё")
    await adm.text("Notch")
    await adm.click("Далее: настройка")
    await adm.click("1. Классика")
    await adm.click("1. Авто")
    await adm.click("🎬 Анимация")
    await adm.click("Все анимации")
    marks = json.dumps(adm.last()["reply_markup"], ensure_ascii=False)
    check(marks.count("✅ ") >= 26, "«Все анимации» ticks every clip (static stays ticked)")
    await adm.click("Только статичная")
    marks = json.dumps(adm.last()["reply_markup"], ensure_ascii=False)
    check("✅ 1. Без анимации" in marks and marks.count("▫️ ") == 25, "«Только статичная» resets the selection")
    await adm.click("Отмена")

    print("== sticker pack (admin: no limits)")
    await adm.text("/new")
    await adm.click("🖼 Стикер-пак")
    check("Как назовём стикер-пак" in adm.last()["text"], "title prompt names the kind")
    await adm.text("Стикеры")
    check("Стикер-пак «Стикеры» создан" in adm.last()["text"], "sticker pack created")
    await adm.text("Technoblade, Grian")
    await adm.click("Далее: настройка")
    await adm.click("2. Minecraft Live")
    await adm.click("1. Авто")
    check("3 на скин" in adm.last()["caption"], "last used variants are remembered")
    await adm.click("🎬 Анимация")
    await pick_anims(adm, {"Без анимации"})
    await adm.click("К итогу")
    hub = adm.last()
    check("Создать 2 стикера" in json.dumps(hub["reply_markup"], ensure_ascii=False), "button counts stickers")
    from PIL import Image
    check(Image.open(io.BytesIO(fake.media[hub["message_id"]])).size == (512, 512), "hub preview is the real 512 px sticker")
    await adm.click("Создать")
    done = adm.last()["text"]
    spack = next(p for p in await db.user_packs(ADMIN_ID) if p.title == "Стикеры")
    sset = fake.sets[spack.name]
    check(sset["type"] == "regular" and len(sset["stickers"]) == 2, "regular sticker set with 2 stickers")
    check("t.me/addstickers/" in done and "Premium не нужен" in done, "done message: addstickers link, no Premium")
    check(any(c[0] == "regular" and c[2] == 512 for c in fake.checked), "stickers uploaded at 512 px")
    await adm.click("Добавить ещё")
    await adm.text("jeb_")
    await adm.click("Далее: настройка")
    await adm.click("1. Классика")
    await adm.click("1. Авто")
    await adm.click("🎬 Анимация")
    await pick_anims(adm, {"Танец"})
    await adm.click("К итогу")
    await adm.click("Создать")
    sset = fake.sets[spack.name]
    check(sset["stickers"][-1]["fmt"] == "video" and fake.checked[-1][:3] == ("regular", "video", 512),
          "animated sticker: 512 px WEBM")
    await adm.click("К паку")
    card = adm.last()["text"]
    check("стикер-пак" in card and "Стикеры: <b>3</b> / 120" in card, "card shows sticker count and 120 limit")
    await adm.click("Стикеры (3)")
    await adm.click("1. ")
    await adm.click("Перерисовать")
    await adm.click("🤸 Поза")
    await adm.click("5. T-поза")
    await adm.click("✅ Заменить")
    check(fake.checked[-1][0] == "regular" and fake.checked[-1][2] == 512, "redraw keeps sticker size")
    await adm.click("К паку")
    await adm.click("Обзор")
    check(True, "sticker overview sent")

    print("== limits: publishing, daily quota, personal limits")
    used = await db.usage_since(USER_ID, "0")
    added = len([e for p in await db.user_packs(USER_ID) for e in await db.pack_emojis(p.id) if e.created_by == USER_ID])
    check(used == added + 2, f"user quota = own adds + redraw + deleted one, admin work not counted (used {used})")
    check(used <= 10, "test expects at least 2 slots left")
    await user.text("/packs")
    await user.click("Пак 2")
    await user.click("Добавить эмодзи")
    await user.text("Notch")
    await user.click("Далее: настройка")
    await user.click("1. Классика")
    await user.click("1. Авто")
    await user.click("Создать")
    left = 12 - used - 1
    check(f"Осталось на сегодня: {left} из 12" in user.last()["text"], "remaining quota reported after creating")
    await user.text("/packs")
    await user.click("Пак 3")
    await user.click("Добавить эмодзи")
    check("Непустых паков уже 2 из 2" in user.last()["text"], "third pack cannot be filled: limit 2")
    await user.text("/packs")
    await user.click("Пак 2")
    await user.click("Добавить эмодзи")
    await user.text("jeb_, Dinnerbone, Grian")
    txt = user.last()["text"]
    check(f"В очереди: <b>{left} скин" in txt and "дневной лимит" in txt, "queue capped by the remaining quota")
    await user.click("Далее: настройка")
    await user.click("1. Классика")
    await user.click("1. Авто")
    await user.click("Создать")
    check("Осталось на сегодня: 0 из 12" in user.last()["text"], "quota used up")
    await user.click("Добавить ещё")
    check("Дневной лимит исчерпан" in user.last()["text"] and "Следующее можно будет добавить" in user.last()["text"],
          "adding refused with the time the next slot frees up")
    await adm.text(f"/setlimit {USER_ID} 3 20")
    check("Сохранено" in adm.last()["text"] and "20 (личный)" in adm.last()["text"], "admin set personal limits")
    await user.text("/menu")
    txt = user.last()["text"]
    check("2/3" in txt and "8</b> из 20" in txt, "user sees the new limits")
    await adm.text(f"/setlimit {USER_ID} - -")
    check("(по умолчанию)" in adm.last()["text"], "limits reset to defaults")

    print("== convert emoji pack <-> sticker pack")
    await user.text("/packs")
    await user.click("Переименованный")
    await user.click("Сделать стикер-пак из этого")
    txt = user.last()["text"]
    check("Создам новый стикер-пак" in txt and "Непустых паков у вас уже 2 из 2" in txt
          and "✅ Сделать" not in json.dumps(user.last()["reply_markup"], ensure_ascii=False),
          "player at the pack limit: conversion explained but blocked")
    await adm.text(f"/setlimit {USER_ID} 5 -")
    await user.text("/packs")
    await user.click("Переименованный")
    await user.click("Сделать стикер-пак из этого")
    txt = user.last()["text"]
    check("в дневной квоте осталось 0 из 12" in txt and "Места хватит" in txt,
          "enough packs but not enough quota: says when there will be room")
    await adm.text(f"/setlimit {USER_ID} - -")
    src_rows = await db.pack_emojis(pack.id)
    src_before = [s["uid"] for s in fake.sets[pack.name]["stickers"]]
    used_before = await db.usage_since(USER_ID, "0")
    await adm.text("/start")
    await adm.click("Все паки")
    await adm.click("Переименованный")
    await adm.click("Сделать стикер-пак из этого")
    check("перерисую в нём" in adm.last()["text"], "admin sees the conversion plan")
    n_checked = len(fake.checked)
    await adm.click("✅ Сделать стикер-пак")
    conv = next(p for p in await db.user_packs(USER_ID) if p.kind == "stickers")
    cset = fake.sets[conv.name]
    check(cset["type"] == "regular" and len(cset["stickers"]) == len(src_rows), "every emoji redrawn as a sticker")
    check(all(c[0] == "regular" and c[2] == 512 for c in fake.checked[n_checked:]), "all uploads are 512 px stickers")
    check([e.label for e in await db.pack_emojis(conv.id)] == [e.label for e in src_rows], "order kept")
    check([e.settings for e in await db.pack_emojis(conv.id)] == [e.settings for e in src_rows],
          "each item keeps its own settings")
    check(conv.owner_id == USER_ID and conv.title == "Переименованный", "new pack belongs to the player, same title")
    check([s["uid"] for s in fake.sets[pack.name]["stickers"]] == src_before, "source emoji pack untouched")
    check(await db.usage_since(USER_ID, "0") == used_before, "admin's conversion does not eat the player's quota")
    done = next(m for m in reversed(adm.mine()) if "Готово" in (m.get("text") or ""))["text"]
    check("t.me/addstickers/" in done and "остался без изменений" in done, "result message")
    await adm.click("К исходному")
    await adm.click("История")
    check("🔁 конвертировал" in adm.last()["text"], "conversion logged in the source pack's history")
    # and back: the admin's sticker pack into an emoji pack
    await adm.text("/packs")
    await adm.click("Стикеры")
    await adm.click("Сделать эмодзи-пак из этого")
    await adm.click("✅ Сделать эмодзи-пак")
    epack = next(p for p in await db.user_packs(ADMIN_ID) if p.kind == "emoji" and p.title == "Стикеры")
    eset = fake.sets[epack.name]
    check(eset["type"] == "custom_emoji" and len(eset["stickers"]) == 3
          and fake.checked[-1][0] == "custom_emoji" and fake.checked[-1][2] == 100,
          "sticker pack converted to a 100 px emoji pack")

    print("== /alert broadcast after an outage")
    await user.text("/alert")
    check("Оповещение" not in (user.last().get("text") or ""), "/alert is ignored for regular players")
    await adm.text("/alert")
    check("получат 1 чел." in adm.last()["text"], "preview counts recent users except the admin")
    await adm.click("Отправить")
    check("Бот снова работает" in user.last()["text"], "player received the alert")
    check("Доставлено: 1" in adm.last()["text"], "admin gets a delivery report")
    await adm.text("/alert 5 Проверка связи")
    check("Проверка связи" in adm.last()["text"], "custom text and window")
    await adm.click("Отмена")

    print("== user cannot open someone else's pack")
    admin_pack = await app.db.create_pack(ADMIN_ID, await app.stickers.new_name(), "Admin only", ADMIN_ID, None)
    from chibibot.tg.ui import PackCB
    raw = {"callback_query": {"id": "x", "from": user._user(), "chat_instance": "ci", "message": user.last(),
                              "data": PackCB(act="open", pid=admin_pack.id).pack()}}
    await user._feed(raw)
    check(fake.alerts and "чужой" in fake.alerts[-1], "foreign pack refused")

    print("== delete pack")
    await user.text("/packs")
    await user.click("Переименованный")
    await user.click("Удалить пак")
    await user.click("Да, удалить")
    check(pack.name not in fake.sets and await db.get_pack(pack.id) is None, "pack deleted everywhere")

    print(f"\nALL OK in {time.time() - t0:.1f}s; artefacts in {out}")
    await mojang.close()
    await db.close()
    worker.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
