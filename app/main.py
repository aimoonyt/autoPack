import asyncio
import contextlib
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.types import BotCommand, BotCommandScopeChat, MenuButtonWebApp, WebAppInfo
from aiohttp import web

from .bot import TouchUser, router
from .broadcast import Broadcaster
from .config import load_config
from .db import DB
from .icons import CustomEmojiFallback, Icons
from .service import PackService
from .web import build_app

COMMANDS = [
    BotCommand(command="start", description="Главное меню"),
    BotCommand(command="new", description="Новый набор"),
    BotCommand(command="packs", description="Мои наборы / куда добавлять"),
    BotCommand(command="stop", description="Перестать добавлять"),
    BotCommand(command="copy", description="Скопировать набор (ответом на стикер)"),
    BotCommand(command="app", description="Редактор наборов"),
    BotCommand(command="help", description="Помощь"),
]


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config()

    db = DB(cfg.db_path)
    await db.connect()

    bot = Bot(cfg.bot_token, default=DefaultBotProperties(parse_mode="HTML", link_preview_is_disabled=True))
    bot.session.middleware(CustomEmojiFallback())
    me = await bot.get_me()
    svc = PackService(bot, db, me.username, cfg.default_emoji, cfg.admin_ids)
    icons = Icons(bot, db)
    await icons.refresh()
    app_icons = Icons(bot, db, "app_icon_pack", "app_icon_map", fill_unmatched=False)
    caster = Broadcaster(bot, db, cfg.timezone)

    dp = Dispatcher()
    dp.update.outer_middleware(TouchUser(db))
    dp.include_router(router)
    dp["db"] = db
    dp["svc"] = svc
    dp["cfg"] = cfg
    dp["icons"] = icons

    runner = web.AppRunner(build_app(bot, db, svc, icons, caster, cfg.bot_token, cfg, app_icons))
    await runner.setup()
    await web.TCPSite(runner, cfg.host, cfg.port).start()
    logging.info("web on %s:%s, admins: %s", cfg.host, cfg.port, sorted(cfg.admin_ids))

    await bot.set_my_commands(COMMANDS)
    for admin_id in cfg.admin_ids:
        with contextlib.suppress(Exception):  # админ мог ещё не нажать /start
            await bot.set_my_commands(
                COMMANDS + [BotCommand(command="admin", description="Админка")],
                scope=BotCommandScopeChat(chat_id=admin_id),
            )
    if cfg.webapp_url:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="Наборы", web_app=WebAppInfo(url=cfg.webapp_url))
        )

    broadcast_task = asyncio.create_task(caster.loop())
    try:
        await dp.start_polling(bot)
    finally:
        broadcast_task.cancel()
        await runner.cleanup()
        await db.close()
        await bot.session.close()


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
