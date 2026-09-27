"""Adding emojis: collect skins, walk through the settings with examples, create.

The settings live on one "board" message (a picture + buttons) that is edited in place,
so the chat does not fill up with previews.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
from dataclasses import dataclass

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from ..db import Pack, User
from ..jobs import WorkerCrashed, job_emoji, job_result, job_step
from ..render.encode import EncodeError
from ..render.engine import RenderError
from ..render.options import (ANIMATIONS, BODIES, BUSTS, CAMERAS, OUTLINES, PARTS, POSES, QUICK_EMOJI,
                              RENDER_MODES, SPEEDS, STYLES, RenderSettings, opt)
from ..sources import Collected, SkinItem, collect_file, collect_names, parse_names
from ..kinds import kind_of
from ..stickers import StickerError
from .app import App
from .media import set_caption, show_media
from .ui import Menu, PackCB, Wiz, btn, chunks, esc, kb, safe_delete, skin_word
from .uploader import Item, render_and_upload, why

log = logging.getLogger(__name__)
router = Router(name="wizard")

MAX_DOC_BYTES = 20 * 1024 * 1024


class W(StatesGroup):
    collect = State()
    config = State()


OPTION_STEPS = {
    "style": STYLES, "body": BODIES, "pose": POSES, "bust": BUSTS, "anim": ANIMATIONS, "cam": CAMERAS,
}
COLS = {"style": 2, "body": 3, "pose": 2, "bust": 2, "anim": 3, "cam": 2}
ADVANCED = ["pose", "bust", "anim", "cam", "parts", "extra"]
SECTION_BUTTONS = [
    ("🎨 Стиль", "style"), ("🧍 Тело", "body"), ("🤸 Поза", "pose"),
    ("🖼 Кадр", "bust"), ("🎬 Анимация", "anim"), ("📷 Камера", "cam"),
    ("👁 Части", "parts"), ("⚙️ Вид", "extra"),
]

STEP_TEXT = {
    "style": ("🎨 <b>Стиль</b>\n"
              "• <b>Классика</b> — оригинальный пиксель-арт чиби.\n"
              "• <b>Minecraft Live</b> — огромная голова, короткое тело и обводка каждой части, как в трансляциях Minecraft Live."),
    "body": ("🧍 <b>Тип тела</b>\n"
             "• <b>Стив</b> — руки 4 px, <b>Алекс</b> — тонкие руки 3 px.\n"
             "• <b>Авто</b> — определю по скину сам (для каждого скина отдельно)."),
    "pose": ("🤸 <b>Поза</b>\n"
             "Поза сохраняется при любом ракурсе камеры и относится к статичному варианту "
             "(«Без анимации» на шаге анимаций)."),
    "bust": ("🖼 <b>Кадр</b>\n"
             "Во весь рост, по пояс (руки и жест остаются в кадре), портрет (голова и плечи) или только голова."),
    "anim": ("🎬 <b>Анимации</b>\n"
             "Отметьте <b>одну или несколько</b> — каждый скин станет отдельной картинкой на каждый вариант. "
             "«Без анимации» — статичная картинка с выбранной позой, остальные — видео до 3 секунд.\n"
             "«Крадётся», «Ползёт», «Галоп» и «Бросок» ставят фигурку на четвереньки."),
    "cam": "📷 <b>Камера</b>\nДесять ракурсов: 3/4, анфас, профили, со спины, сверху и снизу.",
    "parts": ("👁 <b>Части тела</b>\n"
              "Нажмите, чтобы скрыть или показать часть. Второй слой скина (шляпа, куртка, рукава, штанины) "
              "переключается отдельно от основы: спрячьте основу — останется только одежда."),
    "extra": ("⚙️ <b>Вид</b>\n"
              "• <b>Отрисовка</b>: пиксель-арт (чёткие пиксели) или сглаженная.\n"
              "• <b>Обводка</b>: у Live по умолчанию обводится каждая часть, у Классики — без обводки.\n"
              "• <b>Скорость</b> — для анимаций.\n"
              "• <b>Эмодзи-привязка</b> — по нему ваш эмодзи или стикер ищется в Telegram. "
              "Можно прислать любой эмодзи сообщением."),
}


# ------------------------------------------------------------------ helpers

def _items(data: dict) -> list[dict]:
    return data.get("items") or []


def _settings(data: dict) -> RenderSettings:
    return RenderSettings.from_dict(data.get("s"))


def _hidden_text(s: RenderSettings) -> str:
    if not s.hidden:
        return "всё видно"
    names = [o.ru for o in PARTS if o.key in s.hidden]
    return ", ".join(names)


def summary(s: RenderSettings, first_slim: bool | None) -> str:
    body = opt("body", s.body).ru
    if s.body == "auto" and first_slim is not None:
        body += f" ({'Алекс' if first_slim else 'Стив'} у первого скина)"
    lines = [f"🎨 Стиль: <b>{opt('style', s.style).ru}</b>", f"🧍 Тело: <b>{body}</b>"]
    if len(s.anims) > 1:
        names = ", ".join(opt("anim", a).ru for a in s.anims)
        lines.append(f"🎬 Варианты ({len(s.anims)} на скин): <b>{names}</b>")
        if "none" in s.anims:
            lines.append(f"🤸 Поза статичного: <b>{opt('pose', s.pose).ru}</b>")
    elif s.animated:
        lines.append(f"🎬 Анимация: <b>{opt('anim', s.anim).ru}</b>")
    else:
        lines.append(f"🤸 Поза: <b>{opt('pose', s.pose).ru}</b> · статичный")
    if s.any_animated:
        lines[-1] += f" · скорость {opt('speed', s.speed).ru}"
    lines += [
        f"🖼 Кадр: <b>{opt('bust', s.bust).ru}</b>",
        f"📷 Камера: <b>{opt('cam', s.cam).ru}</b>",
        f"👁 Части: <b>{_hidden_text(s)}</b>",
        f"⚙️ Вид: <b>{opt('mode', s.mode).ru}</b>, обводка: <b>{opt('outline', s.outline).ru.lower()}</b>, "
        f"привязка {s.emoji}",
    ]
    return "\n".join(lines)


async def _pack(app: App, data: dict) -> Pack | None:
    return await app.db.get_pack(data.get("pid", 0))


def _make_label(data: dict) -> str:
    if data.get("replace"):
        return "✅ Заменить"
    return f"✅ Создать {kind_of(data.get('kind')).count(_total(data))}"


def _total(data: dict) -> int:
    """How many emojis/stickers "Create" will make: every skin times every picked variant."""
    return len(_items(data)) * len(_settings(data).anims)


@dataclass
class Room:
    total: int           # how many more skins fit into this batch
    why: str             # the limit that binds
    left: int | None     # emoji quota left for the last 24 h (None = unlimited)


async def _room(app: App, pack: Pack, uid: int, admin: bool) -> Room:
    q = await app.quota(uid, admin)
    batch = app.batch_limit(admin)
    options = [(batch, f"за раз можно до {batch}"),
               (pack.k.max_items - pack.count, f"в паке максимум {pack.k.max_items}")]
    if q.left is not None:
        options.append((q.left, f"дневной лимит {q.daily_limit} за 24 ч"))
    total, why = min(options, key=lambda o: o[0])
    return Room(max(0, total), why, q.left)


async def _room_for(app: App, data: dict, pack: Pack) -> Room:
    return await _room(app, pack, data.get("uid", 0), data.get("admin", False))


def collect_text(pack: Pack, data: dict, room: Room) -> str:
    items = _items(data)
    limits = f"В паке: {pack.count}/{pack.k.max_items} · за раз можно до {room.total}"
    if room.left is not None:
        limits += f" · сегодня осталось {room.left}"
    lines = [
        f"📥 <b>Добавление в «{esc(pack.title)}»</b>",
        "Пришлите:",
        "• <b>ники</b> Minecraft — через запятую, <code>;</code> или с новой строки;",
        "• <b>PNG-скины файлом</b> (📎 → Файл, без сжатия);",
        "• <b>ZIP-архив</b> со скинами (и/или .txt со списком ников).",
        "Можно несколькими сообщениями.",
        "",
        limits + ".",
    ]
    if items:
        names = ", ".join(esc(i["label"]) for i in items[:40]) + (" …" if len(items) > 40 else "")
        lines.append(f"✅ В очереди: <b>{skin_word(len(items))}</b> — {names}")
    else:
        lines.append("Очередь пока пуста.")
    errs = data.get("errors") or []
    if errs:
        lines.append("⚠️ Не получилось:")
        lines += [f"• {esc(e)}" for e in errs[-10:]]
    return "\n".join(lines)


def collect_kb(data: dict):
    n = len(_items(data))
    rows = []
    if n:
        rows.append([btn(f"➡️ Далее: настройка ({n})", Wiz(act="go"))])
        rows.append([btn("🧹 Очистить очередь", Wiz(act="clear"))])
    rows.append([btn("❌ Отмена", Wiz(act="cancel"))])
    return kb(*rows)


# ------------------------------------------------------------------ entering the wizard

async def _notice(target: Message, replace_msg: Message | None, text: str, pack: Pack) -> None:
    """Explain why adding is not possible right now, in place of the pack card if we have it."""
    markup = kb([btn("📦 К паку", PackCB(act="open", pid=pack.id)), btn("🏠 Меню", Menu(act="home"))])
    if replace_msg is not None and replace_msg.text is not None:
        try:
            await replace_msg.edit_text(text, reply_markup=markup)
            return
        except TelegramBadRequest:
            pass
    await target.answer(text, reply_markup=markup)
    if replace_msg is not None:
        await safe_delete(replace_msg)


async def _blocked(app: App, uid: int, admin: bool, pack: Pack, need: int = 1) -> str | None:
    """Reason the user cannot add `need` emojis to this pack right now, if any."""
    q = await app.quota(uid, admin)
    if q.left is not None and q.left < need:
        return app.quota_exhausted_text(q)
    return await app.why_no_publish(pack, admin)


async def start_collect(target: Message, state: FSMContext, app: App, me: User, admin: bool, pack: Pack,
                        intro: str | None = None, replace_msg: Message | None = None,
                        pending: dict | None = None) -> None:
    reason = await _blocked(app, me.id, admin, pack)
    if reason:
        await state.clear()
        await _notice(target, replace_msg, reason, pack)
        return
    settings = pack.settings or me.settings or RenderSettings().to_dict()
    await state.set_state(W.collect)
    await state.set_data({"pid": pack.id, "items": [], "errors": [], "s": settings, "admin": admin, "uid": me.id,
                          "kind": pack.kind})
    data = await state.get_data()
    text = collect_text(pack, data, await _room(app, pack, me.id, admin))
    if intro:
        text = intro + "\n\n" + text
    msg = None
    if replace_msg is not None and replace_msg.text is not None:
        try:
            msg = await replace_msg.edit_text(text, reply_markup=collect_kb(data))
        except TelegramBadRequest:
            msg = None
    if msg is None:
        msg = await target.answer(text, reply_markup=collect_kb(data))
        if replace_msg is not None:
            await safe_delete(replace_msg)
    await state.update_data(status=msg.message_id)
    if pending:
        async with app.lock(me.id):
            await apply_pending(target.bot, target.chat.id, state, app, pending)
            await _refresh_status(target.bot, target.chat.id, state, app)


async def start_replace(target: Message, state: FSMContext, app: App, me: User, admin: bool, pack: Pack, e) -> None:
    q = await app.quota(me.id, admin)
    if q.left == 0:
        await _notice(target, None, app.quota_exhausted_text(q), pack)
        await safe_delete(target)
        return
    item = SkinItem(e.label, e.source, e.skin_sha1, e.slim)
    await state.set_state(W.config)
    await state.set_data({
        "pid": pack.id, "items": [item.__dict__], "errors": [], "s": RenderSettings.from_dict(e.settings).to_dict(),
        "replace": e.id, "step": "hub", "hist": [], "ret": "hub", "admin": admin, "uid": me.id, "kind": pack.kind,
    })
    await render_board(target.bot, app, state, target.chat.id)
    await safe_delete(target)


async def _refresh_status(bot: Bot, chat_id: int, state: FSMContext, app: App) -> None:
    """Re-send the queue message at the bottom of the chat (and drop the old one)."""
    data = await state.get_data()
    pack = await _pack(app, data)
    if not pack:
        return
    old = data.get("status")
    msg = await bot.send_message(chat_id, collect_text(pack, data, await _room_for(app, data, pack)),
                                 reply_markup=collect_kb(data))
    await state.update_data(status=msg.message_id)
    if old:
        try:
            await bot.delete_message(chat_id, old)
        except TelegramBadRequest:
            pass


async def _merge(state: FSMContext, app: App, got: Collected, extra_errors: list[str] | None = None) -> None:
    data = await state.get_data()
    pack = await _pack(app, data)
    items = _items(data)
    errors = list(data.get("errors") or []) + list(extra_errors or []) + got.errors
    room = await _room_for(app, data, pack) if pack else Room(0, "пак не найден", None)
    have = {(i["sha1"], i["label"].lower()) for i in items}
    dropped = 0
    for it in got.items:
        key = (it.sha1, it.label.lower())
        if key in have:
            continue
        if len(items) >= room.total:
            dropped += 1
            continue
        items.append(it.__dict__)
        have.add(key)
    if dropped:
        errors.append(f"не влезло {skin_word(dropped)}: {room.why}")
    await state.update_data(items=items, errors=errors[-20:])


# ------------------------------------------------------------------ collecting skins

async def _collect_text(bot: Bot, chat_id: int, state: FSMContext, app: App, text: str) -> bool:
    names, bad = parse_names(text or "")
    if not names:
        return False
    await bot.send_chat_action(chat_id, "typing")
    got = await collect_names(app.mojang, app.store, names)
    await _merge(state, app, got, [f"«{b[:20]}»: это не ник" for b in bad[:5]])
    return True


async def _collect_doc(bot: Bot, chat_id: int, state: FSMContext, app: App, file_id: str, name: str | None,
                       size: int | None) -> None:
    name = name or "skin.png"
    if size and size > MAX_DOC_BYTES:
        await _merge(state, app, Collected([], [f"{name}: файл больше 20 МБ"], []))
        return
    await bot.send_chat_action(chat_id, "typing")
    buf = io.BytesIO()
    await bot.download(file_id, destination=buf)
    got = await asyncio.to_thread(collect_file, app.store, name, buf.getvalue())
    if got.names:
        more = await collect_names(app.mojang, app.store, got.names)
        got = Collected(got.items + more.items, got.errors + more.errors, [])
    await _merge(state, app, got)


async def apply_pending(bot: Bot, chat_id: int, state: FSMContext, app: App, pending: dict) -> None:
    """Skins sent before a pack was chosen (see fallback.py)."""
    for p in pending.get("list", []):
        if p["type"] == "text":
            await _collect_text(bot, chat_id, state, app, p["text"])
        elif p["type"] == "doc":
            await _collect_doc(bot, chat_id, state, app, p["file_id"], p.get("name"), p.get("size"))


def _doc_args(message: Message) -> tuple[str, str | None, int | None]:
    d = message.document
    return d.file_id, d.file_name, d.file_size


@router.message(W.collect, F.document)
async def msg_collect_doc(message: Message, state: FSMContext, app: App, me: User):
    async with app.lock(me.id):
        await _collect_doc(message.bot, message.chat.id, state, app, *_doc_args(message))
        await _refresh_status(message.bot, message.chat.id, state, app)


@router.message(W.collect, F.text & ~F.text.startswith("/"))
async def msg_collect_text(message: Message, state: FSMContext, app: App, me: User):
    async with app.lock(me.id):
        if not await _collect_text(message.bot, message.chat.id, state, app, message.text):
            await message.answer("Не вижу ников. Ник Minecraft — от 1 до 16 латинских букв, цифр или «_». "
                                 "Несколько — через запятую, <code>;</code> или с новой строки.")
            return
        await _refresh_status(message.bot, message.chat.id, state, app)


@router.message(W.collect, F.photo)
@router.message(W.config, F.photo)
async def msg_photo(message: Message):
    await message.answer("🖼 Картинку Telegram сжал и испортил прозрачность. "
                         "Отправьте PNG <b>как файл</b>: 📎 → Файл (или «Отправить без сжатия»).")


@router.message(W.collect, F.sticker | F.video | F.animation | F.voice | F.audio)
async def msg_collect_other(message: Message):
    await message.answer("Жду ники, PNG-скины файлом или ZIP-архив.")


@router.callback_query(W.collect, Wiz.filter(F.act == "clear"))
async def cb_clear(cq: CallbackQuery, state: FSMContext, app: App, me: User):
    async with app.lock(me.id):
        await state.update_data(items=[], errors=[])
        data = await state.get_data()
        pack = await _pack(app, data)
        if pack:
            try:
                await cq.message.edit_text(collect_text(pack, data, await _room_for(app, data, pack)),
                                           reply_markup=collect_kb(data))
            except TelegramBadRequest:
                pass
    await cq.answer("Очередь очищена")


@router.callback_query(W.collect, Wiz.filter(F.act == "go"))
async def cb_go(cq: CallbackQuery, state: FSMContext, bot: Bot, app: App, me: User):
    if app.lock(me.id).locked():
        await cq.answer("⏳ Ещё загружаю скины…")
        return
    async with app.lock(me.id):
        data = await state.get_data()
        if not _items(data):
            await cq.answer("Сначала пришлите хотя бы один скин.", show_alert=True)
            return
        await cq.answer()
        await state.set_state(W.config)
        await state.update_data(step="style", hist=[], ret="next", board=None)
        await safe_delete(cq.message)
        await render_board(bot, app, state, cq.message.chat.id)


# ------------------------------------------------------------------ the board

def _media_key(step: str, first: dict, s: RenderSettings, size: int) -> str:
    d = s.to_dict()
    d.pop("emoji", None)
    return f"{step}|{size}|{first['sha1']}|{first.get('slim')}|{json.dumps(d, sort_keys=True)}"


def board_caption(step: str, s: RenderSettings, data: dict, pack: Pack | None, capacity: int | None = None) -> str:
    items = _items(data)
    first = items[0]
    head = f"{kind_of(data.get('kind')).icon} «{esc(pack.title if pack else '?')}»"
    if data.get("replace"):
        head += f" · перерисовка «{esc(first['label'])}»"
    else:
        head += f" · в очереди {skin_word(len(items))}"
    if step == "hub":
        body = "✨ <b>Итог</b>\n" + summary(s, first.get("slim"))
        if not data.get("replace") and len(s.anims) > 1:
            body += (f"\n\n📦 Всего получится: <b>{skin_word(len(items))} × {len(s.anims)} = "
                     f"{kind_of(data.get('kind')).count(_total(data))}</b>")
        if capacity is not None and capacity < _total(data):
            body += (f"\n⚠️ Сейчас влезет только {capacity} — остальное упрётся в лимит пака "
                     "или дневную квоту.")
        body += "\n\nМожно создавать или донастроить позу, кадр, анимацию, камеру и части тела."
    else:
        body = STEP_TEXT[step]
        if step in ("parts", "extra"):
            body += "\n\n" + summary(s, first.get("slim"))
    foot = f"Превью на скине: <b>{esc(first['label'])}</b>"
    if step in OPTION_STEPS:
        foot += " · номера на картинке = номера кнопок"
    return f"{head}\n\n{body}\n\n{foot}"


def _nav(data: dict, step: str) -> list[list]:
    rows = []
    back = btn("⬅️ Назад", Wiz(act="back"))
    if step != "hub":
        if data.get("ret") == "hub":
            rows.append([back, btn("↩️ К итогу", Wiz(act="step", val="hub"))])
        else:
            rows.append([back, btn("Далее ➡️", Wiz(act="next"))])
    rows.append([btn(_make_label(data), Wiz(act="make"))])
    rows.append([btn("❌ Отмена", Wiz(act="cancel"))])
    return rows


def board_kb(step: str, s: RenderSettings, data: dict):
    rows: list[list] = []
    if step in OPTION_STEPS:
        current = getattr(s, step)
        multi = step == "anim" and not data.get("replace")
        opts = OPTION_STEPS[step]
        buttons = []
        for i, o in enumerate(opts):
            chosen = o.key in s.anims if multi else o.key == current
            mark = "✅ " if chosen else ("▫️ " if multi else "")
            buttons.append(btn(f"{mark}{i + 1}. {o.ru}", Wiz(act="set", val=f"{step}|{o.key}")))
        if step == "anim":
            if multi:
                rows.append([btn("🎬 Все анимации", Wiz(act="anims", val="all")),
                             btn("↺ Только статичная", Wiz(act="anims", val="none"))])
            rows.append([buttons[0]])
            rows += chunks(buttons[1:], COLS[step])
        else:
            rows += chunks(buttons, COLS[step])
    elif step == "parts":
        hidden = set(s.hidden)
        pairs = chunks(PARTS, 2)
        for pair in pairs:
            rows.append([btn(("🚫 " if p.key in hidden else "👁 ") + p.ru, Wiz(act="part", val=p.key)) for p in pair])
        rows.append([btn("👁 Показать всё", Wiz(act="allparts"))])
    elif step == "extra":
        rows.append([btn(("✅ " if s.mode == o.key else "") + f"{o.icon} {o.ru}", Wiz(act="set", val=f"mode|{o.key}"))
                     for o in RENDER_MODES])
        outl = [btn(("✅ " if s.outline == o.key else "") + o.ru, Wiz(act="set", val=f"outline|{o.key}")) for o in OUTLINES]
        rows += chunks(outl, 2)
        if s.any_animated:
            rows.append([btn(("✅ " if s.speed == o.key else "") + o.ru, Wiz(act="set", val=f"speed|{o.key}"))
                         for o in SPEEDS])
        em = [btn(("✅" if s.emoji == e else "") + e, Wiz(act="set", val=f"emoji|{e}")) for e in QUICK_EMOJI]
        rows += chunks(em, 6)
    elif step == "hub":
        rows.append([btn(_make_label(data), Wiz(act="make"))])
        if not data.get("replace"):
            rows.append([btn("⚙️ Настроить дальше: поза, анимация, камера…", Wiz(act="adv"))])
        else:
            rows.append([btn("⚙️ Пошаговая настройка", Wiz(act="adv"))])
        sec = [btn(t, Wiz(act="step", val=k)) for t, k in SECTION_BUTTONS]
        rows += chunks(sec, 3)
        last = [btn("❌ Отмена", Wiz(act="cancel"))]
        if data.get("hist"):
            last.insert(0, btn("⬅️ Назад", Wiz(act="back")))
        rows.append(last)
        return kb(*rows)
    rows += _nav(data, step)
    return kb(*rows)


async def render_board(bot: Bot, app: App, state: FSMContext, chat_id: int) -> None:
    data = await state.get_data()
    step = data.get("step", "hub")
    s = _settings(data)
    first = _items(data)[0]
    pack = await _pack(app, data)
    capacity = None
    if step == "hub" and pack and not data.get("replace"):
        q = await app.quota(data.get("uid", 0), data.get("admin", False))
        capacity = max(0, pack.k.max_items - pack.count)
        if q.left is not None:
            capacity = min(capacity, q.left)
    caption = board_caption(step, s, data, pack, capacity)
    markup = board_kb(step, s, data)
    media_step = step if step in OPTION_STEPS else "result"
    size = kind_of(data.get("kind")).size
    key = _media_key(media_step, first, s, size)
    old = data.get("board")
    cached = app.media.get(key)
    if cached:
        kind, payload = cached
        name = ""
    else:
        slow = media_step == "anim" or s.any_animated
        if slow and old:
            await set_caption(bot, chat_id, old, "⏳ Рисую примеры…")
        try:
            skin = app.store.load_bytes(first["sha1"])
            if media_step == "result":
                heavy = len(s.anims) > 1 and s.any_animated  # a sheet of every picked variant
                kind, payload, name = await app.worker.run(job_result, skin, first.get("slim"), s.to_dict(), size,
                                                           heavy=heavy)
            else:
                heavy = media_step == "anim" or (media_step == "cam" and s.animated)
                kind, payload, name = await app.worker.run(job_step, skin, first.get("slim"), s.to_dict(),
                                                           media_step, size, heavy=heavy)
        except (RenderError, EncodeError, WorkerCrashed) as exc:
            kind, payload, name = "photo", _error_png(str(exc)), "error.png"
            key = None
        except Exception as exc:  # noqa: BLE001 - the board must stay usable whatever happened
            log.exception("preview %s failed", media_step)
            kind, payload, name = "photo", _error_png(f"не удалось нарисовать пример ({exc.__class__.__name__}), "
                                                      "попробуйте ещё раз"), "error.png"
            key = None
    msg = await show_media(app, bot, chat_id, old, kind, payload, name, caption, markup, cache_key=key)
    if msg is not None:
        await state.update_data(board=msg.message_id)


def _error_png(text: str) -> bytes:
    import textwrap

    from PIL import Image, ImageDraw

    from ..render.previews import font

    im = Image.new("RGB", (480, 220), (250, 235, 235))
    d = ImageDraw.Draw(im)
    lines = textwrap.wrap("⚠ " + text, 38)[:6]
    d.multiline_text((16, 110 - 13 * len(lines)), "\n".join(lines), fill=(120, 30, 30), font=font(18), spacing=6)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


# ------------------------------------------------------------------ board navigation

async def _guard(cq: CallbackQuery, state: FSMContext, app: App, me: User) -> dict | None:
    data = await state.get_data()
    if not data.get("board") or cq.message.message_id != data.get("board"):
        await cq.answer("Это старое сообщение — пользуйтесь последним.", show_alert=False)
        return None
    if app.lock(me.id).locked():
        await cq.answer("⏳ Подождите, ещё рисую…")
        return None
    return data


def _next_step(step: str, data: dict) -> str:
    if data.get("ret") == "hub":
        return "hub"
    if step == "style":
        return "body"
    if step == "body":
        return "hub"
    if step in ADVANCED:
        i = ADVANCED.index(step)
        return ADVANCED[i + 1] if i + 1 < len(ADVANCED) else "hub"
    return "hub"


async def _go(cq: CallbackQuery, state: FSMContext, bot: Bot, app: App, me: User, step: str,
              push: bool = True, ret: str | None = None) -> None:
    data = await state.get_data()
    hist = list(data.get("hist") or [])
    cur = data.get("step")
    if push and cur and cur != step:
        hist.append(cur)
    upd = {"step": step, "hist": hist[-20:]}
    if ret is not None:
        upd["ret"] = ret
    if step == "hub":
        upd["ret"] = "hub"
    await state.update_data(**upd)
    await cq.answer()
    async with app.lock(me.id):
        await render_board(bot, app, state, cq.message.chat.id)


@router.callback_query(W.config, Wiz.filter(F.act == "set"))
async def cb_set(cq: CallbackQuery, callback_data: Wiz, state: FSMContext, bot: Bot, app: App, me: User):
    data = await _guard(cq, state, app, me)
    if data is None:
        return
    group, _, key = callback_data.val.partition("|")
    s = _settings(data)
    multi = group == "anim" and not data.get("replace")
    if group == "emoji":
        s.emoji = key
    elif multi:
        # tick/untick one variant; at least one always stays picked
        picked = set(s.anims) ^ {key}
        if not picked:
            await cq.answer("Хотя бы один вариант должен остаться.", show_alert=True)
            return
        s.anims = [o.key for o in ANIMATIONS if o.key in picked]
        s.anim = key if key in picked else s.anims[0]
    elif group in ("style", "body", "pose", "bust", "anim", "cam", "mode", "outline", "speed"):
        setattr(s, group, key)
        if group == "anim":  # redrawing one item: a single variant
            s.anims = [key]
        if group == "pose":
            # a pose is what the static variant shows, so make sure there is one
            if data.get("replace"):
                s.anim, s.anims = "none", ["none"]
            elif "none" not in s.anims:
                s.anims = ["none"] + s.anims
                s.anim = "none"
    else:
        await cq.answer()
        return
    await state.update_data(s=RenderSettings.from_dict(s.to_dict()).to_dict())
    step = data.get("step")
    if step in OPTION_STEPS and not multi:
        await _go(cq, state, bot, app, me, _next_step(step, data))
    else:
        await _go(cq, state, bot, app, me, step, push=False)


@router.callback_query(W.config, Wiz.filter(F.act == "anims"))
async def cb_anims_bulk(cq: CallbackQuery, callback_data: Wiz, state: FSMContext, bot: Bot, app: App, me: User):
    """«Все анимации» ticks every animated clip (the static variant stays as it was);
    «Только статичная» goes back to just the static pose."""
    data = await _guard(cq, state, app, me)
    if data is None or data.get("replace"):
        if data is not None:
            await cq.answer()
        return
    s = _settings(data)
    if callback_data.val == "all":
        keep_static = "none" in s.anims
        s.anims = [o.key for o in ANIMATIONS if o.key != "none" or keep_static]
        s.anim = s.anims[0]
    else:
        s.anims, s.anim = ["none"], "none"
    await state.update_data(s=s.to_dict())
    await _go(cq, state, bot, app, me, "anim", push=False)


@router.callback_query(W.config, Wiz.filter(F.act == "part"))
async def cb_part(cq: CallbackQuery, callback_data: Wiz, state: FSMContext, bot: Bot, app: App, me: User):
    data = await _guard(cq, state, app, me)
    if data is None:
        return
    s = _settings(data)
    hidden = set(s.hidden)
    hidden ^= {callback_data.val}
    s.hidden = [p.key for p in PARTS if p.key in hidden]
    if len(s.hidden) == len(PARTS):
        await cq.answer("Хотя бы одна часть должна остаться видимой.", show_alert=True)
        return
    await state.update_data(s=s.to_dict())
    await _go(cq, state, bot, app, me, "parts", push=False)


@router.callback_query(W.config, Wiz.filter(F.act == "allparts"))
async def cb_allparts(cq: CallbackQuery, state: FSMContext, bot: Bot, app: App, me: User):
    data = await _guard(cq, state, app, me)
    if data is None:
        return
    s = _settings(data)
    s.hidden = []
    await state.update_data(s=s.to_dict())
    await _go(cq, state, bot, app, me, "parts", push=False)


@router.callback_query(W.config, Wiz.filter(F.act == "next"))
async def cb_next(cq: CallbackQuery, state: FSMContext, bot: Bot, app: App, me: User):
    data = await _guard(cq, state, app, me)
    if data is None:
        return
    await _go(cq, state, bot, app, me, _next_step(data.get("step", "hub"), data))


@router.callback_query(W.config, Wiz.filter(F.act == "adv"))
async def cb_adv(cq: CallbackQuery, state: FSMContext, bot: Bot, app: App, me: User):
    data = await _guard(cq, state, app, me)
    if data is None:
        return
    await _go(cq, state, bot, app, me, "pose", ret="next")


@router.callback_query(W.config, Wiz.filter(F.act == "step"))
async def cb_step(cq: CallbackQuery, callback_data: Wiz, state: FSMContext, bot: Bot, app: App, me: User):
    data = await _guard(cq, state, app, me)
    if data is None:
        return
    step = callback_data.val
    if step not in OPTION_STEPS and step not in ("parts", "extra", "hub"):
        await cq.answer()
        return
    await _go(cq, state, bot, app, me, step, ret="hub")


@router.callback_query(W.config, Wiz.filter(F.act == "back"))
async def cb_back(cq: CallbackQuery, state: FSMContext, bot: Bot, app: App, me: User, admin: bool):
    data = await _guard(cq, state, app, me)
    if data is None:
        return
    hist = list(data.get("hist") or [])
    if not hist:
        if data.get("replace"):
            await cq.answer()
            return
        # back to the skin queue
        await cq.answer()
        await state.set_state(W.collect)
        await state.update_data(board=None)
        pack = await _pack(app, data)
        if pack:
            new = await cq.message.answer(collect_text(pack, data, await _room_for(app, data, pack)),
                                          reply_markup=collect_kb(data))
            await state.update_data(status=new.message_id)
        await safe_delete(cq.message)
        return
    prev = hist.pop()
    ret = "next" if prev in ("style", "body") or data.get("ret") == "next" else data.get("ret")
    await state.update_data(hist=hist, step=prev, ret=ret if prev != "hub" else "hub")
    await cq.answer()
    async with app.lock(me.id):
        await render_board(bot, app, state, cq.message.chat.id)


@router.callback_query(Wiz.filter(F.act == "cancel"))
async def cb_cancel(cq: CallbackQuery, state: FSMContext, app: App, me: User, admin: bool):
    from .packs import open_pack

    data = await state.get_data()
    await state.clear()
    await cq.answer("Отменено")
    pack = await _pack(app, data)
    if pack and await app.can_edit(me.id, pack):
        await open_pack(cq.message, app, pack, me, admin, edit=False)
    await safe_delete(cq.message)


# text/doc input while configuring: an emoji sets the emoji, names/files join the queue
def _looks_like_emoji(text: str) -> bool:
    t = text.strip()
    return 0 < len(t) <= 10 and not any(ch.isalnum() for ch in t) and any(ord(ch) >= 0x2190 for ch in t)


@router.message(W.config, F.text & ~F.text.startswith("/"))
async def msg_config_text(message: Message, state: FSMContext, bot: Bot, app: App, me: User):
    data = await state.get_data()
    if _looks_like_emoji(message.text):
        s = _settings(data)
        s.emoji = message.text.strip()
        await state.update_data(s=s.to_dict())
        async with app.lock(me.id):
            await render_board(bot, app, state, message.chat.id)
        await safe_delete(message)
        return
    if data.get("replace"):
        await message.answer("Сейчас идёт перерисовка одной картинки — новые скины сюда не добавить.")
        return
    async with app.lock(me.id):
        if await _collect_text(bot, message.chat.id, state, app, message.text):
            await render_board(bot, app, state, message.chat.id)
            data = await state.get_data()
            errs = data.get("errors") or []
            await message.answer(f"➕ Очередь: {skin_word(len(_items(data)))}."
                                 + (f"\n⚠️ {esc(errs[-1])}" if errs else ""))
        else:
            await message.answer("Пришлите эмодзи (для привязки) или ники, чтобы добавить их в очередь.")


@router.message(W.config, F.document)
async def msg_config_doc(message: Message, state: FSMContext, bot: Bot, app: App, me: User):
    data = await state.get_data()
    if data.get("replace"):
        await message.answer("Сейчас идёт перерисовка одной картинки — новые скины сюда не добавить.")
        return
    async with app.lock(me.id):
        await _collect_doc(bot, message.chat.id, state, app, *_doc_args(message))
        await render_board(bot, app, state, message.chat.id)
        data = await state.get_data()
        await message.answer(f"➕ Очередь: {skin_word(len(_items(data)))}.")


# ------------------------------------------------------------------ creating

@router.callback_query(W.config, Wiz.filter(F.act == "make"))
async def cb_make(cq: CallbackQuery, state: FSMContext, bot: Bot, app: App, me: User, admin: bool):
    data = await _guard(cq, state, app, me)
    if data is None:
        return
    pack = await _pack(app, data)
    if not pack or not await app.can_edit(me.id, pack):
        await cq.answer("Пак не найден.", show_alert=True)
        await state.clear()
        return
    await state.clear()
    await cq.answer("Поехали!")
    async with app.lock(me.id):
        if data.get("replace"):
            await _do_replace(cq, bot, app, me, admin, pack, data)
        else:
            await _do_create(cq, bot, app, me, admin, pack, data)


def _done_kb(pack: Pack):
    rows = []
    if pack.tg_created:
        rows.append([btn("🔗 Открыть пак в Telegram", url=pack.link)])
    rows.append([btn("➕ Добавить ещё", PackCB(act="add", pid=pack.id)), btn("📦 К паку", PackCB(act="open", pid=pack.id))])
    return kb(*rows)


async def _do_create(cq: CallbackQuery, bot: Bot, app: App, me: User, admin: bool, pack: Pack,
                     data: dict) -> None:
    chat_id = cq.message.chat.id
    board = data.get("board")
    s = _settings(data)
    sd = s.to_dict()
    items = _items(data)
    async with app.stickers.lock(pack.id):
        try:
            await app.stickers.sync(pack)
        except Exception as exc:  # noqa: BLE001
            log.warning("sync before create failed: %s", exc)
        pack = await app.db.get_pack(pack.id) or pack
        # limits are checked again here: time has passed since the queue was filled
        reason = await app.why_no_publish(pack, admin)
        if reason:
            await set_caption(bot, chat_id, board, "⚠️ Не получилось")
            await bot.send_message(chat_id, reason, reply_markup=_done_kb(pack))
            return
        # every skin × every picked variant, grouped by skin: Notch-idle, Notch-walk, jeb_-idle, …
        many = len(s.anims) > 1
        planned = [
            Item(f"{it['label']} · {opt('anim', a).ru}" if many else it["label"], it["source"], it["sha1"],
                 it.get("slim"), s.variant(a).to_dict(), s.emoji, keyword=it["label"])
            for it in items for a in s.anims
        ]
        q = await app.quota(me.id, admin)
        room = max(0, pack.k.max_items - pack.count)
        quota_room = len(planned) if q.left is None else q.left
        todo = planned[:min(room, quota_room)]
        skipped = len(planned) - min(len(planned), room)
        over_quota = max(0, min(len(planned), room) - len(todo))
        total = len(todo)
        await set_caption(bot, chat_id, board, f"⏳ Рисую и загружаю в «{esc(pack.title)}»: 0/{total}…")

        async def progress(n: int, of: int) -> None:
            await set_caption(bot, chat_id, board, f"⏳ Рисую и загружаю в «{esc(pack.title)}»: {n}/{of}…")

        res = await render_and_upload(app, pack, todo, me.id, progress)
        ok, failed = res.ok, res.failed
        if ok:
            await app.db.touch_pack(pack.id, me.id, settings=sd)
            kinds = ", ".join(opt("anim", a).ru for a in s.anims)
            await app.db.log(me.id, "emoji_add", pack, details=f"{len(ok)} шт. ({kinds}): " + ", ".join(ok)[:900])
        await app.db.save_user_settings(me.id, sd)
    pack = await app.db.get_pack(pack.id) or pack
    lines = []
    if ok:
        lines.append(f"✅ Готово! В пак «<b>{esc(pack.title)}</b>» добавлено {pack.k.count(len(ok))}.")
        if pack.tg_created:
            lines.append(f"🔗 <a href=\"{pack.link}\">t.me/{pack.k.link_prefix}/{pack.name}</a> — откройте, чтобы добавить пак.")
        if pack.kind == "stickers":
            lines.append("ℹ️ Стикеры работают у всех, Premium не нужен.")
        else:
            lines.append("ℹ️ Отправлять свои эмодзи в сообщениях могут пользователи Telegram Premium.")
    else:
        lines.append("😔 Не удалось ничего добавить.")
    if skipped:
        lines.append(f"⚠️ Ещё {pack.k.count(skipped)} не влезли: в паке максимум {pack.k.count(pack.k.max_items)}.")
    if over_quota:
        q = await app.quota(me.id, admin)
        lines.append(f"⏳ Ещё {pack.k.count(over_quota)} не добавлены: дневной лимит {q.daily_limit} за 24 часа. "
                     f"Снова можно будет {app.when(q.next_free)}.")
    elif not admin:
        q = await app.quota(me.id, admin)
        if q.left is not None:
            lines.append(f"😀 Осталось на сегодня: {q.left} из {q.daily_limit}.")
    if failed:
        lines.append(f"⚠️ Ошибки ({len(failed)}):")
        lines += [f"• {esc(f)}" for f in failed[:15]]
    if board:
        await set_caption(bot, chat_id, board, "✅ Готово" if ok else "⚠️ Не получилось")
    await bot.send_message(chat_id, "\n".join(lines), reply_markup=_done_kb(pack))


async def _do_replace(cq: CallbackQuery, bot: Bot, app: App, me: User, admin: bool, pack: Pack,
                      data: dict) -> None:
    chat_id = cq.message.chat.id
    board = data.get("board")
    s = _settings(data)
    sd = s.to_dict()
    e = await app.db.get_emoji(int(data["replace"]))
    if not e:
        await bot.send_message(chat_id, "Эмодзи не найден — возможно, его уже удалили.")
        return
    q = await app.quota(me.id, admin)
    if q.left == 0:
        await set_caption(bot, chat_id, board, "⚠️ Не получилось")
        await bot.send_message(chat_id, app.quota_exhausted_text(q), reply_markup=_done_kb(pack))
        return
    await set_caption(bot, chat_id, board, f"⏳ Перерисовываю «{esc(e.label)}»…")
    try:
        async with app.stickers.lock(pack.id):
            await app.stickers.sync(pack)
            e = await app.db.get_emoji(e.id)
            if not e:
                raise StickerError("картинка пропала из набора")
            fmt, blob, thumb = await app.worker.run(job_emoji, app.store.load_bytes(e.skin_sha1), e.slim, sd,
                                                    pack.k.size)
            st = await app.stickers.replace(pack, e, fmt, blob, s.emoji, [e.label])
            await app.db.update_emoji(e.id, me.id, settings=sd, animated=fmt == "video", emoji=s.emoji,
                                      file_id=st.file_id, file_unique_id=st.file_unique_id,
                                      custom_emoji_id=st.custom_emoji_id)
            (app.cfg.thumbs_dir / f"{e.id}.png").write_bytes(thumb)
            await app.db.add_usage(me.id, pack.id, "replace")
            await app.db.touch_pack(pack.id, me.id)
            await app.db.log(me.id, "emoji_replace", pack, details=f"{e.label}: {s.summary_ru()}")
    except Exception as exc:  # noqa: BLE001
        log.warning("replace failed: %s", exc)
        if board:
            await set_caption(bot, chat_id, board, "⚠️ Не получилось")
        await bot.send_message(chat_id, f"😔 Не удалось перерисовать: {esc(why(exc))}", reply_markup=_done_kb(pack))
        return
    if board:
        await set_caption(bot, chat_id, board, "✅ Готово")
    await bot.send_message(chat_id, f"✅ Эмодзи «{esc(e.label)}» перерисован.", reply_markup=_done_kb(pack))



@router.callback_query(Wiz.filter())
async def cb_stale(cq: CallbackQuery):
    await cq.answer("Эта настройка устарела (бот перезапускался?). Откройте пак и начните добавление заново.",
                    show_alert=True)
