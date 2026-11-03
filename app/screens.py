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

DEFAULT_TEXTS = {
    # стиль Absolute Cinema: заголовок, строки «иконка + поле: значение»
    "menu": (
        "{big:XMAPKET}\n\n"
        "{ic:sparkle} Добро пожаловать, <b>{name}</b>\n"
        "{ic:user} ID: <code>{id}</code>\n"
        "{ic:pack} Наборов: <b>{packs}</b>\n"
        "{ic:unlock} Без подписи: <b>{paid}</b>\n"
        "{ic:inbox} Во входящих: <b>{inbox}</b>\n"
        "{ic:link} Переходник: @XMAPKET\n\n"
        "Выберите действие:"
    ),
    # стиль BLUEBLACK: заголовок буквами, блоки-цитаты с жирными строками
    "packer": (
        "{big:STICKERS}\n\n"
        "<blockquote><b>{ic:add} Жми «Новый набор» и придумай название\n"
        "{ic:upload} Кидай стикеры, фото, картинки — можно пачкой\n"
        "{ic:check} Готово: набор в Telegram, ссылка у тебя</b></blockquote>\n\n"
        "<blockquote>{ic:edit} Редактор: порядок, эмодзи, название, удаление\n"
        "{ic:star} Убрать подпись бота и сменить ссылку — <b>{price}</b> звёзд за набор</blockquote>"
    ),
    "chat": (
        "{big:SPAMBLOCK}\n\n"
        "<blockquote><b>{ic:chat} Пиши сюда, как мне в личку\n"
        "{ic:image} Текст, фото, голосовые, видео, стикеры\n"
        "{ic:check} Ответ придёт в этот чат</b></blockquote>\n\n"
        "{ic:back} Выйти — «Назад в меню»"
    ),
    "info": (
        "{big:WELLCOME}\n\n"
        "{ic:sparkle} Это мой переходник, тут есть вся необходимая информация\n\n"
        "{ic:emoji} Заказать эмодзи: https://t.me/m/F4b0iYCwZmYy\n"
        "{ic:video} Заказать видео: https://t.me/m/y-vh5NYEZmUy\n\n"
        "{ic:money} <b>ПРАЙС</b> {ic:money}\n"
        "<blockquote><b>Эмодзи шаблонные</b>\n"
        "1. <i>До 30 шт - 100р/1$/шт</i>\n"
        "2. <i>Более 30шт - 90р/0.9$/шт</i>\n"
        "<b>Эмодзи кастомные</b>\n"
        "1. На основе шаблона от 200р/2$\n"
        "2. Полностью кастомный от 300р/3$\n"
        "<b>Видео</b>\n"
        "1. <i>От 500р/6$</i></blockquote>\n\n"
        "{ic:pack} <b><i>Шаблоны</i></b> {ic:pack}\n"
        "<blockquote>{ic:emoji} Эмодзи: https://t.me/addemoji/XMAPKETemojis\n"
        "{ic:fire} Символы: https://t.me/addemoji/XMAPKETsymbols</blockquote>\n\n"
        "{ic:check} Правки <b>входят</b> в стоимость заказа {ic:check}\n\n"
        "{ic:user} <b>ЛИЧКА</b> {ic:user}\n"
        "Я - @XMAPKET {ic:sparkle}\n"
        "Менеджер(если я не отвечаю) - @XMAPKETmngr\n\n"
        "{ic:image} <b>РАБОТЫ</b> {ic:image}\n"
        "@XMAPKETwrk"
    ),
    "order": (
        "{big:ORDER}\n\n"
        "<blockquote><b>{ic:image} Баннер — шапки, обложки, превью\n"
        "{ic:emoji} Эмодзи — шаблонные и кастомные\n"
        "{ic:video} Видео — анимации и ролики</b></blockquote>\n\n"
        "{ic:down} Жми — откроется чат с готовым сообщением"
    ),
}

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
    photo = screen.get("photo_file_id")
    if photo and visible_length(screen.get("text") or DEFAULT_TEXTS[key]) > MAX_CAPTION:
        raise ValueError(f"С баннером текст максимум {MAX_CAPTION} символов — Telegram не даст больше в подписи к фото")
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


async def send(bot: Bot, chat_id: int, key: str, db: DB, icons: Icons, cfg: Config, name: str,
               user_id: int | None = None) -> Message:
    text, photo, markup = await render(key, db, icons, cfg, name, user_id or chat_id)
    if photo:
        return await bot.send_photo(chat_id, photo, caption=text, reply_markup=markup)
    return await bot.send_message(chat_id, text, reply_markup=markup)


async def navigate(message: Message, key: str, db: DB, icons: Icons, cfg: Config, name: str,
                   user_id: int | None = None) -> None:
    """Переход на экран в том же сообщении (если можно), иначе — новое сообщение."""
    text, photo, markup = await render(key, db, icons, cfg, name, user_id or message.chat.id)
    has_photo = bool(message.photo)
    try:
        if photo and has_photo:
            await message.edit_media(InputMediaPhoto(media=photo, caption=text, parse_mode="HTML"), reply_markup=markup)
            return
        if not photo and not has_photo and message.text is not None:
            await message.edit_text(text, reply_markup=markup)
            return
    except TelegramBadRequest as e:
        if "not modified" in (e.message or ""):
            return
        log.info("can't edit screen, resending: %s", e.message)
    try:
        await message.delete()
    except TelegramBadRequest:
        pass
    await send(message.bot, message.chat.id, key, db, icons, cfg, name, user_id)
