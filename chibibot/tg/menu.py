"""Main menu, help, "my packs" and creating a pack."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from ..db import User
from ..kinds import EMOJI, KINDS, STICKERS, kind_of
from .app import App
from .ui import AdminCB, Menu, PackCB, btn, esc, kb, pack_label, safe_edit_text

router = Router(name="menu")


class NewPack(StatesGroup):
    title = State()


HELP = (
    "🧸 <b>Chibi Emoji</b> превращает скины Minecraft в эмодзи-паки и стикер-паки для Telegram.\n\n"
    "<b>Два вида паков</b>\n"
    "😀 <b>Эмодзи-пак</b> — маленькие эмодзи прямо в тексте; отправлять их могут пользователи Telegram Premium.\n"
    "🖼 <b>Стикер-пак</b> — обычные стикеры 512×512, работают у всех без Premium.\n"
    "Всё остальное одинаково: стили, позы, анимации, камеры, части тела.\n\n"
    "<b>Как это работает</b>\n"
    "1. Создайте пак (➕ Новый пак, выберите вид) или откройте существующий.\n"
    "2. Нажмите «➕ Добавить» и пришлите:\n"
    "   • ники Minecraft Java — через запятую, <code>;</code> или с новой строки;\n"
    "   • PNG-скины <b>файлом</b> (без сжатия) — 64×64, 64×32 или HD;\n"
    "   • ZIP-архив со скинами (можно положить и .txt со списком ников).\n"
    "3. Выберите стиль (Классика / Minecraft Live) и тип тела — на каждом шаге есть картинка-пример с вашим скином.\n"
    "4. Нажмите «✅ Создать» сразу или настройте дальше: позу, кадр, анимацию, камеру, видимость частей, обводку.\n"
    "5. Готово — бот пришлёт ссылку на пак, его можно добавить в Telegram.\n\n"
    "Без анимации получается статичная картинка, с анимацией — видео (до 3 секунд).\n"
    "Каждый скин из очереди становится отдельным эмодзи или стикером с одинаковыми настройками.\n\n"
    "<b>Лимиты</b>: число непустых паков (эмодзи и стикеры вместе) и число эмодзи/стикеров за последние 24 часа "
    "(перерисовка тоже считается, удаление квоту не возвращает). Остаток видно в меню.\n\n"
    "Команды: /menu — меню, /packs — мои паки, /new — новый пак, /cancel — отменить действие, /id — ваш ID."
)


async def menu_view(app: App, me: User, admin: bool) -> tuple[str, object]:
    q = await app.quota(me.id, admin)
    text = (
        "🧸 <b>Chibi Emoji</b>\n"
        "Эмодзи-паки и стикер-паки из скинов Minecraft: статичные и анимированные.\n\n"
        + app.limits_text(q)
    )
    if admin:
        st = await app.db.stats()
        text += (f"\n\n👑 Вы администратор. Всего паков: <b>{st['packs']}</b>, эмодзи и стикеров: "
                 f"<b>{st['emojis']}</b>, пользователей: <b>{st['users']}</b>.")
    packs = await app.db.user_packs(me.id)
    rows = [[btn(pack_label(p), PackCB(act="open", pid=p.id))] for p in packs[:5]]
    if packs:
        text += "\n\nВыберите пак или просто пришлите сюда ники, PNG-скины или ZIP — я спрошу, куда добавить."
    else:
        text += "\n\nНачните с «➕ Новый пак»."
    rows.append([btn("📦 Мои паки", Menu(act="packs")), btn("➕ Новый пак", Menu(act="new"))])
    if admin:
        rows.append([btn("👑 Все паки", AdminCB(act="packs")), btn("📜 Журнал", AdminCB(act="log"))])
        rows.append([btn("📊 Статистика", AdminCB(act="st", arg=1)), btn("🔥 Свежие паки", AdminCB(act="fresh", arg=1))])
        rows.append([btn("👥 Пользователи", AdminCB(act="users"))])
    rows.append([btn("❓ Как это работает", Menu(act="help"))])
    return text, kb(*rows)


@router.message(CommandStart())
@router.message(Command("menu"))
async def cmd_start(message: Message, state: FSMContext, app: App, me: User, admin: bool):
    await state.clear()
    text, markup = await menu_view(app, me, admin)
    await message.answer(text, reply_markup=markup)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext, app: App, me: User, admin: bool):
    await state.clear()
    text, markup = await menu_view(app, me, admin)
    await message.answer("Отменено.\n\n" + text, reply_markup=markup)


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(HELP, reply_markup=kb(btn("🏠 Меню", Menu(act="home"))))


@router.message(Command("id"))
async def cmd_id(message: Message, me: User, admin: bool):
    await message.answer(f"Ваш Telegram ID: <code>{me.id}</code>" + (" (администратор)" if admin else ""))


@router.message(Command("claim"))
async def cmd_claim(message: Message, command: CommandObject, app: App, me: User):
    code = (command.args or "").strip()
    if not app.claim_code or not code or code != app.claim_code:
        await message.answer("Неверный или устаревший код.")
        return
    await app.db.set_admin(me.id, True)
    await app.db.log(me.id, "admin_claim", details=f"id {me.id}")
    app.claim_code = None
    await message.answer("👑 Готово, теперь вы администратор. Откройте /menu.")


@router.callback_query(Menu.filter(F.act == "home"))
async def cb_home(cq: CallbackQuery, state: FSMContext, app: App, me: User, admin: bool):
    await state.clear()
    text, markup = await menu_view(app, me, admin)
    await safe_edit_text(cq.message, text, markup)
    await cq.answer()


@router.callback_query(Menu.filter(F.act == "help"))
async def cb_help(cq: CallbackQuery):
    await safe_edit_text(cq.message, HELP, kb(btn("🏠 Меню", Menu(act="home"))))
    await cq.answer()


# ------------------------------------------------------------------ my packs

async def packs_view(app: App, me: User, admin: bool) -> tuple[str, object]:
    packs = await app.db.user_packs(me.id)
    q = await app.quota(me.id, admin)
    if packs:
        lines = ["📦 <b>Ваши паки</b>", app.limits_text(q), ""]
        for p in packs:
            state = "🟢" if p.tg_created else "⚪️"
            lines.append(f"{state}{p.k.icon} <b>{esc(p.title)}</b> — {p.k.count(p.count)}")
        lines.append("\n😀 — эмодзи-пак, 🖼 — стикер-пак. ⚪️ — пак ещё пустой и появится в Telegram "
                     "после первого эмодзи или стикера.")
        text = "\n".join(lines)
    else:
        text = "У вас пока нет паков. Создайте первый — это пара кликов!"
    rows = [[btn(pack_label(p), PackCB(act="open", pid=p.id))] for p in packs]
    if await app.why_no_new_pack(me.id, admin) is None:
        rows.append([btn("➕ Новый пак", Menu(act="new"))])
    rows.append([btn("🏠 Меню", Menu(act="home"))])
    return text, kb(*rows)


@router.message(Command("packs"))
async def cmd_packs(message: Message, state: FSMContext, app: App, me: User, admin: bool):
    await state.clear()
    text, markup = await packs_view(app, me, admin)
    await message.answer(text, reply_markup=markup)


@router.callback_query(Menu.filter(F.act == "packs"))
async def cb_packs(cq: CallbackQuery, state: FSMContext, app: App, me: User, admin: bool):
    await state.clear()
    text, markup = await packs_view(app, me, admin)
    await safe_edit_text(cq.message, text, markup)
    await cq.answer()


# ------------------------------------------------------------------ new pack

async def title_hint(app: App) -> str:
    """How long a title may be and what gets appended to it in Telegram."""
    limit = await app.stickers.max_title_len()
    sfx = await app.stickers.suffix()
    hint = f"до {limit} символов"
    if sfx:
        hint += f"; в конце автоматически добавится {esc(sfx)}"
    return hint


async def clean_title(app: App, text: str) -> str:
    """The user's own part of a title: suffix removed if they typed it, cut to the allowed length."""
    title = " ".join((text or "").split())
    sfx = await app.stickers.suffix()
    if sfx and title.lower().endswith(sfx.lower()):
        title = title[: -len(sfx)].rstrip()
    return title[: await app.stickers.max_title_len()].rstrip()


