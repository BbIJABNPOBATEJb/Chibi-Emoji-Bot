"""Bot startup: services, dispatcher, polling.  Started by bot.py in the project root."""
from __future__ import annotations

import asyncio
import logging
import secrets
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, ErrorEvent

from .config import load_config
from .db import Database
from .jobs import Worker
from .render.encode import ffmpeg_path
from .sources import MojangClient, SkinStore
from .stickers import StickerService
from .tg import admin, convert, fallback, menu, packs, wizard
from .tg.app import App
from .tg.middleware import UserMiddleware

log = logging.getLogger("chibibot")

COMMANDS = [
    BotCommand(command="menu", description="Главное меню"),
    BotCommand(command="packs", description="Мои паки"),
    BotCommand(command="new", description="Новый пак"),
    BotCommand(command="help", description="Как это работает"),
    BotCommand(command="cancel", description="Отменить текущее действие"),
    BotCommand(command="id", description="Мой Telegram ID"),
]


async def on_error(event: ErrorEvent) -> bool:
    log.exception("update failed: %s", event.exception, exc_info=event.exception)
    upd = event.update
    try:
        if upd.callback_query:
            await upd.callback_query.answer("⚠️ Что-то пошло не так, попробуйте ещё раз.", show_alert=True)
        elif upd.message:
            await upd.message.answer("⚠️ Что-то пошло не так. Попробуйте ещё раз или откройте /menu.")
    except TelegramBadRequest:
        pass
    return True


async def _retitle(app: App) -> None:
    """Give packs created before the title suffix (or before a bot rename) the current one."""
    try:
        n = await app.stickers.fix_all_titles()
        if n:
            log.info("Обновлены названия паков: %s", n)
    except Exception as exc:  # noqa: BLE001 - cosmetic, never worth crashing the bot
        log.warning("Не удалось обновить названия паков: %r", exc)


def build_dispatcher(app: App) -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp["app"] = app
    mw = UserMiddleware(app)
    dp.message.outer_middleware(mw)
    dp.callback_query.outer_middleware(mw)
    dp.include_routers(menu.router, admin.router, packs.router, convert.router, wizard.router, fallback.router)
    dp.errors.register(on_error)
    return dp


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    cfg = load_config()
    if not ffmpeg_path():
        log.warning("ffmpeg не найден: анимированные эмодзи работать не будут (pip install imageio-ffmpeg)")

    db = Database(cfg.db_path)
    await db.open()
    bot = Bot(cfg.token, default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True))
    worker = Worker(cfg.render_workers)
    await asyncio.to_thread(worker.warm_up)
    mojang = MojangClient()
    app = App(cfg, db, worker, StickerService(bot, db, cfg.title_suffix), mojang, SkinStore(cfg.skins_dir))

    me = await bot.get_me()
    if not cfg.admin_ids and not await db.admin_ids():
        app.claim_code = secrets.token_hex(4)
        log.warning("Администраторов пока нет. Чтобы стать админом, отправьте боту @%s команду:  /claim %s",
                    me.username, app.claim_code)

    dp = build_dispatcher(app)
    await bot.set_my_commands(COMMANDS)
    retitle = asyncio.create_task(_retitle(app))
    log.info("Бот @%s запущен (воркеров рендера: %s)", me.username, cfg.render_workers)
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        retitle.cancel()
        await mojang.close()
        await db.close()
        worker.shutdown()
        await bot.session.close()


def run() -> None:
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
