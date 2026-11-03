import html
import logging
import time

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart, Filter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputSticker,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
    ReactionTypeEmoji,
    WebAppInfo,
)

from . import screens
from .config import Config
from .db import DB
from .icons import Icons
from .service import PackError, PackService, sticker_format

log = logging.getLogger(__name__)
router = Router()

HINT_COOLDOWN = 30  # сек, чтобы не спамить подсказкой при пачке стикеров
_last_hint: dict[int, float] = {}


class NewPack(StatesGroup):
    title = State()


class TouchUser(BaseMiddleware):
    """Каждого, кто пишет боту, заводим в базе (для рассылки)."""

    def __init__(self, db: DB):
        self.db = db

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user and not user.is_bot:
            await self.db.touch_user(user.id, user.first_name, user.username)
        return await handler(event, data)


def btn(icons: Icons, slot: str | None, text: str, **kw) -> InlineKeyboardButton:
    icon = icons.button_id(slot) if slot else None
    if not icon and slot:
        text = f"{icons.fallback(slot)} {text}"
    return InlineKeyboardButton(text=text, icon_custom_emoji_id=icon, **kw)


def app_button(cfg: Config, icons: Icons, text: str = "Редактор наборов", path: str = "") -> list[InlineKeyboardButton]:
    if not cfg.webapp_url:
        return []
    return [btn(icons, "pack", text, web_app=WebAppInfo(url=cfg.webapp_url + path))]