KIND_TEXT = ("Какой пак создаём?\n\n"
             f"{KINDS[EMOJI].icon} <b>Эмодзи-пак</b> — {KINDS[EMOJI].about}.\n\n"
             f"{KINDS[STICKERS].icon} <b>Стикер-пак</b> — {KINDS[STICKERS].about}.\n\n"
             "Стили, позы, анимации и все остальные настройки у обоих одинаковые.")


def _kind_kb():
    return kb([btn("😀 Эмодзи-пак", Menu(act="new_emoji")), btn("🖼 Стикер-пак", Menu(act="new_stickers"))],
              btn("❌ Отмена", Menu(act="home")))


async def _ask_title(msg: Message, state: FSMContext, app: App, me: User, edit: bool) -> None:
    await state.set_state(NewPack.title)
    default = await clean_title(app, f"Chibi {me.first_name or me.username or ''}")
    await state.update_data(default_title=default)
    k = kind_of((await state.get_data()).get("kind"))
    text = (f"✏️ <b>Как назовём {k.name}?</b>\n"
            f"Пришлите название ({await title_hint(app)}). Его увидят все, кто добавит пак.\n\n"
            f"Или оставьте «{esc(default)}» — в Telegram будет «{esc(await app.stickers.tg_title(default))}».")
    markup = kb(btn(f"👌 «{default}»", Menu(act="deftitle")), btn("❌ Отмена", Menu(act="home")))
    if edit:
        await safe_edit_text(msg, text, markup)
    else:
        await msg.answer(text, reply_markup=markup)


