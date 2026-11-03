import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.types import BotCommand, MenuButtonWebApp, WebAppInfo
from aiohttp import web

from .bot import router
from .config import load_config
from .db import DB
from .service import PackService
from .web import build_app


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config()

    db = DB(cfg.db_path)
    await db.connect()

    bot = Bot(cfg.bot_token, default=DefaultBotProperties(parse_mode="HTML", link_preview_is_disabled=True))
    me = await bot.get_me()
    svc = PackService(bot, db, me.username, cfg.default_emoji)

    dp = Dispatcher()
    dp.include_router(router)
    dp["db"] = db
    dp["svc"] = svc
    dp["cfg"] = cfg

    runner = web.AppRunner(build_app(bot, db, svc, cfg.bot_token))
    await runner.setup()
    await web.TCPSite(runner, cfg.host, cfg.port).start()
    logging.info("web on %s:%s", cfg.host, cfg.port)

    await bot.set_my_commands([
        BotCommand(command="new", description="Новый набор"),
        BotCommand(command="packs", description="Мои наборы / куда добавлять"),
        BotCommand(command="stop", description="Перестать добавлять"),
        BotCommand(command="copy", description="Скопировать набор (ответом на стикер)"),
        BotCommand(command="app", description="Редактор наборов"),
        BotCommand(command="help", description="Помощь"),
    ])
    if cfg.webapp_url:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="Наборы", web_app=WebAppInfo(url=cfg.webapp_url))
        )

    try:
        await dp.start_polling(bot)
    finally:
        await runner.cleanup()
        await db.close()
        await bot.session.close()


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