def kb(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup | None:
    rows = [r for r in rows if r]
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


async def react(m: Message, emoji: str) -> None:
    try:
        await m.react([ReactionTypeEmoji(emoji=emoji)])
    except TelegramBadRequest:
        pass


async def buy_row(icons: Icons, svc: PackService, pack: dict) -> list[InlineKeyboardButton]:
    if svc.has_access(pack):
        return []
    price = (await svc.db.get_settings())["price"]
    return [btn(icons, "star", f"Убрать подпись — {price} ⭐", callback_data=f"buy:{pack['id']}")]


def help_text(i: Icons) -> str:
    return (
        f"{i.e('sparkle')} Я собираю стикерпаки из того, что ты мне кидаешь.\n\n"
        "<b>Как пользоваться</b>\n"
        f"{i.e('add')} /new Название — начать новый набор\n"
        f"{i.e('upload')} Кидай стикеры (можно пачкой), фотки или картинки файлом — "
        "всё полетит в набор. Эмодзи в подписи к картинке станет её эмодзи.\n"
        f"{i.e('stop')} /stop — перестать добавлять\n\n"
        f"{i.e('pack')} /packs — мои наборы, куда добавлять\n"
        f"{i.e('copy')} /copy — ответом на стикер: скопировать весь его набор\n\n"
        f"{i.e('star')} Бесплатно к названию набора добавляется подпись бота. "
        "Купи доступ к набору за звёзды — подпись уйдёт, а ссылку можно будет поменять на свою.\n\n"
        f"{i.e('inbox')} Без активного набора стикеры падают во «Входящие» — "
        "оттуда их можно раскидать в редакторе. Там же порядок, названия, эмодзи, удаление."
    )


class InMode(Filter):
    """Юзер в режиме чата (спамблок) или админ ловит пост для сохранения."""

    def __init__(self, prefix: str):
        self.prefix = prefix

    async def __call__(self, m: Message, db: DB) -> bool:
        if not m.from_user:
            return False
        return ((await db.get_user(m.from_user.id))["mode"] or "").startswith(self.prefix)


@router.message(CommandStart())
@router.message(Command("menu"))
async def cmd_start(m: Message, state: FSMContext, db: DB, cfg: Config, icons: Icons) -> None:
    await state.clear()
    await db.set_mode(m.from_user.id, None)
    await screens.send(m.bot, m.chat.id, "menu", db, icons, cfg, m.from_user.first_name)


@router.message(Command("help"))
async def cmd_help(m: Message, cfg: Config, icons: Icons) -> None:
    await m.answer(help_text(icons), reply_markup=kb(app_button(cfg, icons)))


@router.callback_query(F.data.startswith("nav:"))
async def cb_nav(c: CallbackQuery, state: FSMContext, db: DB, cfg: Config, icons: Icons) -> None:
    key = c.data[4:]
    if key not in screens.SCREEN_LABELS:
        await c.answer()
        return
    await state.clear()
    await db.set_mode(c.from_user.id, "chat" if key == "chat" else None)
    await c.answer()
    await screens.navigate(c.message, key, db, icons, cfg, c.from_user.first_name)


@router.callback_query(F.data == "pk:new")
async def cb_pk_new(c: CallbackQuery, state: FSMContext, svc: PackService, icons: Icons) -> None:
    await c.answer()
    await state.set_state(NewPack.title)
    limit = await svc.max_title(c.from_user.id)
    await c.message.answer(f"{icons.e('edit')} Как назовём набор? (до {limit} символов)")


@router.callback_query(F.data == "pk:list")
async def cb_pk_list(c: CallbackQuery, db: DB, svc: PackService, cfg: Config, icons: Icons) -> None:
    await c.answer()
    await cmd_packs(c.message, db, svc, cfg, icons, user_id=c.from_user.id)


@router.message(Command("id"))
async def cmd_id(m: Message) -> None:
    await m.answer(f"Твой ID: <code>{m.from_user.id}</code>")


@router.message(Command("app"))
async def cmd_app(m: Message, cfg: Config, icons: Icons) -> None:
    if not cfg.webapp_url:
        await m.answer("WEBAPP_URL не настроен, редактор недоступен")
        return
    await m.answer(f"{icons.e('pack')} Открывай:", reply_markup=kb(app_button(cfg, icons)))


@router.message(Command("admin"))
async def cmd_admin(m: Message, cfg: Config, icons: Icons, svc: PackService) -> None:
    if not svc.is_admin(m.from_user.id):
        return
    await m.answer(
        f"{icons.e('settings')} Админка",
        reply_markup=kb(app_button(cfg, icons, "Открыть админку", "#admin")),
    )


@router.message(Command("new"))
async def cmd_new(m: Message, command: CommandObject, state: FSMContext, db: DB, svc: PackService, icons: Icons) -> None:
    title = (command.args or "").strip()
    if not title:
        await state.set_state(NewPack.title)
        limit = await svc.max_title(m.from_user.id)
        await m.answer(f"{icons.e('edit')} Как назовём набор? (до {limit} символов)")
        return
    await start_pending(m, db, svc, icons, title)


@router.message(NewPack.title, F.text & ~F.text.startswith("/"))
async def got_title(m: Message, state: FSMContext, db: DB, svc: PackService, icons: Icons) -> None:
    await state.clear()
    await start_pending(m, db, svc, icons, m.text)


async def start_pending(m: Message, db: DB, svc: PackService, icons: Icons, title: str) -> None:
    try:
        title = await svc.check_title(title, m.from_user.id)
    except PackError as e:
        await m.answer(f"{icons.e('warning')} {html.escape(str(e))}\nДавай ещё раз: /new Название")
        return
    await db.set_active(m.from_user.id, None)
    await db.set_pending(m.from_user.id, title)
    await m.answer(
        f"{icons.e('pack')} Набор «{html.escape(title)}». Кидай первый стикер — с него я его и создам."
    )


@router.message(Command("stop", "done"))
async def cmd_stop(m: Message, state: FSMContext, db: DB, icons: Icons) -> None:
    await state.clear()
    user = await db.get_user(m.from_user.id)
    await db.set_active(m.from_user.id, None)
    await db.set_pending(m.from_user.id, None)
    if user["active_pack"] or user["pending_title"]:
        await m.answer(f"{icons.e('stop')} Ок, больше никуда не добавляю. Новые стикеры пойдут во «Входящие».")
    else:
        await m.answer("Да я и так ничего не собирал.")


async def packs_markup(db: DB, svc: PackService, cfg: Config, icons: Icons, user_id: int) -> InlineKeyboardMarkup:
    user = await db.get_user(user_id)
    price = (await db.get_settings())["price"]
    rows = []
    for p in await db.get_packs(user_id):
        active = p["id"] == user["active_pack"]
        row = [
            btn(icons, "check" if active else None, p["base_title"], callback_data=f"a:{p['id']}"),
            InlineKeyboardButton(text="↗️", url=svc.link(p["name"])),
        ]
        if not svc.has_access(p):
            row.append(btn(icons, "star", f"{price}", callback_data=f"buy:{p['id']}"))
        rows.append(row)
    if user["active_pack"]:
        rows.append([btn(icons, "stop", "Никуда не добавлять", callback_data="a:0")])
    rows.append(app_button(cfg, icons))
    return kb(*rows)


@router.message(Command("packs"))
async def cmd_packs(m: Message, db: DB, svc: PackService, cfg: Config, icons: Icons, user_id: int | None = None) -> None:
    uid = user_id or m.from_user.id
    if not await db.get_packs(uid):
        await m.answer("Наборов пока нет. Начни с /new Название", reply_markup=kb(app_button(cfg, icons)))
        return
    await m.answer(
        f"{icons.e('pack')} Твои наборы. Тыкни, чтобы новые стикеры летели туда:",
        reply_markup=await packs_markup(db, svc, cfg, icons, uid),
    )


@router.callback_query(F.data.startswith("a:"))
async def cb_activate(c: CallbackQuery, db: DB, svc: PackService, cfg: Config, icons: Icons) -> None:
    pid = int(c.data[2:])
    uid = c.from_user.id
    if pid:
        pack = await db.get_pack_by_id(pid)
        if not pack or pack["owner_id"] != uid:
            await c.answer("Нет такого набора", show_alert=True)
            return
        await db.set_pending(uid, None)
        await db.set_active(uid, pid)
        await c.answer(f"Теперь добавляю в «{pack['base_title']}»")
    else:
        await db.set_active(uid, None)
        await c.answer("Больше никуда не добавляю")
    try:
        await c.message.edit_reply_markup(reply_markup=await packs_markup(db, svc, cfg, icons, uid))
    except TelegramBadRequest:
        pass


# --- оплата звёздами ---

async def invoice_args(db: DB, pack: dict) -> dict:
    price = (await db.get_settings())["price"]
    return dict(
        title="Доступ к набору",
        description=f"«{pack['base_title'][:60]}»: убрать подпись бота из названия и открыть смену ссылки",
        payload=f"pack:{pack['id']}",
        currency="XTR",
        prices=[LabeledPrice(label="Доступ к набору", amount=price)],
    )


@router.callback_query(F.data.startswith("buy:"))
async def cb_buy(c: CallbackQuery, bot: Bot, db: DB, svc: PackService) -> None:
    pack = await db.get_pack_by_id(int(c.data[4:]))
    if not pack or pack["owner_id"] != c.from_user.id:
        await c.answer("Нет такого набора", show_alert=True)
        return
    if svc.has_access(pack):
        await c.answer("У этого набора уже есть доступ", show_alert=True)
        return
    await c.answer()
    await bot.send_invoice(chat_id=c.from_user.id, **await invoice_args(db, pack))


@router.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery, db: DB, svc: PackService) -> None:
    pack = None
    if q.invoice_payload.startswith("pack:"):
        pack = await db.get_pack_by_id(int(q.invoice_payload[5:]))
    if not pack or pack["owner_id"] != q.from_user.id:
        await q.answer(ok=False, error_message="Набор не найден — возможно, его удалили")
    elif svc.has_access(pack):
        await q.answer(ok=False, error_message="У этого набора уже есть доступ, второй раз платить не надо")
    else:
        await q.answer(ok=True)


