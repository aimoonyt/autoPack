"""Оформление кастомными эмодзи из набора (по умолчанию t.me/addemoji/tgmacicons).

Слот = место в интерфейсе (иконка «удалить», «звезда» и т.д.). Для каждого слота
сначала берём то, что админ выбрал руками, иначе ищем в наборе эмодзи с подходящим
обычным эмодзи-эквивалентом, иначе показываем обычный эмодзи.
"""
import html
import logging
import re
import time

from aiogram import Bot
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import TelegramMethod
from aiogram.types import InlineKeyboardMarkup

from .db import DB
from .service import sticker_format, tg

log = logging.getLogger(__name__)

# slot: (подпись в админке, кандидаты; первый — запасной обычный эмодзи)
SLOTS: dict[str, tuple[str, list[str]]] = {
    "pack": ("Набор", ["📦", "🗂", "📁", "📂"]),
    "add": ("Добавить", ["➕", "🆕", "✳️"]),
    "inbox": ("Входящие", ["📥", "📨", "📬", "📪"]),
    "upload": ("Загрузить", ["📤", "⬆️", "☁️"]),
    "edit": ("Редактировать", ["✏️", "📝", "🖊"]),
    "trash": ("Удалить", ["🗑", "❌"]),
    "star": ("Звёзды", ["⭐", "🌟", "✨"]),
    "lock": ("Закрыто", ["🔒", "🔐"]),
    "unlock": ("Открыто", ["🔓", "🔑"]),
    "link": ("Ссылка", ["🔗", "🌐"]),
    "check": ("Готово", ["✅", "✔️", "👍"]),
    "stop": ("Стоп", ["⏹", "🛑", "⛔"]),
    "copy": ("Копировать", ["📋", "📄"]),
    "settings": ("Настройки", ["⚙️", "🛠", "🔧"]),
    "chart": ("Статистика", ["📊", "📈"]),
    "megaphone": ("Рассылка", ["📣", "📢", "✉️"]),
    "user": ("Пользователи", ["👤", "👥"]),
    "money": ("Деньги", ["💰", "💵", "💸"]),
    "clock": ("Время", ["⏰", "🕒", "⏱"]),
    "eyes": ("Смотреть", ["👀", "🔍"]),
    "warning": ("Внимание", ["⚠️", "❗"]),
    "image": ("Картинка", ["🖼", "📷"]),
    "palette": ("Оформление", ["🎨", "🖌"]),
    "sparkle": ("Приветствие", ["👋", "✨", "🎉"]),
}

TTL = 3600


def _norm(e: str) -> str:
    return (e or "").replace("️", "")


class Icons:
    def __init__(self, bot: Bot, db: DB):
        self.bot = bot
        self.db = db
        self.pack_name: str | None = None
        self.items: list[dict] = []          # все эмодзи набора
        self.by_id: dict[str, dict] = {}
        self.resolved: dict[str, dict] = {}  # slot -> item
        self.custom_in_bot = True
        self._fetched = 0.0

    async def refresh(self, force: bool = False) -> None:
        settings = await self.db.get_settings()
        self.custom_in_bot = bool(settings.get("bot_custom_emoji", True))
        name = (settings.get("icon_pack") or "").strip()
        if force or name != self.pack_name or time.time() - self._fetched > TTL:
            self.pack_name = name
            self.items = []
            if name:
                try:
                    ss = await tg(self.bot.get_sticker_set, name=name)
                    self.items = [
                        {
                            "id": s.custom_emoji_id,
                            "emoji": s.emoji or "",
                            "format": sticker_format(s),
                            "file_id": s.file_id,
                            "thumb_id": s.thumbnail.file_id if s.thumbnail else None,
                        }
                        for s in ss.stickers if s.custom_emoji_id
                    ]
                except TelegramBadRequest as e:
                    log.warning("icon pack %s: %s", name, e.message)
            self._fetched = time.time()
            self.by_id = {i["id"]: i for i in self.items}
        overrides = settings.get("icon_map") or {}
        self.resolved = {}
        for slot, (_, candidates) in SLOTS.items():
            item = self.by_id.get(overrides.get(slot) or "")
            if item is None and overrides.get(slot) != "none":
                wanted = [_norm(c) for c in candidates]
                for c in wanted:
                    item = next((i for i in self.items if _norm(i["emoji"]) == c), None)
                    if item:
                        break
            if item:
                self.resolved[slot] = item

    def fallback(self, slot: str) -> str:
        return SLOTS[slot][1][0]

    def e(self, slot: str) -> str:
        """HTML для сообщения бота: премиум-эмодзи или обычный."""
        item = self.resolved.get(slot)
        if not item or not self.custom_in_bot:
            return self.fallback(slot)
        fb = html.escape(item["emoji"] or self.fallback(slot))
        return f'<tg-emoji emoji-id="{item["id"]}">{fb}</tg-emoji>'

    def button_id(self, slot: str) -> str | None:
        item = self.resolved.get(slot)
        return item["id"] if item and self.custom_in_bot else None


_TG_EMOJI = re.compile(r"<tg-emoji[^>]*>(.*?)</tg-emoji>", re.S)


def _strip_markup(markup):
    if not isinstance(markup, InlineKeyboardMarkup):
        return markup
    return InlineKeyboardMarkup(inline_keyboard=[
        [b.model_copy(update={"icon_custom_emoji_id": None}) for b in row]
        for row in markup.inline_keyboard
    ])


class CustomEmojiFallback(BaseRequestMiddleware):
    """Если Telegram не дал боту премиум-эмодзи — повторяем запрос с обычными."""

    async def __call__(self, make_request, bot: Bot, method: TelegramMethod):
        try:
            return await make_request(bot, method)
        except TelegramBadRequest:
            update = {}
            for field in ("text", "caption"):
                value = getattr(method, field, None)
                if isinstance(value, str) and "<tg-emoji" in value:
                    update[field] = _TG_EMOJI.sub(r"\1", value)
            markup = getattr(method, "reply_markup", None)
            if isinstance(markup, InlineKeyboardMarkup) and any(
                b.icon_custom_emoji_id for row in markup.inline_keyboard for b in row
            ):
                update["reply_markup"] = _strip_markup(markup)
            if not update:
                raise
            log.info("retrying %s without custom emoji", type(method).__name__)
            return await make_request(bot, method.model_copy(update=update))
