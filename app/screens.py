"""Экраны главного меню: тексты, баннеры, кнопки и переходы между ними.

Тексты — HTML Telegram с плейсхолдерами {ic:слот} (иконка из набора премиум-эмодзи),
{name} (имя юзера) и {price} (цена доступа). Админ может поменять текст и баннер
любого экрана в мини-апке или просто переслать боту готовый пост.
"""
import html
import logging
import re

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
    WebAppInfo,
)

from .config import Config
from .db import DB
from . import rich
from .icons import Icons

log = logging.getLogger(__name__)

SCREEN_LABELS = {
    "menu": "Главное меню",
    "packer": "Упаковщик",
    "chat": "Спамблок",
    "info": "Инфа",
    "order": "Заказ",
}

DEFAULT_ORDER_LINKS = {
    "banner": "https://t.me/m/3_xy7O69OThi",
    "emoji": "https://t.me/m/F4b0iYCwZmYy",
    "video": "https://t.me/m/y-vh5NYEZmUy",
}

# Разметка — см. app/rich.py: «# » заголовок в плашке, [[ ]] плашка по центру, [banner] место баннера.
DEFAULT_TEXTS = {
    # как Absolute Cinema: баннер, строки «иконка + поле: значение»
    "menu": (
        "[banner]\n"
        "Добро пожаловать в {xm} <b>XMAPKET</b>\n"
        "{ic:user} ID: <code>{id}</code>\n"
        "{ic:pack} Наборов: <b>{packs}</b>\n"
        "{ic:unlock} Без подписи: <b>{paid}</b>\n"
        "{ic:inbox} Во входящих: <b>{inbox}</b>\n"
        "{ic:link} Переходник: @XMAPKET\n"
        "Выберите действие:"
    ),
    # как BLUEBLACK: заголовки буквами в плашках, плашки с текстом по центру, баннер посередине
    "packer": (
        "# {big:STICKERS}\n"
        "[[\n"
        "<i>Соберу стикерпак из всего, что ты кинешь:</i>\n"
        "{ic:add} Жми «Новый набор» и придумай название\n"
        "{ic:upload} Кидай стикеры, фото, картинки — можно пачкой\n"
        "{ic:check} Готово — набор в Telegram, ссылка у тебя\n"
        "]]\n"
        "[banner]\n"
        "# {big:EDITOR}\n"
        "[[\n"
        "{ic:edit} Порядок, эмодзи, название, удаление\n"
        "{ic:star} Без подписи и своя ссылка — <b>{price}</b> звёзд за набор {xm}\n"
        "]]"
    ),
    "chat": (
        "# {big:SPAMBLOCK}\n"
        "[[\n"
        "<i>Не можешь написать мне в личку?</i>\n"
        "{ic:chat} Пиши сюда — как мне в лс\n"
        "{ic:image} Текст, фото, голосовые, видео, стикеры\n"
        "{ic:check} Ответ придёт в этот чат {xm}\n"
        "]]\n"
        "[banner]\n"
        "~ Выйти из чата — «Назад в меню»"
    ),
    "info": (
        "# {big:WELLCOME}\n"
        "[[\n"
        "Это мой переходник, тут есть вся необходимая информация {xm}\n"
        "{ic:emoji} <a href=\"https://t.me/m/F4b0iYCwZmYy\">Заказать эмодзи</a>\n"
        "{ic:video} <a href=\"https://t.me/m/y-vh5NYEZmUy\">Заказать видео</a>\n"
        "]]\n"
        "[banner]\n"
        "# {big:PRICE}\n"
        "[[\n"
        "<b>Эмодзи шаблонные</b>\n"
        "<i>До 30 шт - 100р/1$/шт</i>\n"
        "<i>Более 30шт - 90р/0.9$/шт</i>\n"
        "<b>Эмодзи кастомные</b>\n"
        "На основе шаблона от 200р/2$\n"
        "Полностью кастомный от 300р/3$\n"
        "<b>Видео</b>\n"
        "<i>От 500р/6$</i>\n"
        "]]\n"
        "# {big:TEMPLATES}\n"
        "[[\n"
        "{ic:emoji} <a href=\"https://t.me/addemoji/XMAPKETemojis\">Эмодзи</a>\n"
        "{ic:fire} <a href=\"https://t.me/addemoji/XMAPKETsymbols\">Символы</a>\n"
        "Правки <b>входят</b> в стоимость заказа {xm}\n"
        "]]\n"
        "# {big:CONTACTS}\n"
        "[[\n"
        "Я — @XMAPKET {xm}\n"
        "Менеджер (если я не отвечаю) — @XMAPKETmngr\n"
        "Работы — @XMAPKETwrk\n"
        "]]"
    ),
    "order": (
        "# {big:ORDER}\n"
        "[[\n"
        "{ic:image} <b>Баннер</b> — шапки, обложки, превью\n"
        "{ic:emoji} <b>Эмодзи</b> — шаблонные и кастомные\n"
        "{ic:video} <b>Видео</b> — анимации и ролики\n"
        "]]\n"
        "[banner]\n"
        "~ Жми кнопку — откроется чат с готовым сообщением"
    ),
}