@router.message(F.successful_payment)
async def on_paid(m: Message, db: DB, svc: PackService, icons: Icons) -> None:
    pay = m.successful_payment
    pack = await db.get_pack_by_id(int(pay.invoice_payload[5:])) if pay.invoice_payload.startswith("pack:") else None
    if not pack:
        log.error("payment without pack: %s", pay.invoice_payload)
        await m.answer("Оплата пришла, но набор не нашёлся. Напиши админу, вернём звёзды.")
        return
    await db.add_payment(m.from_user.id, pack, pay.total_amount, pay.telegram_payment_charge_id)
    await svc.mark_paid(pack)
    await m.answer(
        f"{icons.e('check')} Оплачено! Подпись из «{html.escape(pack['base_title'])}» убрана, "
        "а в редакторе теперь можно поменять ссылку на набор."
    )


async def do_copy(m: Message, db: DB, svc: PackService, icons: Icons, user_id: int, set_name: str) -> None:
    status = await m.answer(f"{icons.e('copy')} Копирую набор, это может занять время…")
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
        f"{icons.e('check')} Готово: <a href=\"{svc.link(pack['name'])}\">{html.escape(pack['base_title'])}</a>\n"
        "Он теперь активный — новые стикеры полетят туда.",
        reply_markup=kb(await buy_row(icons, svc, pack)),
    )


@router.message(Command("copy"))
async def cmd_copy(m: Message, db: DB, svc: PackService, icons: Icons) -> None:
    src = m.reply_to_message
    if not src or not src.sticker or not src.sticker.set_name:
        await m.answer("Ответь этой командой на стикер из набора, который надо скопировать.")
        return
    await do_copy(m, db, svc, icons, m.from_user.id, src.sticker.set_name)


