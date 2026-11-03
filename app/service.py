"""Вся работа с наборами в Telegram. Используется и ботом, и API мини-апки."""
import asyncio
import io
import logging
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
MAX_STATIC_BYTES = 512 * 1024


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


def clean_title(title: str | None) -> str:
    title = (title or "").strip()
    if not title:
        raise PackError("Название не может быть пустым")
    if len(title) > 64:
        raise PackError("Название длиннее 64 символов")
    return title


class PackService:
    def __init__(self, bot: Bot, db: DB, bot_username: str, default_emoji: str):
        self.bot = bot
        self.db = db
        self.bot_username = bot_username
        self.default_emoji = default_emoji
        self._locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    def lock(self, user_id: int) -> asyncio.Lock:
        """Последовательная обработка на юзера, чтобы стикеры не перемешались."""
        return self._locks[user_id]

    def link(self, name: str) -> str:
        return f"https://t.me/addstickers/{name}"

    def _new_name(self, user_id: int) -> str:
        suffix = f"_by_{self.bot_username}"
        prefix = f"p{user_id}_{secrets.token_hex(3)}"
        return prefix[: 64 - len(suffix)] + suffix

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
    async def get_set(self, name: str) -> StickerSet | None:
        try:
            return await tg(self.bot.get_sticker_set, name=name)
        except TelegramBadRequest as e:
            if _is_set_gone(e):
                # набор снесли руками через @Stickers — забываем про него
                await self.db.delete_pack(name)
                return None
            raise

    async def require_set(self, name: str) -> StickerSet:
        ss = await self.get_set(name)
        if ss is None:
            raise PackError("Набор больше не существует в Telegram")
        return ss

    async def emojis_for(self, ss: StickerSet) -> dict[str, list[str]]:
        stored = await self.db.get_emojis([s.file_unique_id for s in ss.stickers])
        return {
            s.file_unique_id: stored.get(s.file_unique_id) or ([s.emoji] if s.emoji else [])
            for s in ss.stickers
        }

    async def _find(self, name: str, uid: str) -> tuple[StickerSet, Sticker]:
        ss = await self.require_set(name)
        for s in ss.stickers:
            if s.file_unique_id == uid:
                return ss, s
        raise PackError("Стикер не найден в наборе")

    # --- запись ---
    async def create(self, user_id: int, title: str, inputs: list[InputSticker]) -> dict:
        title = clean_title(title)
        if not inputs:
            raise PackError("Нужен хотя бы один стикер")
        if len(inputs) > MAX_STICKERS:
            raise PackError(f"В наборе максимум {MAX_STICKERS} стикеров")
        name = self._new_name(user_id)
        await tg(
            self.bot.create_new_sticker_set,
            user_id=user_id, name=name, title=title, stickers=inputs[:INITIAL_BATCH],
        )
        pack = await self.db.add_pack(name, user_id, title)
        if len(inputs) > INITIAL_BATCH:
            await self.add(user_id, name, inputs[INITIAL_BATCH:])
        return pack

    async def add(self, user_id: int, name: str, inputs: list[InputSticker]) -> int:
        added = 0
        for inp in inputs:
            try:
                await tg(self.bot.add_sticker_to_set, user_id=user_id, name=name, sticker=inp)
            except TelegramBadRequest as e:
                if _is_set_gone(e):
                    await self.db.delete_pack(name)
                    raise PackError("Набор больше не существует в Telegram") from e
                if "STICKERS_TOO_MUCH" in (e.message or "").upper():
                    raise PackError(
                        f"Набор забит ({MAX_STICKERS}). Добавлено: {added}"
                    ) from e
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
        title = src.title if len(src.title) <= 64 else src.title[:64]
        return await self.create(user_id, title, inputs)

    async def set_title(self, name: str, title: str) -> str:
        title = clean_title(title)
        await tg(self.bot.set_sticker_set_title, name=name, title=title)
        await self.db.set_pack_title(name, title)
        return title

    async def delete_pack(self, name: str) -> None:
        try:
            await tg(self.bot.delete_sticker_set, name=name)
        except TelegramBadRequest as e:
            if not _is_set_gone(e):
                raise
        await self.db.delete_pack(name)

    async def reorder(self, name: str, order: list[str]) -> None:
        ss = await self.require_set(name)
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

    async def set_emojis(self, name: str, uid: str, emojis: list[str]) -> list[str]:
        _, st = await self._find(name, uid)
        emojis = clean_emojis(emojis, self.default_emoji)
        await tg(self.bot.set_sticker_emoji_list, sticker=st.file_id, emoji_list=emojis)
        await self.db.set_emojis(uid, emojis)
        return emojis

    async def delete_sticker(self, name: str, uid: str) -> bool:
        """Возвращает True, если набор после этого ещё существует."""
        ss, st = await self._find(name, uid)
        await tg(self.bot.delete_sticker_from_set, sticker=st.file_id)
        if len(ss.stickers) <= 1:
            return await self.get_set(name) is not None
        return True