# все буквы, которые нужны заголовкам по умолчанию
HEADING_LETTERS = "".join(sorted({ch for t in DEFAULT_TEXTS.values()
                                  for w in re.findall(r"\{big:([^}]*)\}", t) for ch in w.upper() if ch.isalnum()}))

MAX_CAPTION = 1024
_TAGS = re.compile(r"<[^>]+>")


def visible_length(text: str) -> int:
    """Сколько символов увидит Telegram (теги не считаются)."""
    text = re.sub(r"\{big:([^}]*)\}", lambda m: m.group(1), text)
    text = re.sub(r"\{(ic:[a-z_]+|name|id|packs|paid|inbox|price)\}", "XX", text)
    return len(html.unescape(_TAGS.sub("", text)))


async def get_screens(db: DB) -> dict[str, dict]:
    stored = (await db.get_settings()).get("screens") or {}
    return {
        key: {
            "text": (stored.get(key) or {}).get("text") or DEFAULT_TEXTS[key],
            "photo_file_id": (stored.get(key) or {}).get("photo_file_id"),
            "custom": bool((stored.get(key) or {}).get("text")),
        }
        for key in SCREEN_LABELS
    }


async def save_screen(db: DB, key: str, text: str | None = None, photo_file_id: str | None | bool = False) -> None:
    """text=None — не трогать; text="" — вернуть стандартный. photo_file_id=False — не трогать."""
    stored = (await db.get_settings()).get("screens") or {}
    screen = dict(stored.get(key) or {})
    if text is not None:
        screen["text"] = text
    if photo_file_id is not False:
        screen["photo_file_id"] = photo_file_id
    stored[key] = screen
    await db.set_setting("screens", stored)


async def get_order_links(db: DB) -> dict[str, str]:
    stored = (await db.get_settings()).get("order_links") or {}
    return {k: stored.get(k) or v for k, v in DEFAULT_ORDER_LINKS.items()}


def _btn(icons: Icons, slot: str, text: str, style: str | None = None, **kw) -> InlineKeyboardButton:
    icon = icons.button_id(slot)
    if not icon:
        text = f"{icons.fallback(slot)} {text}"
    return InlineKeyboardButton(text=text, icon_custom_emoji_id=icon, style=style, **kw)