@router.callback_query(F.data.startswith("cp:"))
async def cb_copy(c: CallbackQuery, db: DB, svc: PackService, icons: Icons) -> None:
    item = await db.get_inbox_item(int(c.data[3:]))
    if not item or item["user_id"] != c.from_user.id or not item["set_name"]:
        await c.answer("Не помню, что это был за набор", show_alert=True)
        return
    await c.answer()
    await db.delete_inbox(c.from_user.id, [item["id"]])
    await do_copy(c.message, db, svc, icons, c.from_user.id, item["set_name"])


# --- админ: «перешли пост — сохраню» ---

@router.message(InMode("capture:"))
async def on_capture(m: Message, db: DB, svc: PackService, cfg: Config, icons: Icons) -> None:
    uid = m.from_user.id
    target = (await db.get_user(uid))["mode"][len("capture:"):]
    await db.set_mode(uid, None)
    if not svc.is_admin(uid):
        return
    text = m.html_text if (m.text or m.caption) else ""
    photo = m.photo[-1].file_id if m.photo else None
    try:
        if target == "broadcast":
            from .broadcast import Broadcaster
            settings = await db.get_settings()
            post = settings["broadcast"]
            post["text"] = text
            if photo:
                post["photo_file_id"] = photo
            Broadcaster.validate(post)
            await db.set_setting("broadcast", post)
            await m.answer(f"{icons.e('check')} Сохранил как пост для рассылки. Вот так он будет выглядеть:")
            from .broadcast import post_markup
            if post.get("photo_file_id"):
                await m.answer_photo(post["photo_file_id"], caption=post["text"] or None, reply_markup=post_markup(post))
            else:
                await m.answer(post["text"], reply_markup=post_markup(post))
        elif target.startswith("screen:") and target[7:] in screens.SCREEN_LABELS:
            key = target[7:]
            if not text:
                raise ValueError("В сообщении нет текста")
            await screens.save_screen(db, key, text, photo if photo else False)
            await m.answer(f"{icons.e('check')} Сохранил экран «{screens.SCREEN_LABELS[key]}». Вот так он выглядит:")
            await screens.send(m.bot, m.chat.id, key, db, icons, cfg, m.from_user.first_name)
    except ValueError as e:
        await m.answer(f"{icons.e('warning')} Не сохранил: {html.escape(str(e))}")
    except TelegramBadRequest as e:
        await m.answer(f"{icons.e('warning')} Сохранил, но Telegram не смог это показать: {html.escape(e.message)}")


# --- спамблок: всё, что пишет юзер, уходит админу в мини-апку ---

CHAT_NOTIFY_COOLDOWN = 60
_last_notify: dict[int, float] = {}


def extract_message(m: Message) -> dict | None:
    caption = m.html_text if (m.text or m.caption) else None
    if m.text:
        return {"kind": "text", "text": caption}
    if m.photo:
        return {"kind": "photo", "text": caption, "file_id": m.photo[-1].file_id}
    if m.sticker:
        st = m.sticker
        return {"kind": "sticker", "text": st.emoji, "file_id": st.file_id,
                "thumb_id": st.thumbnail.file_id if st.thumbnail else None, "fmt": sticker_format(st)}
    for kind in ("video", "animation", "video_note"):
        obj = getattr(m, kind)
        if obj:
            return {"kind": kind, "text": caption, "file_id": obj.file_id,
                    "thumb_id": obj.thumbnail.file_id if obj.thumbnail else None}
    for kind in ("voice", "audio", "document"):
        obj = getattr(m, kind)
        if obj:
            return {"kind": kind, "text": caption, "file_id": obj.file_id,
                    "file_name": getattr(obj, "file_name", None)}
    return None


PREVIEW = {"photo": "Фото", "sticker": "Стикер", "video": "Видео", "animation": "GIF",
           "video_note": "Кружок", "voice": "Голосовое", "audio": "Аудио", "document": "Файл"}


@router.message(InMode("chat"))
async def on_chat(m: Message, bot: Bot, db: DB, svc: PackService, cfg: Config, icons: Icons) -> None:
    data = extract_message(m)
    if not data:
        await m.reply("Такое не передать, отправь текстом, фото, видео или голосовым.")
        return
    uid = m.from_user.id
    await db.add_message(uid, False, **data)
    await react(m, "✍")
    now = time.monotonic()
    if now - _last_notify.get(uid, 0) < CHAT_NOTIFY_COOLDOWN:
        return
    _last_notify[uid] = now
    who = html.escape(m.from_user.full_name) + (f" (@{m.from_user.username})" if m.from_user.username else "")
    preview = data["text"] if data["kind"] == "text" else f"[{PREVIEW[data['kind']]}] {data.get('text') or ''}"
    if len(preview) > 300:
        preview = preview[:300] + "…"
    markup = kb(app_button(cfg, icons, "Открыть чат", f"#chat:{uid}"))
    for admin_id in svc.admin_ids:
        if admin_id == uid:
            continue
        try:
            await bot.send_message(
                admin_id,
                f"{icons.e('chat')} <b>Спамблок</b> — пишет {who}\n\n<blockquote>{preview}</blockquote>",
                reply_markup=markup,
            )
        except TelegramBadRequest:
            # превью могло порезать HTML-тег — шлём без него
            await bot.send_message(admin_id, f"{icons.e('chat')} <b>Спамблок</b> — пишет {who}", reply_markup=markup)
        except Exception:  # noqa: BLE001
            log.exception("notify admin")


