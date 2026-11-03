"""Еженедельная рассылка поста, который админ задаёт в мини-апке."""
import asyncio
import logging
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .db import DB
from .service import tg

log = logging.getLogger(__name__)

WEEKDAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
SEND_DELAY = 0.05  # ~20 сообщений в секунду, лимит Telegram — 30


def post_markup(post: dict) -> InlineKeyboardMarkup | None:
    if post.get("button_text") and post.get("button_url"):
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=post["button_text"], url=post["button_url"])
        ]])
    return None


class Broadcaster:
    def __init__(self, bot: Bot, db: DB, timezone: str):
        self.bot = bot
        self.db = db
        self.tz = ZoneInfo(timezone)
        self.running = False
        self.progress: dict | None = None

    async def send_post(self, chat_id: int, post: dict) -> None:
        markup = post_markup(post)
        if post.get("photo_file_id"):
            await tg(
                self.bot.send_photo, chat_id=chat_id, photo=post["photo_file_id"],
                caption=post.get("text") or None, reply_markup=markup,
            )
        else:
            await tg(
                self.bot.send_message, chat_id=chat_id, text=post["text"], reply_markup=markup,
            )

    @staticmethod
    def validate(post: dict) -> None:
        text = post.get("text") or ""
        if not text.strip() and not post.get("photo_file_id"):
            raise ValueError("Пост пустой: нужен текст или картинка")
        if post.get("photo_file_id") and len(text) > 1024:
            raise ValueError("С картинкой текст максимум 1024 символа")
        if len(text) > 4096:
            raise ValueError("Текст максимум 4096 символов")
        if bool(post.get("button_text")) != bool(post.get("button_url")):
            raise ValueError("У кнопки нужны и текст, и ссылка (или ни того, ни другого)")
        if post.get("button_url") and not post["button_url"].startswith(("https://", "http://", "tg://")):
            raise ValueError("Ссылка кнопки должна начинаться с https://")

    async def run(self) -> dict:
        if self.running:
            raise ValueError("Рассылка уже идёт")
        settings = await self.db.get_settings()
        post = settings["broadcast"]
        self.validate(post)
        self.running = True
        started = time.time()
        post["last_sent_at"] = int(started)
        await self.db.set_setting("broadcast", post)
        ok = fail = blocked = 0
        try:
            users = await self.db.broadcast_user_ids()
            self.progress = {"total": len(users), "done": 0}
            for uid in users:
                try:
                    await self.send_post(uid, post)
                    ok += 1
                except TelegramForbiddenError:
                    await self.db.set_blocked(uid)
                    blocked += 1
                except TelegramBadRequest as e:
                    if "chat not found" in (e.message or "").lower():
                        await self.db.set_blocked(uid)
                        blocked += 1
                    else:
                        log.warning("broadcast to %s: %s", uid, e.message)
                        fail += 1
                except Exception:  # noqa: BLE001
                    log.exception("broadcast to %s", uid)
                    fail += 1
                self.progress["done"] += 1
                await asyncio.sleep(SEND_DELAY)
        finally:
            self.running = False
            self.progress = None
        result = {"at": int(started), "ok": ok, "blocked": blocked, "failed": fail,
                  "seconds": int(time.time() - started)}
        settings = await self.db.get_settings()
        settings["broadcast"]["last_result"] = result
        await self.db.set_setting("broadcast", settings["broadcast"])
        log.info("broadcast done: %s", result)
        return result

    def next_run(self, post: dict) -> datetime | None:
        if not post.get("enabled"):
            return None
        now = datetime.now(self.tz)
        base = now.replace(hour=post["hour"], minute=0, second=0, microsecond=0)
        for d in range(8):
            c = base + timedelta(days=d)
            if c.weekday() != post["weekday"]:
                continue
            if c > now:
                return c
            if c + timedelta(hours=1) > now and not self._sent_recently(post):
                return c  # должна уйти прямо сейчас
        return None

    @staticmethod
    def _sent_recently(post: dict) -> bool:
        return bool(post.get("last_sent_at")) and time.time() - post["last_sent_at"] < 6 * 86400

    def _due(self, post: dict, now: datetime) -> bool:
        return (
            post.get("enabled")
            and now.weekday() == post["weekday"]
            and now.hour == post["hour"]
            and not self._sent_recently(post)
        )

    async def loop(self) -> None:
        while True:
            await asyncio.sleep(30)
            try:
                post = (await self.db.get_settings())["broadcast"]
                if not self.running and self._due(post, datetime.now(self.tz)):
                    log.info("weekly broadcast started")
                    await self.run()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("broadcast loop")