async def keyboard(key: str, db: DB, icons: Icons, cfg: Config) -> InlineKeyboardMarkup:
    back = [_btn(icons, "back", "Назад в меню", callback_data="nav:menu")]
    if key == "menu":
        rows = [
            [_btn(icons, "pack", "Упаковщик стикер-паков", "primary", callback_data="nav:packer")],
            [_btn(icons, "chat", "Спамблок", callback_data="nav:chat"),
             _btn(icons, "info", "Инфа", callback_data="nav:info")],
            [_btn(icons, "order", "Заказ", "success", callback_data="nav:order")],
        ]
    elif key == "packer":
        rows = []
        if cfg.webapp_url:
            rows.append([_btn(icons, "edit", "Открыть редактор", "primary", web_app=WebAppInfo(url=cfg.webapp_url))])
        rows += [
            [_btn(icons, "add", "Новый набор", callback_data="pk:new"),
             _btn(icons, "pack", "Мои наборы", callback_data="pk:list")],
            back,
        ]
    elif key == "order":
        links = await get_order_links(db)
        rows = [
            [_btn(icons, "image", "Баннер", url=links["banner"])],
            [_btn(icons, "emoji", "Эмодзи", url=links["emoji"]),
             _btn(icons, "video", "Видео", url=links["video"])],
            back,
        ]
    else:  # chat, info
        rows = [back]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def render(key: str, db: DB, icons: Icons, cfg: Config, name: str,
                 user_id: int | None = None) -> tuple[str, str | None, InlineKeyboardMarkup]:
    screens = await get_screens(db)
    price = str((await db.get_settings())["price"])
    packs = await db.get_packs(user_id) if user_id else []
    inbox = await db.get_inbox(user_id) if user_id else []
    text = icons.render(
        screens[key]["text"], name=html.escape(name or "друг"), price=price,
        id=str(user_id or "—"), packs=str(len(packs)), paid=str(sum(1 for p in packs if p["paid"])),
        inbox=str(len(inbox)),
    )
    return text, screens[key]["photo_file_id"], await keyboard(key, db, icons, cfg)


RICH_ERRORS: dict[str, object] = {"at": 0.0, "error": None}  # для подсказки в админке


async def send(bot: Bot, chat_id: int, key: str, db: DB, icons: Icons, cfg: Config, name: str,
               user_id: int | None = None) -> Message:
    text, photo, markup = await render(key, db, icons, cfg, name, user_id or chat_id)
    try:
        return await bot.send_rich_message(chat_id, rich.build(text, photo), reply_markup=markup)
    except TelegramBadRequest as e:
        _rich_failed(e)
    return await _send_classic(bot, chat_id, text, photo, markup)


def _rich_failed(e: TelegramBadRequest) -> None:
    import time
    log.warning("rich message rejected, sending classic: %s", e.message)
    RICH_ERRORS.update(at=time.time(), error=e.message)


async def _send_classic(bot: Bot, chat_id: int, text: str, photo: str | None, markup) -> Message:
    classic = rich.to_classic(text)
    if photo and rich.plain_length(classic) <= MAX_CAPTION:
        return await bot.send_photo(chat_id, photo, caption=classic, reply_markup=markup)
    if photo:
        await bot.send_photo(chat_id, photo)
    return await bot.send_message(chat_id, classic, reply_markup=markup)


async def navigate(message: Message, key: str, db: DB, icons: Icons, cfg: Config, name: str,
                   user_id: int | None = None) -> None:
    """Переход на экран в том же сообщении (если можно), иначе — новое сообщение."""
    text, photo, markup = await render(key, db, icons, cfg, name, user_id or message.chat.id)
    if getattr(message, "rich_message", None) is not None:
        try:
            await message.bot.edit_message_text(
                chat_id=message.chat.id, message_id=message.message_id,
                rich_message=rich.build(text, photo), reply_markup=markup,
            )
            return
        except TelegramBadRequest as e:
            if "not modified" in (e.message or ""):
                return
            log.info("can't edit rich screen, resending: %s", e.message)
    try:
        await message.delete()
    except TelegramBadRequest:
        pass
    await send(message.bot, message.chat.id, key, db, icons, cfg, name, user_id)