# --- приём стикеров и картинок ---

async def ingest(
    m: Message, db: DB, svc: PackService, cfg: Config, icons: Icons, *,
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
                    f"{icons.e('check')} Создал <a href=\"{svc.link(pack['name'])}\">{html.escape(pack['base_title'])}</a>. "
                    "Кидай ещё, всё полетит туда. /stop — закончить.",
                    reply_markup=kb(await buy_row(icons, svc, pack), app_button(cfg, icons)),
                )
                return
            pack = await db.get_pack_by_id(user["active_pack"]) if user["active_pack"] else None
            if pack:
                await svc.add(uid, pack, [inp])
                await react(m, "👍")
                return
            item_id = await db.add_inbox(
                uid, file_id, file_unique_id, thumb, fmt, inp.emoji_list[0], set_name
            )
    except PackError as e:
        await m.reply(f"{icons.e('warning')} Не вышло: {html.escape(str(e))}")
        return
    except TelegramBadRequest as e:
        await m.reply(f"{icons.e('warning')} Телега отказала: {html.escape(e.message)}")
        return

    await react(m, "👀")
    now = time.monotonic()
    if now - _last_hint.get(uid, 0) < HINT_COOLDOWN:
        return
    _last_hint[uid] = now
    copy_row = (
        [btn(icons, "copy", "Скопировать весь этот набор", callback_data=f"cp:{item_id}")]
        if set_name else []
    )
    await m.reply(
        f"{icons.e('inbox')} Активного набора нет, закинул во «Входящие».\n"
        "/new Название — новый набор, /packs — выбрать существующий.",
        reply_markup=kb(copy_row, app_button(cfg, icons)),
    )


@router.message(F.sticker)
async def on_sticker(m: Message, db: DB, svc: PackService, cfg: Config, icons: Icons) -> None:
    st = m.sticker
    if st.type == "custom_emoji":
        await m.reply("Это кастомный эмодзи, в обычный стикерпак его не засунуть.")
        return
    await ingest(
        m, db, svc, cfg, icons,
        file_id=st.file_id, file_unique_id=st.file_unique_id,
        thumb=st.thumbnail.file_id if st.thumbnail else None,
        fmt=sticker_format(st), emoji=st.emoji, set_name=st.set_name,
    )


def caption_emoji(m: Message) -> str | None:
    text = (m.caption or "").strip()
    return text.split()[0] if text else None


async def ingest_image(m: Message, bot: Bot, db: DB, svc: PackService, cfg: Config, icons: Icons, file_id: str) -> None:
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
        m, db, svc, cfg, icons, file_id=sticker_id, file_unique_id=sticker_id, thumb=None,
        fmt="static", emoji=caption_emoji(m),
    )


@router.message(F.photo)
async def on_photo(m: Message, bot: Bot, db: DB, svc: PackService, cfg: Config, icons: Icons) -> None:
    await ingest_image(m, bot, db, svc, cfg, icons, m.photo[-1].file_id)


@router.message(F.document.mime_type.startswith("image/"))
async def on_image_doc(m: Message, bot: Bot, db: DB, svc: PackService, cfg: Config, icons: Icons) -> None:
    if m.document.file_size and m.document.file_size > 20 * 1024 * 1024:
        await m.reply("Файл больше 20 МБ, бот такое скачать не может.")
        return
    await ingest_image(m, bot, db, svc, cfg, icons, m.document.file_id)


@router.message()
async def fallback(m: Message, cfg: Config, icons: Icons) -> None:
    await m.answer(
        f"{icons.e('chat')} Хочешь написать мне — зайди в «Спамблок».\n"
        f"{icons.e('pack')} Кинь стикер или картинку — положу в набор.",
        reply_markup=kb(
            [btn(icons, "chat", "Спамблок", callback_data="nav:chat")],
            [btn(icons, "home", "Меню", callback_data="nav:menu")],
        ),
    )
