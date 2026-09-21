"""SQLite 持久化：每次操作使用独立连接，所有消息带群 ID。"""

import hashlib
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, nickname TEXT NOT NULL, token_hash TEXT UNIQUE NOT NULL
                );
                CREATE TABLE IF NOT EXISTS rooms (
                    id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL
                );
                CREATE TABLE IF NOT EXISTS members (
                    room_id INTEGER REFERENCES rooms(id), user_id TEXT REFERENCES users(id),
                    PRIMARY KEY (room_id, user_id)
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY, room_id INTEGER NOT NULL REFERENCES rooms(id),
                    sender_id TEXT, nickname TEXT NOT NULL, role TEXT NOT NULL,
                    content TEXT NOT NULL, created_at TEXT NOT NULL, reply_to INTEGER
                );
                CREATE INDEX IF NOT EXISTS messages_room ON messages(room_id, id);
                CREATE TABLE IF NOT EXISTS images (
                    id TEXT PRIMARY KEY, room_id INTEGER NOT NULL REFERENCES rooms(id),
                    owner_id TEXT NOT NULL REFERENCES users(id), data BLOB NOT NULL,
                    created_at TEXT NOT NULL
                );
            """)
            # 原有数据库原地增加可空列，保留全部消息。
            columns = {row["name"] for row in db.execute("PRAGMA table_info(messages)")}
            if "image_id" not in columns:
                db.execute("ALTER TABLE messages ADD COLUMN image_id TEXT REFERENCES images(id)")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def create_user(self, nickname):
        token = secrets.token_urlsafe(32)
        user = {"id": secrets.token_hex(12), "nickname": nickname}
        with self.connect() as db:
            db.execute("INSERT INTO users VALUES (?, ?, ?)",
                       (user["id"], nickname, hashlib.sha256(token.encode()).hexdigest()))
        return {**user, "token": token}

    def authenticate(self, token):
        with self.connect() as db:
            row = db.execute("SELECT id, nickname FROM users WHERE token_hash=?",
                             (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        return dict(row) if row else None

    def join_room(self, name, user_id):
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO rooms(name) VALUES (?)", (name,))
            room = dict(db.execute("SELECT * FROM rooms WHERE name=?", (name,)).fetchone())
            db.execute("INSERT OR IGNORE INTO members VALUES (?, ?)", (room["id"], user_id))
        return room

    def is_member(self, room_id, user_id):
        with self.connect() as db:
            return db.execute("SELECT 1 FROM members WHERE room_id=? AND user_id=?",
                              (room_id, user_id)).fetchone() is not None

    def rooms_for(self, user_id):
        with self.connect() as db:
            rows = db.execute(
                "SELECT r.id,r.name,MAX(m.id) AS last_message_id FROM rooms r "
                "JOIN members u ON u.room_id=r.id LEFT JOIN messages m ON m.room_id=r.id "
                "WHERE u.user_id=? GROUP BY r.id ORDER BY last_message_id DESC,r.id DESC", (user_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    def message(self, room_id, message_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM messages WHERE room_id=? AND id=?",
                             (room_id, message_id)).fetchone()
            return self._decorate(db, row) if row else None

    def _decorate(self, db, row):
        item = dict(row)
        item["quote"] = None
        if item["reply_to"]:
            quote = db.execute(
                "SELECT id,nickname,content,image_id FROM messages WHERE room_id=? AND id=?",
                (item["room_id"], item["reply_to"]),
            ).fetchone()
            if quote:
                item["quote"] = dict(quote)
                item["quote"]["content"] = item["quote"]["content"][:500]
        return item

    def add_image(self, room_id, user_id, data):
        image_id = secrets.token_hex(16)
        with self.connect() as db:
            # 未发送的附件最多保留一天，避免用户取消选择后无限积累。
            db.execute("DELETE FROM images WHERE created_at < ? AND id NOT IN "
                       "(SELECT image_id FROM messages WHERE image_id IS NOT NULL)",
                       (datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() - 86400,
                                               timezone.utc).isoformat(),))
            db.execute("INSERT INTO images VALUES (?,?,?,?,?)",
                       (image_id, room_id, user_id, data, datetime.now(timezone.utc).isoformat()))
        return image_id

    def get_image(self, room_id, image_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM images WHERE room_id=? AND id=?", (room_id, image_id)).fetchone()
        return dict(row) if row else None

    def add_message(self, room_id, content, *, user=None, reply_to=None, role=None, image_id=None):
        with self.connect() as db:
            cursor = db.execute(
                "INSERT INTO messages(room_id,sender_id,nickname,role,content,created_at,reply_to,image_id) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (room_id, user["id"] if user else None, user["nickname"] if user else "AI",
                 role or ("user" if user else "assistant"), content,
                 datetime.now(timezone.utc).isoformat(), reply_to, image_id),
            )
            return self._decorate(db, db.execute("SELECT * FROM messages WHERE id=?", (cursor.lastrowid,)).fetchone())

    def history(self, room_id, *, limit=100, through=None):
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM messages WHERE room_id=? AND id<=? ORDER BY id DESC LIMIT ?",
                (room_id, through if through is not None else 9223372036854775807, limit),
            ).fetchall()
            return [self._decorate(db, row) for row in reversed(rows)]
