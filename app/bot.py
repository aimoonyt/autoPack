import html
import logging
import time

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputSticker,
    Message,
    ReactionTypeEmoji,
    WebAppInfo,
)

from .config import Config
from .db import DB
from .service import PackError, PackService, sticker_format

log = logging.getLogger(__name__)
router = Router()

HINT_COOLDOWN = 30  # сек, чтобы не спамить подсказкой при пачке стикеров
_last_hint: dict[int, float] = {}


class NewPack(StatesGroup):
    title = State()


def app_button(cfg: Config, text: str = "🧩 Редактор наборов") -> list[InlineKeyboardButton]:
    if not cfg.webapp_url:
        return []
    return [InlineKeyboardButton(text=text, web_app=WebAppInfo(url=cfg.webapp_url))]


def kb(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup | None:
    rows = [r for r in rows if r]
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


async def react(m: Message, emoji: str) -> None:
    try:
        await m.react([ReactionTypeEmoji(emoji=emoji)])
    except TelegramBadRequest:
        pass


HELP = (
    "Я собираю стикерпаки из того, что ты мне кидаешь.\n\n"
    "<b>Как пользоваться</b>\n"
    "1. /new Название — начать новый набор\n"
    "2. Кидай стикеры (можно пачкой пересылать), фотки или картинки файлом — "
    "всё полетит в набор. Эмодзи в подписи к картинке станет её эмодзи.\n"
    "3. /stop — перестать добавлять в набор\n\n"
    "/packs — мои наборы, выбрать куда добавлять\n"
    "/copy — ответом на стикер: скопировать весь его набор себе\n\n"
    "Если активного набора нет, стикеры падают во «Входящие» — "
    "оттуда их можно раскидать в редакторе. Там же: порядок, названия, эмодзи, удаление."
)


@router.message(CommandStart())
@router.message(Command("help"))
async def cmd_start(m: Message, cfg: Config) -> None:
    await m.answer(HELP, reply_markup=kb(app_button(cfg)))


@router.message(Command("app"))
async def cmd_app(m: Message, cfg: Config) -> None:
    if not cfg.webapp_url:
        await m.answer("WEBAPP_URL не настроен, редактор недоступен")
        return
    await m.answer("Открывай:", reply_markup=kb(app_button(cfg)))


@router.message(Command("new"))
async def cmd_new(m: Message, command: CommandObject, state: FSMContext, db: DB) -> None:
    title = (command.args or "").strip()
    if not title:
        await state.set_state(NewPack.title)
        await m.answer("Как назовём набор? (до 64 символов)")
        return
    await start_pending(m, db, title)


@router.message(NewPack.title, F.text & ~F.text.startswith("/"))
async def got_title(m: Message, state: FSMContext, db: DB) -> None:
    await state.clear()
    await start_pending(m, db, m.text.strip())


async def start_pending(m: Message, db: DB, title: str) -> None:
    if len(title) > 64:
        await m.answer("Слишком длинно, максимум 64 символа. Давай ещё раз: /new Название")
        return
    await db.set_active(m.from_user.id, None)
    await db.set_pending(m.from_user.id, title)
    await m.answer(
        f"Набор «{html.escape(title)}». Кидай первый стикер — с него я его и создам."
    )


@router.message(Command("stop", "done"))
async def cmd_stop(m: Message, state: FSMContext, db: DB) -> None:
    await state.clear()
    user = await db.get_user(m.from_user.id)
    await db.set_active(m.from_user.id, None)
    await db.set_pending(m.from_user.id, None)
    if user["active_pack"] or user["pending_title"]:
        await m.answer("Ок, больше никуда не добавляю. Новые стикеры пойдут во «Входящие».")
    else:
        await m.answer("Да я и так ничего не собирал.")


async def packs_markup(db: DB, svc: PackService, cfg: Config, user_id: int) -> InlineKeyboardMarkup:
    user = await db.get_user(user_id)
    rows = []
    for p in await db.get_packs(user_id):
        mark = "✅ " if p["id"] == user["active_pack"] else ""
        rows.append([
            InlineKeyboardButton(text=f"{mark}{p['title']}", callback_data=f"a:{p['id']}"),
            InlineKeyboardButton(text="↗️", url=svc.link(p["name"])),
        ])
    if user["active_pack"]:
        rows.append([InlineKeyboardButton(text="⏹ Никуда не добавлять", callback_data="a:0")])
    rows.append(app_button(cfg))
    return kb(*rows)


@router.message(Command("packs"))
async def cmd_packs(m: Message, db: DB, svc: PackService, cfg: Config) -> None:
    if not await db.get_packs(m.from_user.id):
        await m.answer("Наборов пока нет. Начни с /new Название", reply_markup=kb(app_button(cfg)))
        return
    await m.answer(
        "Твои наборы. Тыкни, чтобы новые стикеры летели туда:",
        reply_markup=await packs_markup(db, svc, cfg, m.from_user.id),
    )


@router.callback_query(F.data.startswith("a:"))
async def cb_activate(c: CallbackQuery, db: DB, svc: PackService, cfg: Config) -> None:
    pid = int(c.data[2:])
    uid = c.from_user.id
    if pid:
        pack = await db.get_pack_by_id(pid)
        if not pack or pack["owner_id"] != uid:
            await c.answer("Нет такого набора", show_alert=True)
            return
        await db.set_pending(uid, None)
        await db.set_active(uid, pid)
        await c.answer(f"Теперь добавляю в «{pack['title']}»")
    else:
        await db.set_active(uid, None)
        await c.answer("Больше никуда не добавляю")
    try:
        await c.message.edit_reply_markup(reply_markup=await packs_markup(db, svc, cfg, uid))
    except TelegramBadRequest:
        pass


async def do_copy(m: Message, db: DB, svc: PackService, user_id: int, set_name: str) -> None:
    status = await m.answer("Копирую набор, это может занять время…")
    try:
        async with svc.lock(user_id):
            pack = await svc.copy_set(user_id, set_name)
            await db.set_pending(user_id, None)
            await db.set_active(user_id, pack["id"])
    except PackError as e:
        await status.edit_text(f"Не вышло: {html.escape(str(e))}")
        return
    except TelegramBadRequest as e:
        await status.edit_text(f"Телега отказала: {html.escape(e.message)}")
        return
    await status.edit_text(
        f"Готово: <a href=\"{svc.link(pack['name'])}\">{html.escape(pack['title'])}</a>\n"
        "Он теперь активный — новые стикеры полетят туда."
    )


@router.message(Command("copy"))
async def cmd_copy(m: Message, db: DB, svc: PackService) -> None:
    src = m.reply_to_message
    if not src or not src.sticker or not src.sticker.set_name:
        await m.answer("Ответь этой командой на стикер из набора, который надо скопировать.")
        return
    await do_copy(m, db, svc, m.from_user.id, src.sticker.set_name)


@router.callback_query(F.data.startswith("cp:"))
async def cb_copy(c: CallbackQuery, db: DB, svc: PackService) -> None:
    item = await db.get_inbox_item(int(c.data[3:]))
    if not item or item["user_id"] != c.from_user.id or not item["set_name"]:
        await c.answer("Не помню, что это был за набор", show_alert=True)
        return
    await c.answer()
    await db.delete_inbox(c.from_user.id, [item["id"]])
    await do_copy(c.message, db, svc, c.from_user.id, item["set_name"])


# --- приём стикеров и картинок ---

async def ingest(
    m: Message, db: DB, svc: PackService, cfg: Config, *,
    file_id: str, file_unique_id: str, thumb: str | None, fmt: str,
    emoji: str | None, set_name: str | None = None,
) -> None:
    uid = m.from_user.id
    inp: InputSticker = svc.make_input(file_id, fmt, [emoji] if emoji else None)
    try:
        async with svc.lock(uid):
            user = await db.get_user(uid)
            if user["pending_title"]:
                pack = await svc.create(uid, user["pending_title"], [inp])
                await db.set_pending(uid, None)
                await db.set_active(uid, pack["id"])
                await m.reply(
                    f"Создал <a href=\"{svc.link(pack['name'])}\">{html.escape(pack['title'])}</a>. "
                    "Кидай ещё, всё полетит туда. /stop — закончить.",
                    reply_markup=kb(app_button(cfg)),
                )
                return
            pack = await db.get_pack_by_id(user["active_pack"]) if user["active_pack"] else None
            if pack:
                await svc.add(uid, pack["name"], [inp])
                await react(m, "👍")
                return
            item_id = await db.add_inbox(
                uid, file_id, file_unique_id, thumb, fmt, inp.emoji_list[0], set_name
            )
    except PackError as e:
        await m.reply(f"Не вышло: {html.escape(str(e))}")
        return
    except TelegramBadRequest as e:
        await m.reply(f"Телега отказала: {html.escape(e.message)}")
        return

    await react(m, "👀")
    now = time.monotonic()
    if now - _last_hint.get(uid, 0) < HINT_COOLDOWN:
        return
    _last_hint[uid] = now
    copy_row = (
        [InlineKeyboardButton(text="📋 Скопировать весь этот набор", callback_data=f"cp:{item_id}")]
        if set_name else []
    )
    await m.reply(
        "Активного набора нет, закинул во «Входящие».\n"
        "/new Название — новый набор, /packs — выбрать существующий.",
        reply_markup=kb(copy_row, app_button(cfg)),
    )


@router.message(F.sticker)
async def on_sticker(m: Message, db: DB, svc: PackService, cfg: Config) -> None:
    st = m.sticker
    if st.type == "custom_emoji":
        await m.reply("Это кастомный эмодзи, в обычный стикерпак его не засунуть.")
        return
    await ingest(
        m, db, svc, cfg,
        file_id=st.file_id, file_unique_id=st.file_unique_id,
        thumb=st.thumbnail.file_id if st.thumbnail else None,
        fmt=sticker_format(st), emoji=st.emoji, set_name=st.set_name,
    )


def caption_emoji(m: Message) -> str | None:
    text = (m.caption or "").strip()
    return text.split()[0] if text else None


async def ingest_image(m: Message, bot: Bot, db: DB, svc: PackService, cfg: Config, file_id: str) -> None:
    try:
        data = (await bot.download(file_id)).getvalue()
        sticker_id = await svc.upload(m.from_user.id, data, "static", "img")
    except PackError as e:
        await m.reply(f"Не вышло: {html.escape(str(e))}")
        return
    except TelegramBadRequest as e:
        await m.reply(f"Телега отказала: {html.escape(e.message)}")
        return
    await ingest(
        m, db, svc, cfg, file_id=sticker_id, file_unique_id=sticker_id, thumb=None,
        fmt="static", emoji=caption_emoji(m),
    )


@router.message(F.photo)
async def on_photo(m: Message, bot: Bot, db: DB, svc: PackService, cfg: Config) -> None:
    await ingest_image(m, bot, db, svc, cfg, m.photo[-1].file_id)


@router.message(F.document.mime_type.startswith("image/"))
async def on_image_doc(m: Message, bot: Bot, db: DB, svc: PackService, cfg: Config) -> None:
    if m.document.file_size and m.document.file_size > 20 * 1024 * 1024:
        await m.reply("Файл больше 20 МБ, бот такое скачать не может.")
        return
    await ingest_image(m, bot, db, svc, cfg, m.document.file_id)


@router.message()
async def fallback(m: Message, cfg: Config) -> None:
    await m.answer(
        "Кидай стикеры или картинки. /help — что я умею.", reply_markup=kb(app_button(cfg))
    )
