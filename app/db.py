import copy
import json
import os
import time
from typing import Any

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS packs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    owner_id   INTEGER NOT NULL,
    title      TEXT NOT NULL,          -- название в Telegram (с подписью, если не оплачен)
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS packs_owner ON packs(owner_id);

CREATE TABLE IF NOT EXISTS users (
    user_id       INTEGER PRIMARY KEY,
    active_pack   INTEGER,
    pending_title TEXT
);

CREATE TABLE IF NOT EXISTS inbox (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL,
    file_id        TEXT NOT NULL,
    file_unique_id TEXT NOT NULL,
    thumb_file_id  TEXT,
    format         TEXT NOT NULL,
    emoji          TEXT NOT NULL,
    set_name       TEXT,
    created_at     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS inbox_user ON inbox(user_id);

-- Bot API отдаёт у стикера только один emoji, полный список храним сами
CREATE TABLE IF NOT EXISTS sticker_emojis (
    file_unique_id TEXT PRIMARY KEY,
    emojis         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS payments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    pack_id    INTEGER,
    pack_name  TEXT,
    amount     INTEGER NOT NULL,
    charge_id  TEXT NOT NULL UNIQUE,
    created_at INTEGER NOT NULL,
    refunded   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    outgoing   INTEGER NOT NULL,         -- 1 = ответ админа, 0 = от юзера
    kind       TEXT NOT NULL,            -- text/photo/sticker/video/animation/voice/video_note/audio/document
    text       TEXT,                     -- HTML
    file_id    TEXT,
    thumb_id   TEXT,
    fmt        TEXT,                     -- формат стикера
    file_name  TEXT,
    created_at INTEGER NOT NULL,
    read       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS messages_user ON messages(user_id, id);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# колонки, которые добавлялись после первой версии (миграция существующей базы)
MIGRATIONS = {
    "packs": {
        "base_title": "TEXT",
        "paid": "INTEGER NOT NULL DEFAULT 0",
    },
    "users": {
        "created_at": "INTEGER",
        "blocked": "INTEGER NOT NULL DEFAULT 0",
        "first_name": "TEXT",
        "username": "TEXT",
        "mode": "TEXT",  # NULL — обычный режим, chat — спамблок, capture:<цель> — админ ловит пост
    },
}

SCREENS_VERSION = 2

DEFAULT_SETTINGS: dict[str, Any] = {
    "price": 15,
    "suffix": " || @stikcerpack_bot by @xmapket",
    # оформление бота — iOS-иконки, твои эмодзи — редкими акцентами {xm}, буквы — из символов
    "icon_pack": "tgmacicons",
    "accent_pack": "XMAPKETemojis, XMAPKETsymbols",
    "icon_map": {},
    # иконки мини-апки — отдельный набор в стиле iOS-интерфейса Telegram
    "app_icon_pack": "tgmacicons",
    "app_icon_map": {},
    "bot_custom_emoji": True,
    "broadcast": {
        "enabled": False,
        "text": "",
        "photo_file_id": None,
        "button_text": "",
        "button_url": "",
        "weekday": 0,  # 0 = понедельник
        "hour": 12,
        "last_sent_at": None,
        "last_result": None,
    },
}


class DB:
    def __init__(self, path: str):
        self.path = path
        self.conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.executescript(SCHEMA)
        for table, cols in MIGRATIONS.items():
            existing = {r["name"] for r in await self._all(f"PRAGMA table_info({table})")}
            for col, decl in cols.items():
                if col not in existing:
                    await self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        await self.conn.execute("UPDATE packs SET base_title=title WHERE base_title IS NULL")
        # прошлый дефолт ставил наборы XMAPKET основными — теперь основное оформление iOS-иконками
        await self.conn.execute(
            "UPDATE settings SET value=? WHERE key='icon_pack' AND value=?",
            (json.dumps(DEFAULT_SETTINGS["icon_pack"]), json.dumps("XMAPKETemojis, XMAPKETsymbols")),
        )
        # тексты экранов по умолчанию сильно поменялись — старые сохранённые сбрасываем, баннеры оставляем
        row = await self._one("SELECT value FROM settings WHERE key='screens_version'")
        if not row or json.loads(row["value"]) < SCREENS_VERSION:
            stored = await self._one("SELECT value FROM settings WHERE key='screens'")
            if stored:
                screens = json.loads(stored["value"])
                for scr in screens.values():
                    scr.pop("text", None)
                await self.conn.execute(
                    "UPDATE settings SET value=? WHERE key='screens'", (json.dumps(screens, ensure_ascii=False),)
                )
            await self.conn.execute(
                "INSERT INTO settings(key, value) VALUES ('screens_version', ?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(SCREENS_VERSION),)
            )
        await self.conn.commit()

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()

    async def _all(self, sql: str, *args: Any) -> list[dict]:
        async with self.conn.execute(sql, args) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def _one(self, sql: str, *args: Any) -> dict | None:
        async with self.conn.execute(sql, args) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def _exec(self, sql: str, *args: Any) -> int:
        cur = await self.conn.execute(sql, args)
        await self.conn.commit()
        return cur.lastrowid

    # --- packs ---
    async def add_pack(self, name: str, owner_id: int, title: str, base_title: str) -> dict:
        pid = await self._exec(
            "INSERT INTO packs(name, owner_id, title, base_title, created_at) VALUES (?,?,?,?,?)",
            name, owner_id, title, base_title, int(time.time()),
        )
        return await self.get_pack_by_id(pid)

    async def get_packs(self, owner_id: int) -> list[dict]:
        return await self._all(
            "SELECT * FROM packs WHERE owner_id=? ORDER BY created_at DESC, id DESC", owner_id
        )

    async def get_pack(self, name: str) -> dict | None:
        return await self._one("SELECT * FROM packs WHERE name=?", name)

    async def get_pack_by_id(self, pid: int) -> dict | None:
        return await self._one("SELECT * FROM packs WHERE id=?", pid)

    async def set_pack_titles(self, pid: int, title: str, base_title: str) -> None:
        await self._exec("UPDATE packs SET title=?, base_title=? WHERE id=?", title, base_title, pid)

    async def set_pack_paid(self, pid: int, paid: bool) -> None:
        await self._exec("UPDATE packs SET paid=? WHERE id=?", int(paid), pid)

    async def rename_pack(self, pid: int, name: str) -> None:
        await self._exec("UPDATE packs SET name=? WHERE id=?", name, pid)

    async def delete_pack(self, name: str) -> None:
        pack = await self.get_pack(name)
        if not pack:
            return
        await self._exec("UPDATE users SET active_pack=NULL WHERE active_pack=?", pack["id"])
        await self._exec("DELETE FROM packs WHERE id=?", pack["id"])

    # --- users ---
    async def get_user(self, user_id: int) -> dict:
        row = await self._one("SELECT * FROM users WHERE user_id=?", user_id)
        if row is None:
            await self._exec(
                "INSERT OR IGNORE INTO users(user_id, created_at) VALUES (?,?)", user_id, int(time.time())
            )
            row = await self._one("SELECT * FROM users WHERE user_id=?", user_id)
        return row

    async def touch_user(self, user_id: int, first_name: str | None = None, username: str | None = None) -> None:
        """Юзер что-то написал: заводим, обновляем имя, снимаем пометку «заблокировал»."""
        user = await self.get_user(user_id)
        if user["blocked"] or user["first_name"] != first_name or user["username"] != username:
            await self._exec(
                "UPDATE users SET blocked=0, first_name=?, username=? WHERE user_id=?",
                first_name, username, user_id,
            )

    async def set_mode(self, user_id: int, mode: str | None) -> None:
        await self.get_user(user_id)
        await self._exec("UPDATE users SET mode=? WHERE user_id=?", mode, user_id)

    async def set_blocked(self, user_id: int) -> None:
        await self._exec("UPDATE users SET blocked=1 WHERE user_id=?", user_id)

    async def broadcast_user_ids(self) -> list[int]:
        rows = await self._all("SELECT user_id FROM users WHERE blocked=0 ORDER BY user_id")
        return [r["user_id"] for r in rows]

    async def set_active(self, user_id: int, pack_id: int | None) -> None:
        await self.get_user(user_id)
        await self._exec("UPDATE users SET active_pack=? WHERE user_id=?", pack_id, user_id)

    async def set_pending(self, user_id: int, title: str | None) -> None:
        await self.get_user(user_id)
        await self._exec("UPDATE users SET pending_title=? WHERE user_id=?", title, user_id)

    # --- inbox ---
    async def add_inbox(
        self, user_id: int, file_id: str, file_unique_id: str, thumb_file_id: str | None,
        fmt: str, emoji: str, set_name: str | None,
    ) -> int:
        return await self._exec(
            "INSERT INTO inbox(user_id, file_id, file_unique_id, thumb_file_id, format, emoji, set_name, created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            user_id, file_id, file_unique_id, thumb_file_id, fmt, emoji, set_name, int(time.time()),
        )

    async def get_inbox(self, user_id: int) -> list[dict]:
        return await self._all("SELECT * FROM inbox WHERE user_id=? ORDER BY id", user_id)

    async def get_inbox_item(self, item_id: int) -> dict | None:
        return await self._one("SELECT * FROM inbox WHERE id=?", item_id)

    async def get_inbox_items(self, user_id: int, ids: list[int]) -> list[dict]:
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        rows = await self._all(
            f"SELECT * FROM inbox WHERE user_id=? AND id IN ({marks})", user_id, *ids
        )
        by_id = {r["id"]: r for r in rows}
        return [by_id[i] for i in ids if i in by_id]  # сохраняем порядок, как выбрал юзер

    async def delete_inbox(self, user_id: int, ids: list[int]) -> None:
        if not ids:
            return
        marks = ",".join("?" * len(ids))
        await self._exec(f"DELETE FROM inbox WHERE user_id=? AND id IN ({marks})", user_id, *ids)

    # --- emojis ---
    async def get_emojis(self, uids: list[str]) -> dict[str, list[str]]:
        if not uids:
            return {}
        marks = ",".join("?" * len(uids))
        rows = await self._all(
            f"SELECT * FROM sticker_emojis WHERE file_unique_id IN ({marks})", *uids
        )
        return {r["file_unique_id"]: json.loads(r["emojis"]) for r in rows}

    async def set_emojis(self, uid: str, emojis: list[str]) -> None:
        await self._exec(
            "INSERT INTO sticker_emojis(file_unique_id, emojis) VALUES (?,?)"
            " ON CONFLICT(file_unique_id) DO UPDATE SET emojis=excluded.emojis",
            uid, json.dumps(emojis, ensure_ascii=False),
        )

    # --- payments ---
    async def add_payment(self, user_id: int, pack: dict, amount: int, charge_id: str) -> None:
        await self._exec(
            "INSERT OR IGNORE INTO payments(user_id, pack_id, pack_name, amount, charge_id, created_at)"
            " VALUES (?,?,?,?,?,?)",
            user_id, pack["id"], pack["name"], amount, charge_id, int(time.time()),
        )

    async def get_payment(self, pid: int) -> dict | None:
        return await self._one("SELECT * FROM payments WHERE id=?", pid)

    async def set_refunded(self, pid: int) -> None:
        await self._exec("UPDATE payments SET refunded=1 WHERE id=?", pid)

    async def recent_payments(self, limit: int = 30) -> list[dict]:
        return await self._all(
            "SELECT p.*, k.base_title AS pack_title FROM payments p"
            " LEFT JOIN packs k ON k.id = p.pack_id ORDER BY p.id DESC LIMIT ?", limit,
        )

    async def stats(self) -> dict:
        one = lambda sql: self._one(sql)  # noqa: E731
        return {
            "users": (await one("SELECT COUNT(*) c FROM users"))["c"],
            "blocked": (await one("SELECT COUNT(*) c FROM users WHERE blocked=1"))["c"],
            "packs": (await one("SELECT COUNT(*) c FROM packs"))["c"],
            "paid_packs": (await one("SELECT COUNT(*) c FROM packs WHERE paid=1"))["c"],
            "stars": (await one("SELECT COALESCE(SUM(amount),0) c FROM payments WHERE refunded=0"))["c"],
            "payments": (await one("SELECT COUNT(*) c FROM payments WHERE refunded=0"))["c"],
        }

    # --- чат (спамблок) ---
    async def add_message(self, user_id: int, outgoing: bool, kind: str, text: str | None = None,
                          file_id: str | None = None, thumb_id: str | None = None,
                          fmt: str | None = None, file_name: str | None = None) -> int:
        return await self._exec(
            "INSERT INTO messages(user_id, outgoing, kind, text, file_id, thumb_id, fmt, file_name, created_at, read)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            user_id, int(outgoing), kind, text, file_id, thumb_id, fmt, file_name, int(time.time()), int(outgoing),
        )

    async def get_messages(self, user_id: int, after: int = 0, limit: int = 200) -> list[dict]:
        rows = await self._all(
            "SELECT * FROM messages WHERE user_id=? AND id>? ORDER BY id DESC LIMIT ?", user_id, after, limit
        )
        return rows[::-1]

    async def mark_read(self, user_id: int) -> None:
        await self._exec("UPDATE messages SET read=1 WHERE user_id=? AND read=0", user_id)

    async def dialogs(self) -> list[dict]:
        return await self._all(
            "SELECT m.*, u.first_name, u.username, u.blocked,"
            " (SELECT COUNT(*) FROM messages x WHERE x.user_id=m.user_id AND x.read=0) AS unread"
            " FROM messages m JOIN (SELECT user_id, MAX(id) mid FROM messages GROUP BY user_id) t ON t.mid=m.id"
            " LEFT JOIN users u ON u.user_id=m.user_id ORDER BY m.id DESC"
        )

    async def unread_total(self) -> int:
        return (await self._one("SELECT COUNT(*) c FROM messages WHERE read=0"))["c"]

    # --- settings ---
    async def get_settings(self) -> dict:
        rows = await self._all("SELECT * FROM settings")
        result = copy.deepcopy(DEFAULT_SETTINGS)
        for r in rows:
            value = json.loads(r["value"])
            if isinstance(result.get(r["key"]), dict) and isinstance(value, dict):
                result[r["key"]].update(value)
            else:
                result[r["key"]] = value
        return result

    async def set_setting(self, key: str, value: Any) -> None:
        await self._exec(
            "INSERT INTO settings(key, value) VALUES (?,?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            key, json.dumps(value, ensure_ascii=False),
        )
