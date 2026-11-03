"""HTTP API для мини-апки + раздача её статики."""
import hashlib
import hmac
import logging
import mimetypes
import time
from collections import OrderedDict
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.utils.web_app import safe_parse_webapp_init_data
from aiohttp import web

from .db import DB
from .service import PackError, PackService, sticker_format

log = logging.getLogger(__name__)

WEBAPP_DIR = Path(__file__).resolve().parent.parent / "webapp"
INIT_DATA_TTL = 7 * 24 * 3600
MAX_UPLOAD = 10 * 1024 * 1024
FILE_CACHE_SIZE = 400

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
    def __init__(self, bot: Bot, db: DB, svc: PackService, token: str):
        self.bot = bot
        self.db = db
        self.svc = svc
        self.token = token
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

    async def owned_pack(self, request: web.Request) -> dict:
        pack = await self.db.get_pack(request.match_info["name"])
        if not pack or pack["owner_id"] != self.user_id(request):
            raise web.HTTPNotFound(
                text='{"error":"Набор не найден"}', content_type="application/json"
            )
        return pack

    def ser_inbox(self, item: dict) -> dict:
        return {
            "id": item["id"],
            "format": item["format"],
            "emoji": item["emoji"],
            "file": self.furl(item["file_id"]),
            "thumb": self.furl(item["thumb_file_id"]),
        }

    async def ser_pack(self, pack: dict, full: bool, active_id: int | None) -> dict | None:
        ss = await self.svc.get_set(pack["name"])
        if ss is None:
            return None
        if ss.title != pack["title"]:  # переименовали снаружи
            await self.db.set_pack_title(pack["name"], ss.title)
        data = {
            "name": pack["name"],
            "title": ss.title,
            "link": self.svc.link(pack["name"]),
            "count": len(ss.stickers),
            "active": pack["id"] == active_id,
        }
        if ss.stickers:
            first = ss.stickers[0]
            data["cover"] = {
                "format": sticker_format(first),
                "file": self.furl(first.file_id),
                "thumb": self.furl(first.thumbnail.file_id if first.thumbnail else None),
            }
        if full:
            emojis = await self.svc.emojis_for(ss)
            data["stickers"] = [
                {
                    "uid": s.file_unique_id,
                    "format": sticker_format(s),
                    "emojis": emojis[s.file_unique_id],
                    "file": self.furl(s.file_id),
                    "thumb": self.furl(s.thumbnail.file_id if s.thumbnail else None),
                }
                for s in ss.stickers
            ]
        return data

    async def pack_response(self, request: web.Request, name: str) -> web.Response:
        pack = await self.db.get_pack(name)
        user = await self.db.get_user(self.user_id(request))
        data = await self.ser_pack(pack, True, user["active_pack"]) if pack else None
        if data is None:
            return json_error(404, "Набор больше не существует")
        return web.json_response(data)

    # --- handlers ---
    async def me(self, request: web.Request) -> web.Response:
        uid = self.user_id(request)
        user = await self.db.get_user(uid)
        packs = []
        for p in await self.db.get_packs(uid):
            data = await self.ser_pack(p, False, user["active_pack"])
            if data:
                packs.append(data)
        inbox = [self.ser_inbox(i) for i in await self.db.get_inbox(uid)]
        return web.json_response({
            "packs": packs,
            "inbox": inbox,
            "bot": self.svc.bot_username,
            "pending_title": user["pending_title"],
        })

    async def get_pack(self, request: web.Request) -> web.Response:
        pack = await self.owned_pack(request)
        return await self.pack_response(request, pack["name"])

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
        return await self.pack_response(request, pack["name"])

    async def update_pack(self, request: web.Request) -> web.Response:
        pack = await self.owned_pack(request)
        body = await request.json()
        if "title" in body:
            await self.svc.set_title(pack["name"], body["title"])
        return await self.pack_response(request, pack["name"])

    async def delete_pack(self, request: web.Request) -> web.Response:
        pack = await self.owned_pack(request)
        async with self.svc.lock(self.user_id(request)):
            await self.svc.delete_pack(pack["name"])
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
        return await self.pack_response(request, pack["name"])

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
                await self.svc.add(uid, pack["name"], [inp])
                await self.db.delete_inbox(uid, [item_id])
        return await self.pack_response(request, pack["name"])

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
            await self.svc.add(uid, pack["name"], [self.svc.make_input(file_id, fmt, emojis)])
        return await self.pack_response(request, pack["name"])

    async def reorder(self, request: web.Request) -> web.Response:
        pack = await self.owned_pack(request)
        body = await request.json()
        order = body.get("order")
        if not isinstance(order, list) or not all(isinstance(x, str) for x in order):
            raise PackError("Кривой порядок")
        async with self.svc.lock(self.user_id(request)):
            await self.svc.reorder(pack["name"], order)
        return await self.pack_response(request, pack["name"])

    async def set_emojis(self, request: web.Request) -> web.Response:
        pack = await self.owned_pack(request)
        body = await request.json()
        emojis = body.get("emojis")
        if not isinstance(emojis, list) or not all(isinstance(x, str) for x in emojis):
            raise PackError("Кривой список эмодзи")
        await self.svc.set_emojis(pack["name"], request.match_info["uid"], emojis)
        return await self.pack_response(request, pack["name"])

    async def delete_sticker(self, request: web.Request) -> web.Response:
        pack = await self.owned_pack(request)
        async with self.svc.lock(self.user_id(request)):
            alive = await self.svc.delete_sticker(pack["name"], request.match_info["uid"])
        if not alive:
            return web.json_response({"deleted_pack": True})
        return await self.pack_response(request, pack["name"])

    async def delete_inbox(self, request: web.Request) -> web.Response:
        uid = self.user_id(request)
        body = await request.json()
        ids = [int(i) for i in body.get("ids") or []]
        await self.db.delete_inbox(uid, ids)
        return web.json_response({"inbox": [self.ser_inbox(i) for i in await self.db.get_inbox(uid)]})

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
        return web.Response(
            body=body, content_type=ctype,
            headers={"Cache-Control": "private, max-age=86400"},
        )


def build_app(bot: Bot, db: DB, svc: PackService, token: str) -> web.Application:
    api = Api(bot, db, svc, token)

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
    r = app.router
    r.add_get("/api/me", api.me)
    r.add_post("/api/packs", api.create_pack)
    r.add_get("/api/packs/{name}", api.get_pack)
    r.add_patch("/api/packs/{name}", api.update_pack)
    r.add_delete("/api/packs/{name}", api.delete_pack)
    r.add_post("/api/packs/{name}/active", api.activate)
    r.add_post("/api/packs/{name}/stickers", api.add_from_inbox)
    r.add_post("/api/packs/{name}/upload", api.upload)
    r.add_put("/api/packs/{name}/order", api.reorder)
    r.add_put("/api/packs/{name}/stickers/{uid}", api.set_emojis)
    r.add_delete("/api/packs/{name}/stickers/{uid}", api.delete_sticker)
    r.add_post("/api/inbox/delete", api.delete_inbox)
    r.add_get("/f/{file_id}", api.file)

    async def index(_: web.Request) -> web.FileResponse:
        return web.FileResponse(WEBAPP_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    r.add_get("/", index)
    r.add_static("/static/", WEBAPP_DIR)
    return app
