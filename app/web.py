"""HTTP API для мини-апки + раздача её статики."""
import asyncio
import hashlib
import html
import hmac
import logging
import mimetypes
import time
from collections import OrderedDict
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import BufferedInputFile
from aiogram.utils.web_app import safe_parse_webapp_init_data
from aiohttp import web

from . import screens
from .bot import invoice_args
from .broadcast import WEEKDAYS, Broadcaster
from .db import DB
from .icons import SLOTS, STRIPPED, Icons
from .service import PackError, PackService, sticker_format

log = logging.getLogger(__name__)

WEBAPP_DIR = Path(__file__).resolve().parent.parent / "webapp"
TASKS = web.AppKey("tasks", set)
INIT_DATA_TTL = 7 * 24 * 3600
MAX_UPLOAD = 10 * 1024 * 1024
FILE_CACHE_SIZE = 600

CONTENT_TYPES = {
    ".webp": "image/webp",
    ".tgs": "application/x-tgsticker",
    ".webm": "video/webm",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}


def json_error(status: int, message: str) -> web.Response:
    return web.json_response({"error": message}, status=status)


class Api:
    def __init__(self, bot: Bot, db: DB, svc: PackService, icons: Icons, caster: Broadcaster, token: str, cfg=None,
                 app_icons: Icons | None = None):
        self.app_icons = app_icons or icons
        self.cfg = cfg
        self.bot = bot
        self.db = db
        self.svc = svc
        self.icons = icons
        self.caster = caster
        self._sign_key = hashlib.sha256(b"file-proxy:" + token.encode()).digest()
        self._files: OrderedDict[str, tuple[bytes, str]] = OrderedDict()

    # --- utils ---
    def sign(self, file_id: str) -> str:
        return hmac.new(self._sign_key, file_id.encode(), hashlib.sha256).hexdigest()[:24]

    def furl(self, file_id: str | None) -> str | None:
        if not file_id:
            return None
        return f"/f/{file_id}?s={self.sign(file_id)}"

    def user_id(self, request: web.Request) -> int:
        return request["user_id"]

    def require_admin(self, request: web.Request) -> None:
        if not self.svc.is_admin(self.user_id(request)):
            raise web.HTTPForbidden(text='{"error":"Только для админа"}', content_type="application/json")

    async def owned_pack(self, request: web.Request) -> dict:
        pack = await self.db.get_pack(request.match_info["name"])
        if not pack or pack["owner_id"] != self.user_id(request):
            raise web.HTTPNotFound(text='{"error":"Набор не найден"}', content_type="application/json")
        return pack

    def ser_media(self, file_id: str | None, thumb_id: str | None, fmt: str) -> dict:
        return {"format": fmt, "file": self.furl(file_id), "thumb": self.furl(thumb_id)}

    def ser_inbox(self, item: dict) -> dict:
        return {"id": item["id"], "emoji": item["emoji"],
                **self.ser_media(item["file_id"], item["thumb_file_id"], item["format"])}

    async def ser_pack(self, pack: dict, full: bool, active_id: int | None) -> dict | None:
        ss = await self.svc.get_set(pack)
        if ss is None:
            return None
        data = {
            "name": pack["name"],
            "title": pack["base_title"],
            "full_title": ss.title,
            "link": self.svc.link(pack["name"]),
            "count": len(ss.stickers),
            "active": pack["id"] == active_id,
            "access": self.svc.has_access(pack),
            "max_title": await self.svc.max_title(pack["owner_id"], self.svc.has_access(pack)),
        }
        if ss.stickers:
            first = ss.stickers[0]
            data["cover"] = self.ser_media(
                first.file_id, first.thumbnail.file_id if first.thumbnail else None, sticker_format(first)
            )
        if full:
            emojis = await self.svc.emojis_for(ss)
            data["stickers"] = [
                {"uid": s.file_unique_id, "emojis": emojis[s.file_unique_id],
                 **self.ser_media(s.file_id, s.thumbnail.file_id if s.thumbnail else None, sticker_format(s))}
                for s in ss.stickers
            ]
        return data

    async def pack_response(self, request: web.Request, pack_id: int) -> web.Response:
        pack = await self.db.get_pack_by_id(pack_id)
        user = await self.db.get_user(self.user_id(request))
        data = await self.ser_pack(pack, True, user["active_pack"]) if pack else None
        if data is None:
            return json_error(404, "Набор больше не существует")
        return web.json_response(data)

    # --- общие ---
    async def me(self, request: web.Request) -> web.Response:
        uid = self.user_id(request)
        user = await self.db.get_user(uid)
        settings = await self.db.get_settings()
        packs = []
        for p in await self.db.get_packs(uid):
            data = await self.ser_pack(p, False, user["active_pack"])
            if data:
                packs.append(data)
        return web.json_response({
            "packs": packs,
            "inbox": [self.ser_inbox(i) for i in await self.db.get_inbox(uid)],
            "bot": self.svc.bot_username,
            "pending_title": user["pending_title"],
            "is_admin": self.svc.is_admin(uid),
            "price": settings["price"],
            "suffix": settings["suffix"],
            "max_title": await self.svc.max_title(uid),
        })

    async def icons_handler(self, request: web.Request) -> web.Response:
        icons = self.icons if request.query.get("scope") == "bot" else self.app_icons
        await icons.refresh()
        slots = {}
        for slot, (label, cands) in SLOTS.items():
            item = icons.resolved.get(slot)
            slots[slot] = {
                "label": label,
                "fallback": cands[0],
                "icon": {"id": item["id"], **self.ser_media(item["file_id"], item["thumb_id"], item["format"])}
                if item else None,
            }
        data = {"slots": slots}
        if self.svc.is_admin(self.user_id(request)):
            data["all"] = [
                {"id": i["id"], "emoji": i["emoji"], **self.ser_media(i["file_id"], i["thumb_id"], i["format"])}
                for i in icons.items + [a for a in icons.accent if a["id"] not in {x["id"] for x in icons.items}]
            ]
        return web.json_response(data)

    # --- наборы ---
    async def get_pack(self, request: web.Request) -> web.Response:
        pack = await self.owned_pack(request)
        return await self.pack_response(request, pack["id"])

    async def _inbox_inputs(self, uid: int, ids: list) -> tuple[list[int], list]:
        try:
            ids = [int(i) for i in ids]
        except (TypeError, ValueError):
            raise PackError("Кривые id")
        items = await self.db.get_inbox_items(uid, ids)
        inputs = [self.svc.make_input(i["file_id"], i["format"], [i["emoji"]]) for i in items]
        return [i["id"] for i in items], inputs

    async def create_pack(self, request: web.Request) -> web.Response:
        uid = self.user_id(request)
        body = await request.json()
        async with self.svc.lock(uid):
            ids, inputs = await self._inbox_inputs(uid, body.get("inbox_ids") or [])
            if not inputs:
                raise PackError("Выбери хотя бы один стикер из входящих")
            pack = await self.svc.create(uid, body.get("title", ""), inputs)
            await self.db.delete_inbox(uid, ids)
        return await self.pack_response(request, pack["id"])

    async def create_pack_upload(self, request: web.Request) -> web.Response:
        """Новый набор сразу из загруженного файла (когда во входящих пусто)."""
        uid = self.user_id(request)
        reader = await request.multipart()
        data, filename, title, emojis = None, "file", "", []
        async for part in reader:
            if part.name == "file":
                filename = part.filename or "file"
                data = await part.read(decode=False)
                if len(data) > MAX_UPLOAD:
                    raise PackError("Файл больше 10 МБ")
            elif part.name == "title":
                title = await part.text()
            elif part.name == "emojis":
                emojis = (await part.text()).split()
        if not data:
            raise PackError("Файл не пришёл")
        title = await self.svc.check_title(title, uid)  # проверяем до заливки файла
        fmt = {".tgs": "animated", ".webm": "video"}.get(Path(filename).suffix.lower(), "static")
        async with self.svc.lock(uid):
            file_id = await self.svc.upload(uid, data, fmt, filename)
            pack = await self.svc.create(uid, title, [self.svc.make_input(file_id, fmt, emojis)])
        return await self.pack_response(request, pack["id"])

    async def create_pack_pending(self, request: web.Request) -> web.Response:
        """Название задано в мини-апке, набор создастся с первого стикера, кинутого боту."""
        uid = self.user_id(request)
        title = await self.svc.check_title((await request.json()).get("title"), uid)
        await self.db.set_active(uid, None)
        await self.db.set_mode(uid, None)
        await self.db.set_pending(uid, title)
        return web.json_response({"ok": True, "title": title})

    async def update_pack(self, request: web.Request) -> web.Response:
        pack = await self.owned_pack(request)
        body = await request.json()
        if "title" in body:
            await self.svc.set_title(pack, body["title"])
        return await self.pack_response(request, pack["id"])

    async def delete_pack(self, request: web.Request) -> web.Response:
        pack = await self.owned_pack(request)
        async with self.svc.lock(self.user_id(request)):
            await self.svc.delete_pack(pack)
        return web.json_response({"ok": True})

    async def activate(self, request: web.Request) -> web.Response:
        pack = await self.owned_pack(request)
        uid = self.user_id(request)
        body = await request.json()
        if body.get("active"):
            await self.db.set_pending(uid, None)
            await self.db.set_active(uid, pack["id"])
        else:
            user = await self.db.get_user(uid)
            if user["active_pack"] == pack["id"]:
                await self.db.set_active(uid, None)
        return await self.pack_response(request, pack["id"])

    async def add_from_inbox(self, request: web.Request) -> web.Response:
        pack = await self.owned_pack(request)
        uid = self.user_id(request)
        body = await request.json()
        async with self.svc.lock(uid):
            ids, inputs = await self._inbox_inputs(uid, body.get("inbox_ids") or [])
            if not inputs:
                raise PackError("Нечего добавлять")
            # по одному, чтобы при ошибке на середине уже добавленные ушли из входящих
            for item_id, inp in zip(ids, inputs):
                await self.svc.add(uid, pack, [inp])
                await self.db.delete_inbox(uid, [item_id])
        return await self.pack_response(request, pack["id"])

    async def upload(self, request: web.Request) -> web.Response:
        pack = await self.owned_pack(request)
        uid = self.user_id(request)
        reader = await request.multipart()
        data, filename, emojis = None, "file", []
        async for part in reader:
            if part.name == "file":
                filename = part.filename or "file"
                data = await part.read(decode=False)
                if len(data) > MAX_UPLOAD:
                    raise PackError("Файл больше 10 МБ")
            elif part.name == "emojis":
                emojis = (await part.text()).split()
        if not data:
            raise PackError("Файл не пришёл")
        ext = Path(filename).suffix.lower()
        fmt = {".tgs": "animated", ".webm": "video"}.get(ext, "static")
        async with self.svc.lock(uid):
            file_id = await self.svc.upload(uid, data, fmt, filename)
            await self.svc.add(uid, pack, [self.svc.make_input(file_id, fmt, emojis)])
        return await self.pack_response(request, pack["id"])

    async def reorder(self, request: web.Request) -> web.Response:
        pack = await self.owned_pack(request)
        body = await request.json()
        order = body.get("order")
        if not isinstance(order, list) or not all(isinstance(x, str) for x in order):
            raise PackError("Кривой порядок")
        async with self.svc.lock(self.user_id(request)):
            await self.svc.reorder(pack, order)
        return await self.pack_response(request, pack["id"])

    async def set_emojis(self, request: web.Request) -> web.Response:
        pack = await self.owned_pack(request)
        body = await request.json()
        emojis = body.get("emojis")
        if not isinstance(emojis, list) or not all(isinstance(x, str) for x in emojis):
            raise PackError("Кривой список эмодзи")
        await self.svc.set_emojis(pack, request.match_info["uid"], emojis)
        return await self.pack_response(request, pack["id"])

    async def delete_sticker(self, request: web.Request) -> web.Response:
        pack = await self.owned_pack(request)
        async with self.svc.lock(self.user_id(request)):
            alive = await self.svc.delete_sticker(pack, request.match_info["uid"])
        if not alive:
            return web.json_response({"deleted_pack": True})
        return await self.pack_response(request, pack["id"])

    async def invoice(self, request: web.Request) -> web.Response:
        pack = await self.owned_pack(request)
        if self.svc.has_access(pack):
            raise PackError("У этого набора уже есть доступ")
        link = await self.bot.create_invoice_link(**await invoice_args(self.db, pack))
        return web.json_response({"url": link})

    async def change_link(self, request: web.Request) -> web.Response:
        pack = await self.owned_pack(request)
        body = await request.json()
        async with self.svc.lock(self.user_id(request)):
            await self.svc.change_link(pack, str(body.get("slug", "")))
        return await self.pack_response(request, pack["id"])

    async def delete_inbox(self, request: web.Request) -> web.Response:
        uid = self.user_id(request)
        body = await request.json()
        ids = [int(i) for i in body.get("ids") or []]
        await self.db.delete_inbox(uid, ids)
        return web.json_response({"inbox": [self.ser_inbox(i) for i in await self.db.get_inbox(uid)]})

    # --- админка ---
    async def admin_state(self, request: web.Request) -> web.Response:
        self.require_admin(request)
        settings = await self.db.get_settings()
        post = settings["broadcast"]
        nxt = self.caster.next_run(post)
        payments = [
            {"id": p["id"], "user_id": p["user_id"], "amount": p["amount"], "refunded": bool(p["refunded"]),
             "created_at": p["created_at"], "pack": p["pack_title"] or p["pack_name"]}
            for p in await self.db.recent_payments()
        ]
        return web.json_response({
            "stats": await self.db.stats(),
            "payments": payments,
            "settings": {
                "price": settings["price"],
                "suffix": settings["suffix"],
                "icon_pack": settings["icon_pack"],
                "icon_map": settings["icon_map"],
                "app_icon_pack": settings["app_icon_pack"],
                "accent_pack": settings["accent_pack"],
                "app_icon_map": settings["app_icon_map"],
                "bot_custom_emoji": settings["bot_custom_emoji"],
            },
            "broadcast": {
                **{k: post[k] for k in ("enabled", "text", "button_text", "button_url", "weekday", "hour",
                                        "last_sent_at", "last_result")},
                "photo": self.furl(post.get("photo_file_id")),
                "next_run": int(nxt.timestamp()) if nxt else None,
                "running": self.caster.progress,
                "timezone": str(self.caster.tz),
            },
            "weekdays": WEEKDAYS,
            "icon_errors": self.icons.errors,
            "app_icon_errors": self.app_icons.errors,
            "letters": "".join(sorted(self.icons.letters.keys())),
            "emoji_rejected_at": int(STRIPPED["at"]) or None,
            "heading_letters": screens.HEADING_LETTERS,
            "accent_errors": self.icons.accent_errors,
            "rich_error": {"at": int(screens.RICH_ERRORS["at"]), "error": screens.RICH_ERRORS["error"]}
            if screens.RICH_ERRORS["at"] else None,
            "unread": await self.db.unread_total(),
            "screens": await self.ser_screens(),
            "order_links": await screens.get_order_links(self.db),
        })

    async def ser_screens(self) -> list[dict]:
        data = await screens.get_screens(self.db)
        return [
            {"key": k, "label": screens.SCREEN_LABELS[k], "text": v["text"], "custom": v["custom"],
             "photo": self.furl(v["photo_file_id"]), "default": screens.DEFAULT_TEXTS[k]}
            for k, v in data.items()
        ]

    async def _upload_photo(self, request: web.Request) -> str:
        """Принимает картинку из мини-апки и возвращает её file_id в Telegram."""
        uid = self.user_id(request)
        reader = await request.multipart()
        data, filename = None, "photo.jpg"
        async for part in reader:
            if part.name == "file":
                filename = part.filename or filename
                data = await part.read(decode=False)
        if not data:
            raise PackError("Файл не пришёл")
        # Заливаем фотку админу в личку, чтобы получить file_id, и сразу удаляем сообщение
        msg = await self.bot.send_photo(uid, BufferedInputFile(data, filename=filename),
                                        caption="Картинка сохранена", disable_notification=True)
        try:
            await self.bot.delete_message(uid, msg.message_id)
        except TelegramBadRequest:
            pass
        return msg.photo[-1].file_id

    # --- экраны меню ---
    def _screen_key(self, request: web.Request) -> str:
        key = request.match_info["key"]
        if key not in screens.SCREEN_LABELS:
            raise PackError("Нет такого экрана")
        return key

    async def admin_screen(self, request: web.Request) -> web.Response:
        self.require_admin(request)
        key = self._screen_key(request)
        body = await request.json()
        try:
            if "text" in body or body.get("remove_photo"):
                await screens.save_screen(
                    self.db, key,
                    text=str(body["text"]).strip() if "text" in body else None,
                    photo_file_id=None if body.get("remove_photo") else False,
                )
        except ValueError as e:
            raise PackError(str(e))
        if key == "order" and isinstance(body.get("links"), dict):
            links = {}
            for k in screens.DEFAULT_ORDER_LINKS:
                url = str(body["links"].get(k) or "").strip()
                if url and not url.startswith(("https://", "http://", "tg://")):
                    raise PackError("Ссылки должны начинаться с https://")
                if url:
                    links[k] = url
            await self.db.set_setting("order_links", links)
        return await self.admin_state(request)

    async def admin_screen_photo(self, request: web.Request) -> web.Response:
        self.require_admin(request)
        key = self._screen_key(request)
        file_id = await self._upload_photo(request)
        try:
            await screens.save_screen(self.db, key, photo_file_id=file_id)
        except ValueError as e:
            raise PackError(str(e))
        return await self.admin_state(request)

    async def admin_screen_test(self, request: web.Request) -> web.Response:
        self.require_admin(request)
        key = self._screen_key(request)
        await screens.send(self.bot, self.user_id(request), key, self.db, self.icons, self.cfg, "Тест")
        return web.json_response({"ok": True})

    async def admin_capture(self, request: web.Request) -> web.Response:
        self.require_admin(request)
        body = await request.json()
        target = str(body.get("target", ""))
        if target not in ("broadcast", "letters") and not (target.startswith("screen:") and target[7:] in screens.SCREEN_LABELS):
            raise PackError("Непонятно, куда сохранять")
        await self.db.set_mode(self.user_id(request), "capture:" + target)
        return web.json_response({"ok": True})

    # --- спамблок-мессенджер ---
    def ser_message(self, m: dict) -> dict:
        return {
            "id": m["id"], "out": bool(m["outgoing"]), "kind": m["kind"], "text": m["text"],
            "at": m["created_at"], "file_name": m["file_name"],
            "format": m["fmt"] or ("static" if m["kind"] == "sticker" else None),
            "file": self.furl(m["file_id"]), "thumb": self.furl(m["thumb_id"]),
        }

    async def admin_dialogs(self, request: web.Request) -> web.Response:
        self.require_admin(request)
        return web.json_response({"dialogs": [
            {"user_id": d["user_id"], "name": d["first_name"] or str(d["user_id"]), "username": d["username"],
             "blocked": bool(d["blocked"]), "unread": d["unread"], "last": self.ser_message(d)}
            for d in await self.db.dialogs()
        ]})

    async def admin_chat(self, request: web.Request) -> web.Response:
        self.require_admin(request)
        uid = int(request.match_info["uid"])
        after = int(request.query.get("after", 0))
        user = await self.db.get_user(uid)
        messages = await self.db.get_messages(uid, after)
        await self.db.mark_read(uid)
        return web.json_response({
            "user": {"user_id": uid, "name": user["first_name"] or str(uid), "username": user["username"],
                     "blocked": bool(user["blocked"]), "in_chat": user["mode"] == "chat"},
            "messages": [self.ser_message(m) for m in messages],
        })

    async def admin_chat_send(self, request: web.Request) -> web.Response:
        self.require_admin(request)
        uid = int(request.match_info["uid"])
        header = f"{self.icons.e('chat')} <b>Ответ:</b>\n\n"
        try:
            if request.content_type.startswith("multipart/"):
                reader = await request.multipart()
                data, filename, text = None, "photo.jpg", ""
                async for part in reader:
                    if part.name == "file":
                        filename = part.filename or filename
                        data = await part.read(decode=False)
                    elif part.name == "text":
                        text = (await part.text()).strip()
                if not data:
                    raise PackError("Файл не пришёл")
                body = html.escape(text)
                msg = await self.bot.send_photo(uid, BufferedInputFile(data, filename=filename),
                                                caption=(header + body) if body else header.strip())
                await self.db.add_message(uid, True, "photo", body or None, msg.photo[-1].file_id)
            else:
                text = str((await request.json()).get("text", "")).strip()
                if not text:
                    raise PackError("Пустое сообщение")
                if len(text) > 4000:
                    raise PackError("Слишком длинно, максимум 4000 символов")
                body = html.escape(text)
                await self.bot.send_message(uid, header + body)
                await self.db.add_message(uid, True, "text", body)
        except TelegramForbiddenError:
            await self.db.set_blocked(uid)
            raise PackError("Юзер заблокировал бота — сообщение не дойдёт")
        return await self.admin_chat(request)

    async def admin_settings(self, request: web.Request) -> web.Response:
        self.require_admin(request)
        body = await request.json()
        if "price" in body:
            try:
                price = int(body["price"])
            except (TypeError, ValueError):
                raise PackError("Цена — целое число")
            if not 1 <= price <= 10000:
                raise PackError("Цена от 1 до 10000 звёзд")
            await self.db.set_setting("price", price)
        if "suffix" in body:
            suffix = str(body["suffix"])
            if len(suffix) > 40:
                raise PackError("Подпись максимум 40 символов, иначе под название места не останется")
            await self.db.set_setting("suffix", suffix)
        if "icon_pack" in body:
            name = str(body["icon_pack"]).strip()
            await self.db.set_setting("icon_pack", name)
        if "bot_custom_emoji" in body:
            await self.db.set_setting("bot_custom_emoji", bool(body["bot_custom_emoji"]))
        if body.get("reset_letters"):
            await self.db.set_setting("letters", {})
        if "accent_pack" in body:
            await self.db.set_setting("accent_pack", str(body["accent_pack"]).strip())
        if "app_icon_pack" in body:
            await self.db.set_setting("app_icon_pack", str(body["app_icon_pack"]).strip())
        for key in ("icon_map", "app_icon_map"):
            if key in body and isinstance(body[key], dict):
                clean = {k: str(v) for k, v in body[key].items() if k in SLOTS and v}
                await self.db.set_setting(key, clean)
        await self.icons.refresh(force="icon_pack" in body or "accent_pack" in body)
        await self.app_icons.refresh(force="app_icon_pack" in body)
        return await self.admin_state(request)

    async def admin_broadcast(self, request: web.Request) -> web.Response:
        self.require_admin(request)
        body = await request.json()
        post = (await self.db.get_settings())["broadcast"]
        for key in ("text", "button_text", "button_url"):
            if key in body:
                post[key] = str(body[key] or "").strip()
        if "enabled" in body:
            post["enabled"] = bool(body["enabled"])
        if "weekday" in body:
            post["weekday"] = max(0, min(6, int(body["weekday"])))
        if "hour" in body:
            post["hour"] = max(0, min(23, int(body["hour"])))
        if body.get("remove_photo"):
            post["photo_file_id"] = None
        if post["enabled"] or post["text"] or post["photo_file_id"]:
            try:
                Broadcaster.validate(post)
            except ValueError as e:
                raise PackError(str(e))
        await self.db.set_setting("broadcast", post)
        return await self.admin_state(request)

    async def admin_broadcast_photo(self, request: web.Request) -> web.Response:
        self.require_admin(request)
        file_id = await self._upload_photo(request)
        post = (await self.db.get_settings())["broadcast"]
        post["photo_file_id"] = file_id
        await self.db.set_setting("broadcast", post)
        return await self.admin_state(request)

    async def admin_broadcast_test(self, request: web.Request) -> web.Response:
        self.require_admin(request)
        post = (await self.db.get_settings())["broadcast"]
        try:
            Broadcaster.validate(post)
        except ValueError as e:
            raise PackError(str(e))
        await self.caster.send_post(self.user_id(request), post)
        return web.json_response({"ok": True})

    async def admin_broadcast_send(self, request: web.Request) -> web.Response:
        self.require_admin(request)
        if self.caster.running:
            raise PackError("Рассылка уже идёт")
        post = (await self.db.get_settings())["broadcast"]
        try:
            Broadcaster.validate(post)
        except ValueError as e:
            raise PackError(str(e))
        task = asyncio.create_task(self.caster.run())
        request.app[TASKS].add(task)
        task.add_done_callback(request.app[TASKS].discard)
        return await self.admin_state(request)

    async def admin_refund(self, request: web.Request) -> web.Response:
        self.require_admin(request)
        body = await request.json()
        pay = await self.db.get_payment(int(body.get("id", 0)))
        if not pay or pay["refunded"]:
            raise PackError("Платёж не найден или уже возвращён")
        await self.bot.refund_star_payment(user_id=pay["user_id"], telegram_payment_charge_id=pay["charge_id"])
        await self.db.set_refunded(pay["id"])
        pack = await self.db.get_pack_by_id(pay["pack_id"]) if pay["pack_id"] else None
        if pack:
            await self.db.set_pack_paid(pack["id"], False)
            pack["paid"] = 0
            await self.svc.get_set(pack)  # вернёт подпись в название
        return await self.admin_state(request)

    # --- файлы ---
    async def file(self, request: web.Request) -> web.Response:
        file_id = request.match_info["file_id"]
        if not hmac.compare_digest(request.query.get("s", ""), self.sign(file_id)):
            raise web.HTTPForbidden()
        cached = self._files.get(file_id)
        if cached is None:
            try:
                f = await self.bot.get_file(file_id)
                buf = await self.bot.download_file(f.file_path)
            except TelegramBadRequest:
                raise web.HTTPNotFound()
            ext = Path(f.file_path).suffix.lower()
            ctype = CONTENT_TYPES.get(ext) or mimetypes.guess_type(f.file_path)[0] or "application/octet-stream"
            cached = (buf.getvalue(), ctype)
            self._files[file_id] = cached
            while len(self._files) > FILE_CACHE_SIZE:
                self._files.popitem(last=False)
        else:
            self._files.move_to_end(file_id)
        body, ctype = cached
        return web.Response(body=body, content_type=ctype, headers={"Cache-Control": "private, max-age=86400"})


