"""Вся работа с наборами в Telegram. Используется и ботом, и API мини-апки."""
import asyncio
import io
import logging
import re
import secrets
from collections import defaultdict

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import BufferedInputFile, InputSticker, Sticker, StickerSet
from PIL import Image, ImageOps

from .db import DB

log = logging.getLogger(__name__)

MAX_STICKERS = 120
INITIAL_BATCH = 50  # createNewStickerSet принимает максимум 50 стикеров
MAX_EMOJIS = 20
MAX_TITLE = 64
MAX_STATIC_BYTES = 512 * 1024
SLUG_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*")


class PackError(Exception):
    """Ошибка, которую можно показать пользователю как есть."""


async def tg(call, *args, **kwargs):
    """Вызов Bot API с переживанием флуд-лимитов."""
    for attempt in range(6):
        try:
            return await call(*args, **kwargs)
        except TelegramRetryAfter as e:
            if attempt == 5:
                raise
            log.warning("flood wait %ss", e.retry_after)
            await asyncio.sleep(e.retry_after + 0.5)


def sticker_format(st: Sticker) -> str:
    if st.is_animated:
        return "animated"
    if st.is_video:
        return "video"
    return "static"


def _is_set_gone(e: TelegramBadRequest) -> bool:
    msg = (e.message or "").upper()
    return "STICKERSET_INVALID" in msg or "STICKERSET_NOT_FOUND" in msg


def image_to_webp(data: bytes) -> bytes:
    """Картинка -> статичный стикер: WEBP, большая сторона ровно 512px."""
    try:
        im = Image.open(io.BytesIO(data))
        im.seek(0)
        im = ImageOps.exif_transpose(im).convert("RGBA")
    except Exception as e:  # noqa: BLE001
        raise PackError("Не смог прочитать картинку") from e
    w, h = im.size
    scale = 512 / max(w, h)
    im = im.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
    for quality in (95, 85, 75, 60, 45):
        out = io.BytesIO()
        im.save(out, "WEBP", quality=quality, method=6)
        if out.tell() <= MAX_STATIC_BYTES:
            return out.getvalue()
    raise PackError("Картинка слишком тяжёлая даже после сжатия")


def clean_emojis(emojis: list[str] | None, default: str) -> list[str]:
    result: list[str] = []
    for e in emojis or []:
        e = (e or "").strip()
        if e and e not in result:
            result.append(e)
    return result[:MAX_EMOJIS] or [default]


