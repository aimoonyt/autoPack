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
    title      TEXT NOT NULL,
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
"""


class DB:
    def __init__(self, path: str):
        self.path = path
        self.conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.executescript(SCHEMA)
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
    async def add_pack(self, name: str, owner_id: int, title: str) -> dict:
        pid = await self._exec(
            "INSERT INTO packs(name, owner_id, title, created_at) VALUES (?,?,?,?)",
            name, owner_id, title, int(time.time()),
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

    async def set_pack_title(self, name: str, title: str) -> None:
        await self._exec("UPDATE packs SET title=? WHERE name=?", title, name)

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
            await self._exec("INSERT OR IGNORE INTO users(user_id) VALUES (?)", user_id)
            row = {"user_id": user_id, "active_pack": None, "pending_title": None}
        return row

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