def build_app(bot: Bot, db: DB, svc: PackService, icons: Icons, caster: Broadcaster, token: str,
              cfg=None, app_icons: Icons | None = None) -> web.Application:
    api = Api(bot, db, svc, icons, caster, token, cfg, app_icons)

    @web.middleware
    async def middleware(request: web.Request, handler):
        if request.path.startswith("/api/"):
            init_data = request.headers.get("X-Init-Data", "")
            try:
                parsed = safe_parse_webapp_init_data(token, init_data)
            except ValueError:
                return json_error(401, "Открой через Telegram")
            if not parsed.user or time.time() - parsed.auth_date.timestamp() > INIT_DATA_TTL:
                return json_error(401, "Сессия протухла, переоткрой мини-апку")
            request["user_id"] = parsed.user.id
        try:
            return await handler(request)
        except PackError as e:
            return json_error(400, str(e))
        except TelegramBadRequest as e:
            return json_error(400, f"Telegram: {e.message}")
        except web.HTTPException:
            raise
        except Exception:  # noqa: BLE001
            log.exception("api error")
            return json_error(500, "Что-то сломалось на сервере")

    app = web.Application(middlewares=[middleware], client_max_size=MAX_UPLOAD + 1024 * 1024)
    app[TASKS] = set()
    r = app.router
    r.add_get("/api/me", api.me)
    r.add_get("/api/icons", api.icons_handler)
    r.add_post("/api/packs", api.create_pack)
    r.add_post("/api/packs/new/upload", api.create_pack_upload)
    r.add_post("/api/packs/new/pending", api.create_pack_pending)
    r.add_get("/api/packs/{name}", api.get_pack)
    r.add_patch("/api/packs/{name}", api.update_pack)
    r.add_delete("/api/packs/{name}", api.delete_pack)
    r.add_post("/api/packs/{name}/active", api.activate)
    r.add_post("/api/packs/{name}/stickers", api.add_from_inbox)
    r.add_post("/api/packs/{name}/upload", api.upload)
    r.add_post("/api/packs/{name}/invoice", api.invoice)
    r.add_post("/api/packs/{name}/link", api.change_link)
    r.add_put("/api/packs/{name}/order", api.reorder)
    r.add_put("/api/packs/{name}/stickers/{uid}", api.set_emojis)
    r.add_delete("/api/packs/{name}/stickers/{uid}", api.delete_sticker)
    r.add_post("/api/inbox/delete", api.delete_inbox)
    r.add_get("/api/admin", api.admin_state)
    r.add_put("/api/admin/settings", api.admin_settings)
    r.add_put("/api/admin/broadcast", api.admin_broadcast)
    r.add_post("/api/admin/broadcast/photo", api.admin_broadcast_photo)
    r.add_post("/api/admin/broadcast/test", api.admin_broadcast_test)
    r.add_post("/api/admin/broadcast/send", api.admin_broadcast_send)
    r.add_post("/api/admin/refund", api.admin_refund)
    r.add_put("/api/admin/screens/{key}", api.admin_screen)
    r.add_post("/api/admin/screens/{key}/photo", api.admin_screen_photo)
    r.add_post("/api/admin/screens/{key}/test", api.admin_screen_test)
    r.add_post("/api/admin/capture", api.admin_capture)
    r.add_get("/api/admin/chats", api.admin_dialogs)
    r.add_get("/api/admin/chats/{uid}", api.admin_chat)
    r.add_post("/api/admin/chats/{uid}/send", api.admin_chat_send)
    r.add_get("/f/{file_id}", api.file)

    version = str(int(time.time()))  # сбивает кеш вебвью Telegram после обновления

    async def index(_: web.Request) -> web.Response:
        page = (WEBAPP_DIR / "index.html").read_text(encoding="utf-8").replace("{{v}}", version)
        return web.Response(text=page, content_type="text/html", headers={"Cache-Control": "no-cache"})

    r.add_get("/", index)
    r.add_static("/static/", WEBAPP_DIR, append_version=False)
    return app