class PackService:
    def __init__(self, bot: Bot, db: DB, bot_username: str, default_emoji: str,
                 admin_ids: frozenset[int] = frozenset()):
        self.bot = bot
        self.db = db
        self.bot_username = bot_username
        self.default_emoji = default_emoji
        self.admin_ids = admin_ids
        self._locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    def lock(self, user_id: int) -> asyncio.Lock:
        """Последовательная обработка на юзера, чтобы стикеры не перемешались."""
        return self._locks[user_id]

    def link(self, name: str) -> str:
        return f"https://t.me/addstickers/{name}"

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_ids

    # --- доступ и подпись ---
    def has_access(self, pack: dict) -> bool:
        """Оплачен (или владелец — админ): без подписи, можно менять ссылку."""
        return bool(pack["paid"]) or self.is_admin(pack["owner_id"])

    async def suffix(self) -> str:
        return (await self.db.get_settings())["suffix"] or ""

    async def max_title(self, user_id: int, paid: bool = False) -> int:
        if paid or self.is_admin(user_id):
            return MAX_TITLE
        return MAX_TITLE - len(await self.suffix())

    async def check_title(self, title: str | None, user_id: int, paid: bool = False) -> str:
        title = (title or "").strip()
        if not title:
            raise PackError("Название не может быть пустым")
        limit = await self.max_title(user_id, paid)
        if len(title) > limit:
            if limit < MAX_TITLE:
                raise PackError(
                    f"Без доступа название максимум {limit} символов — к нему добавляется подпись. "
                    "Купи доступ к набору, и будет 64."
                )
            raise PackError(f"Название длиннее {MAX_TITLE} символов")
        return title

    async def full_title(self, base: str, owner_id: int, paid: bool) -> str:
        if paid or self.is_admin(owner_id):
            return base[:MAX_TITLE]
        suffix = await self.suffix()
        return base[: MAX_TITLE - len(suffix)] + suffix

    def _new_name(self, user_id: int) -> str:
        suffix = f"_by_{self.bot_username}"
        prefix = f"p{user_id}_{secrets.token_hex(3)}"
        return prefix[: MAX_TITLE - len(suffix)] + suffix

    def make_input(self, file_id: str, fmt: str, emojis: list[str] | None) -> InputSticker:
        return InputSticker(
            sticker=file_id, format=fmt, emoji_list=clean_emojis(emojis, self.default_emoji)
        )

    async def upload(self, user_id: int, data: bytes, fmt: str, filename: str) -> str:
        """Заливает файл как стикер и возвращает file_id."""
        if fmt == "static":
            data, filename = image_to_webp(data), "sticker.webp"
        f = await tg(
            self.bot.upload_sticker_file,
            user_id=user_id,
            sticker=BufferedInputFile(data, filename=filename),
            sticker_format=fmt,
        )
        return f.file_id

    # --- чтение ---
    async def get_set(self, pack: dict) -> StickerSet | None:
        """Набор из Telegram. Заодно следит, чтобы у неоплаченных была подпись."""
        try:
            ss = await tg(self.bot.get_sticker_set, name=pack["name"])
        except TelegramBadRequest as e:
            if _is_set_gone(e):
                # набор снесли руками через @Stickers — забываем про него
                await self.db.delete_pack(pack["name"])
                return None
            raise
        if self.has_access(pack):
            if ss.title != pack["title"]:  # переименовали снаружи — принимаем
                await self.db.set_pack_titles(pack["id"], ss.title, ss.title)
                pack.update(title=ss.title, base_title=ss.title)
        else:
            expected = await self.full_title(pack["base_title"], pack["owner_id"], False)
            if ss.title != expected:
                try:
                    await tg(self.bot.set_sticker_set_title, name=pack["name"], title=expected)
                    try:
                        ss = ss.model_copy(update={"title": expected})
                    except AttributeError:
                        ss.title = expected
                    await self.db.set_pack_titles(pack["id"], expected, pack["base_title"])
                    pack["title"] = expected
                except TelegramBadRequest as e:
                    log.warning("can't enforce title on %s: %s", pack["name"], e.message)
        return ss

    async def require_set(self, pack: dict) -> StickerSet:
        ss = await self.get_set(pack)
        if ss is None:
            raise PackError("Набор больше не существует в Telegram")
        return ss

    async def emojis_for(self, ss: StickerSet) -> dict[str, list[str]]:
        stored = await self.db.get_emojis([s.file_unique_id for s in ss.stickers])
        return {
            s.file_unique_id: stored.get(s.file_unique_id) or ([s.emoji] if s.emoji else [])
            for s in ss.stickers
        }

    async def _find(self, pack: dict, uid: str) -> tuple[StickerSet, Sticker]:
        ss = await self.require_set(pack)
        for s in ss.stickers:
            if s.file_unique_id == uid:
                return ss, s
        raise PackError("Стикер не найден в наборе")

    # --- запись ---
    async def create(self, user_id: int, title: str, inputs: list[InputSticker]) -> dict:
        base = await self.check_title(title, user_id)
        if not inputs:
            raise PackError("Нужен хотя бы один стикер")
        if len(inputs) > MAX_STICKERS:
            raise PackError(f"В наборе максимум {MAX_STICKERS} стикеров")
        full = await self.full_title(base, user_id, False)
        name = self._new_name(user_id)
        await tg(
            self.bot.create_new_sticker_set,
            user_id=user_id, name=name, title=full, stickers=inputs[:INITIAL_BATCH],
        )
        pack = await self.db.add_pack(name, user_id, full, base)
        if len(inputs) > INITIAL_BATCH:
            await self.add(user_id, pack, inputs[INITIAL_BATCH:])
        return pack

    async def add(self, user_id: int, pack: dict, inputs: list[InputSticker]) -> int:
        added = 0
        for inp in inputs:
            try:
                await tg(self.bot.add_sticker_to_set, user_id=pack["owner_id"], name=pack["name"], sticker=inp)
            except TelegramBadRequest as e:
                if _is_set_gone(e):
                    await self.db.delete_pack(pack["name"])
                    raise PackError("Набор больше не существует в Telegram") from e
                if "STICKERS_TOO_MUCH" in (e.message or "").upper():
                    raise PackError(f"Набор забит ({MAX_STICKERS}). Добавлено: {added}") from e
                raise
            added += 1
        return added

    async def copy_set(self, user_id: int, src_name: str) -> dict:
        try:
            src = await tg(self.bot.get_sticker_set, name=src_name)
        except TelegramBadRequest as e:
            raise PackError("Исходный набор не найден") from e
        if src.sticker_type != "regular":
            raise PackError("Копировать можно только обычные наборы (не эмодзи и не маски)")
        inputs = [
            self.make_input(s.file_id, sticker_format(s), [s.emoji] if s.emoji else None)
            for s in src.stickers
        ]
        title = src.title[: await self.max_title(user_id)].strip() or "Набор"
        return await self.create(user_id, title, inputs)

    async def set_title(self, pack: dict, title: str) -> None:
        paid = self.has_access(pack)
        base = await self.check_title(title, pack["owner_id"], paid)
        full = await self.full_title(base, pack["owner_id"], paid)
        await tg(self.bot.set_sticker_set_title, name=pack["name"], title=full)
        await self.db.set_pack_titles(pack["id"], full, base)

    async def mark_paid(self, pack: dict) -> None:
        await self.db.set_pack_paid(pack["id"], True)
        pack["paid"] = 1
        try:
            await tg(self.bot.set_sticker_set_title, name=pack["name"], title=pack["base_title"][:MAX_TITLE])
            await self.db.set_pack_titles(pack["id"], pack["base_title"][:MAX_TITLE], pack["base_title"])
        except TelegramBadRequest as e:
            log.warning("can't drop suffix on %s: %s", pack["name"], e.message)

    async def change_link(self, pack: dict, slug: str) -> dict:
        """Telegram не умеет переименовывать ссылку, поэтому пересоздаём набор под новым именем."""
        if not self.has_access(pack):
            raise PackError("Смена ссылки доступна только после покупки доступа к набору")
        slug = (slug or "").strip()
        suffix = f"_by_{self.bot_username}"
        if slug.lower().endswith(suffix.lower()):
            slug = slug[: -len(suffix)]
        if not SLUG_RE.fullmatch(slug) or "__" in slug or slug.endswith("_"):
            raise PackError("Ссылка: латиница, цифры и _, начинается с буквы, без __ и _ на конце")
        new_name = slug + suffix
        if len(new_name) > MAX_TITLE:
            raise PackError(f"Слишком длинно, максимум {MAX_TITLE - len(suffix)} символов")
        if new_name.lower() == pack["name"].lower():
            raise PackError("Это и так текущая ссылка")
        try:
            await tg(self.bot.get_sticker_set, name=new_name)
            raise PackError("Эта ссылка уже занята")
        except TelegramBadRequest:
            pass

        ss = await self.require_set(pack)
        emojis = await self.emojis_for(ss)
        inputs = [self.make_input(s.file_id, sticker_format(s), emojis[s.file_unique_id]) for s in ss.stickers]
        try:
            await tg(
                self.bot.create_new_sticker_set,
                user_id=pack["owner_id"], name=new_name, title=ss.title, stickers=inputs[:INITIAL_BATCH],
            )
        except TelegramBadRequest as e:
            if "OCCUPIED" in (e.message or "").upper():
                raise PackError("Эта ссылка уже занята") from e
            raise
        old_name = pack["name"]
        await self.db.rename_pack(pack["id"], new_name)
        pack["name"] = new_name
        if len(inputs) > INITIAL_BATCH:
            await self.add(pack["owner_id"], pack, inputs[INITIAL_BATCH:])
        # переносим полные списки эмодзи на новые стикеры
        new_ss = await tg(self.bot.get_sticker_set, name=new_name)
        for old, new in zip(ss.stickers, new_ss.stickers):
            if len(emojis[old.file_unique_id]) > 1:
                await self.db.set_emojis(new.file_unique_id, emojis[old.file_unique_id])
        try:
            await tg(self.bot.delete_sticker_set, name=old_name)
        except TelegramBadRequest as e:
            log.warning("old set %s not deleted: %s", old_name, e.message)
        return pack

    async def delete_pack(self, pack: dict) -> None:
        try:
            await tg(self.bot.delete_sticker_set, name=pack["name"])
        except TelegramBadRequest as e:
            if not _is_set_gone(e):
                raise
        await self.db.delete_pack(pack["name"])

    async def reorder(self, pack: dict, order: list[str]) -> None:
        ss = await self.require_set(pack)
        current = [s.file_unique_id for s in ss.stickers]
        file_ids = {s.file_unique_id: s.file_id for s in ss.stickers}
        if sorted(current) != sorted(order):
            raise PackError("Набор изменился, обнови страницу")
        # Двигаем только то, что стоит не на своём месте
        for pos, uid in enumerate(order):
            if current[pos] == uid:
                continue
            await tg(self.bot.set_sticker_position_in_set, sticker=file_ids[uid], position=pos)
            current.remove(uid)
            current.insert(pos, uid)

    async def set_emojis(self, pack: dict, uid: str, emojis: list[str]) -> list[str]:
        _, st = await self._find(pack, uid)
        emojis = clean_emojis(emojis, self.default_emoji)
        await tg(self.bot.set_sticker_emoji_list, sticker=st.file_id, emoji_list=emojis)
        await self.db.set_emojis(uid, emojis)
        return emojis

    async def delete_sticker(self, pack: dict, uid: str) -> bool:
        """Возвращает True, если набор после этого ещё существует."""
        ss, st = await self._find(pack, uid)
        await tg(self.bot.delete_sticker_from_set, sticker=st.file_id)
        if len(ss.stickers) <= 1:
            return await self.get_set(pack) is not None
        return True