@router.message(Command("new"))
async def cmd_new(message: Message, state: FSMContext, app: App, me: User, admin: bool):
    reason = await app.why_no_new_pack(me.id, admin)
    if reason:
        await message.answer(reason, reply_markup=kb(btn("📦 Мои паки", Menu(act="packs"))))
        return
    await message.answer(KIND_TEXT, reply_markup=_kind_kb())


@router.callback_query(Menu.filter(F.act == "new"))
async def cb_new(cq: CallbackQuery, state: FSMContext, app: App, me: User, admin: bool):
    reason = await app.why_no_new_pack(me.id, admin)
    if reason:
        await cq.answer(reason, show_alert=True)
        return
    await safe_edit_text(cq.message, KIND_TEXT, _kind_kb())
    await cq.answer()


@router.callback_query(Menu.filter(F.act.in_({"new_emoji", "new_stickers"})))
async def cb_new_kind(cq: CallbackQuery, callback_data: Menu, state: FSMContext, app: App, me: User, admin: bool):
    reason = await app.why_no_new_pack(me.id, admin)
    if reason:
        await cq.answer(reason, show_alert=True)
        return
    await state.update_data(kind=STICKERS if callback_data.act == "new_stickers" else EMOJI)
    await _ask_title(cq.message, state, app, me, edit=True)
    await cq.answer()


async def _create(message: Message, state: FSMContext, app: App, me: User, admin: bool, title: str):
    from .wizard import start_collect  # local import: wizard imports this module's helpers

    reason = await app.why_no_new_pack(me.id, admin)
    if reason:
        await state.clear()
        await message.answer(reason)
        return
    data = await state.get_data()
    pending = data.get("pending")  # skins sent before the pack existed
    k = kind_of(data.get("kind"))
    title = await clean_title(app, title) or "Chibi"
    name = await app.stickers.new_name()
    pack = await app.db.create_pack(me.id, name, title, me.id, me.settings, kind=k.key)
    await app.db.log(me.id, "pack_create", pack, details=f"{title} ({k.name})")
    await state.clear()
    intro = (f"🎉 {k.icon} {k.name.capitalize()} «{esc(title)}» создан! "
             f"В Telegram: «{esc(await app.stickers.tg_title(title))}».")
    await start_collect(message, state, app, me, admin, pack, intro=intro, pending=pending)


@router.callback_query(NewPack.title, Menu.filter(F.act == "deftitle"))
async def cb_default_title(cq: CallbackQuery, state: FSMContext, app: App, me: User, admin: bool):
    data = await state.get_data()
    await cq.answer()
    await _create(cq.message, state, app, me, admin, data.get("default_title") or "Chibi")


@router.message(NewPack.title, F.text & ~F.text.startswith("/"))
async def msg_title(message: Message, state: FSMContext, app: App, me: User, admin: bool):
    title = await clean_title(app, message.text)
    if not title:
        await message.answer("Название не может быть пустым.")
        return
    await _create(message, state, app, me, admin, title)
