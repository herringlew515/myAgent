"""使用独立临时数据库和假模型验证多人聊天，不发送付费模型请求。"""

import asyncio
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from starlette.websockets import WebSocketDisconnect
from PIL import Image

from server import create_app
from storage import Store


class FakeAgent:
    def __init__(self):
        self.calls = []
        self.active = 0
        self.max_active = 0

    async def ainvoke(self, payload, config):
        self.calls.append(payload["messages"])
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.05)
            text = json.loads(payload["messages"][-1]["content"])["text"]
            if "失败" in text:
                raise RuntimeError("secret-provider-detail")
            return {"messages": [AIMessage(content=f"收到：{text}")]}
        finally:
            self.active -= 1


class GroupChatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "chat.db"
        self.agent = FakeAgent()
        self.app = create_app(self.path, agent_factory=lambda: self.agent)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.a = self.user("小王")
        self.b = self.user("小李")
        self.room = self.join(self.a, "周末计划")
        self.join(self.b, "周末计划")

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.tmp.cleanup()

    def user(self, nickname):
        response = self.client.post("/api/sessions", json={"nickname": nickname})
        self.assertEqual(response.status_code, 201)
        return response.json()

    def headers(self, user):
        return {"Authorization": f"Bearer {user['token']}"}

    def join(self, user, name):
        response = self.client.post("/api/rooms", headers=self.headers(user), json={"name": name})
        self.assertEqual(response.status_code, 200)
        return response.json()

    def auth(self, ws, user):
        ws.send_json({"token": user["token"]})
        event = ws.receive_json()
        self.assertEqual(event["type"], "history")
        return event["messages"]

    def receive_message(self, ws, role):
        for _ in range(30):
            event = ws.receive_json()
            if event["type"] == "message" and event["message"]["role"] == role:
                return event["message"]
        self.fail(f"没有收到 {role} 消息")

    def test_two_users_broadcast_and_refresh_and_db_persistence(self):
        route = f"/ws/rooms/{self.room['id']}"
        with self.client.websocket_connect(route) as a, self.client.websocket_connect(route) as b:
            self.auth(a, self.a)
            self.auth(b, self.b)
            a.send_json({"content": "周六去爬山", "sender_id": self.b["id"]})
            first = self.receive_message(a, "user")
            self.assertEqual(first, self.receive_message(b, "user"))
            self.assertEqual(first["sender_id"], self.a["id"])
            b.send_json({"content": "四点前回来"})
            self.assertEqual(self.receive_message(a, "user"), self.receive_message(b, "user"))
        with self.client.websocket_connect(route) as refreshed:
            history = self.auth(refreshed, self.a)
            self.assertEqual([m["content"] for m in history], ["周六去爬山", "四点前回来"])
        self.assertEqual(Store(self.path).history(self.room["id"]), history)
        self.assertEqual(self.agent.calls, [])

    def test_ai_context_isolation_identity_and_trigger_cutoff(self):
        other = self.join(self.a, "另一群")
        with self.client.websocket_connect(f"/ws/rooms/{other['id']}") as ws:
            self.auth(ws, self.a)
            ws.send_json({"content": "另一个群的秘密"})
            self.receive_message(ws, "user")
        with self.client.websocket_connect(f"/ws/rooms/{self.room['id']}") as a, \
                self.client.websocket_connect(f"/ws/rooms/{self.room['id']}") as b:
            self.auth(a, self.a)
            self.auth(b, self.b)
            a.send_json({"content": "周六爬山"})
            self.receive_message(a, "user")
            self.receive_message(b, "user")
            b.send_json({"content": "@AI总结一下"})
            trigger = self.receive_message(b, "user")
            self.receive_message(a, "user")
            answer = self.receive_message(a, "assistant")
            self.assertEqual(answer, self.receive_message(b, "assistant"))
            self.assertEqual(answer["reply_to"], trigger["id"])
        context = [json.loads(m["content"]) for m in self.agent.calls[0]]
        self.assertEqual([m["nickname"] for m in context], ["小王", "小李"])
        self.assertEqual([m["text"] for m in context], ["周六爬山", "@AI总结一下"])

    def test_ai_requests_ordered_and_regular_chat_continues_after_failure(self):
        with self.client.websocket_connect(f"/ws/rooms/{self.room['id']}") as ws:
            self.auth(ws, self.a)
            ws.send_json({"content": "@AI 失败"})
            first = self.receive_message(ws, "user")
            ws.send_json({"content": "@AI 第二条"})
            second = self.receive_message(ws, "user")
            failure = self.receive_message(ws, "system")
            success = self.receive_message(ws, "assistant")
            self.assertEqual(failure["reply_to"], first["id"])
            self.assertNotIn("secret-provider-detail", failure["content"])
            self.assertEqual(success["reply_to"], second["id"])
            ws.send_json({"content": "继续聊天"})
            self.assertEqual(self.receive_message(ws, "user")["content"], "继续聊天")
        self.assertEqual(self.agent.max_active, 1)
        second_context = [json.loads(m["content"])["text"] for m in self.agent.calls[1]]
        self.assertEqual(second_context, ["@AI 失败", "@AI 第二条"])

    def test_http_and_websocket_membership_and_origin_checks(self):
        outsider = self.user("群外成员")
        path = f"/api/rooms/{self.room['id']}/messages"
        self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.client.get(path, headers=self.headers(outsider)).status_code, 403)
        self.assertEqual(self.client.get(path, headers=self.headers(self.a)).status_code, 200)
        for token in ("invalid", outsider["token"]):
            with self.client.websocket_connect(f"/ws/rooms/{self.room['id']}") as ws:
                ws.send_json({"token": token})
                with self.assertRaises(WebSocketDisconnect) as caught:
                    ws.receive_json()
                self.assertEqual(caught.exception.code, 1008)
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect(f"/ws/rooms/{self.room['id']}",
                                               headers={"origin": "https://untrusted.example"}):
                pass

    def test_invalid_messages_are_rejected_without_disconnect(self):
        with self.client.websocket_connect(f"/ws/rooms/{self.room['id']}") as ws:
            self.auth(ws, self.a)
            for raw in ('{', '{"content":"  "}', json.dumps({"content": "字" * 4001}), '[]'):
                ws.send_text(raw)
                self.assertEqual(ws.receive_json()["type"], "error")
            ws.send_json({"content": "恢复正常"})
            self.assertEqual(self.receive_message(ws, "user")["content"], "恢复正常")
        self.assertEqual(len(Store(self.path).history(self.room["id"])), 1)

    def test_page_assets_and_input_validation(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/static/app.js").status_code, 200)
        for nickname in (" ", "x" * 25, "AI"):
            self.assertEqual(self.client.post("/api/sessions", json={"nickname": nickname}).status_code, 422)
        self.assertEqual(self.client.post("/api/rooms", headers=self.headers(self.a),
                                          json={"name": " "}).status_code, 422)

    def test_quote_broadcast_persists_and_cross_room_reference_rejected(self):
        other = self.join(self.a, "隔离群")
        private = self.app.state.store.add_message(other["id"], "其他群内容", user=self.a)
        route = f"/ws/rooms/{self.room['id']}"
        with self.client.websocket_connect(route) as a, self.client.websocket_connect(route) as b:
            self.auth(a, self.a)
            self.auth(b, self.b)
            a.send_json({"content": "下午四点集合"})
            first = self.receive_message(a, "user")
            self.receive_message(b, "user")
            b.send_json({"content": "收到", "reply_to": first["id"]})
            quoted = self.receive_message(b, "user")
            self.assertEqual(self.receive_message(a, "user"), quoted)
            self.assertEqual(quoted["quote"]["content"], "下午四点集合")
            self.assertEqual(quoted["quote"]["nickname"], "小王")
            a.send_json({"content": "跨群引用", "reply_to": private["id"]})
            self.assertEqual(a.receive_json()["type"], "error")
        with self.client.websocket_connect(route) as a:
            self.assertEqual(self.auth(a, self.a)[-1], quoted)

    def test_ai_receives_quote_outside_recent_context(self):
        store = self.app.state.store
        original = store.add_message(self.room["id"], "预算是 500 元", user=self.b)
        for _ in range(42):
            store.add_message(self.room["id"], "其他讨论", user=self.a)
        with self.client.websocket_connect(f"/ws/rooms/{self.room['id']}") as ws:
            self.auth(ws, self.a)
            ws.send_json({"content": "@AI 这个预算是多少", "reply_to": original["id"]})
            self.receive_message(ws, "assistant")
        context = self.agent.calls[0]
        self.assertEqual(len(context), 40)
        last = json.loads(context[-1]["content"])
        self.assertEqual(last["quoted_message"]["content"], "预算是 500 元")

    def test_room_list_and_history_pagination_are_scoped_and_persistent(self):
        other = self.join(self.a, "只属于小王的列表")
        for i in range(105):
            self.app.state.store.add_message(self.room["id"], f"message {i}", user=self.a)
        rooms_a = self.client.get("/api/rooms", headers=self.headers(self.a)).json()
        rooms_b = self.client.get("/api/rooms", headers=self.headers(self.b)).json()
        self.assertEqual({r["id"] for r in rooms_a}, {self.room["id"], other["id"]})
        self.assertEqual([r["id"] for r in rooms_b], [self.room["id"]])
        self.assertEqual(self.client.get("/api/rooms").status_code, 401)
        url = f"/api/rooms/{self.room['id']}/messages"
        recent = self.client.get(url, headers=self.headers(self.a)).json()
        older = self.client.get(url, params={"before": recent[0]["id"]}, headers=self.headers(self.a)).json()
        self.assertEqual(len(recent), 100)
        self.assertEqual(len(older), 5)
        self.assertEqual(older[0]["content"], "message 0")
        self.assertLess(older[-1]["id"], recent[0]["id"])
        self.assertEqual(Store(self.path).rooms_for(self.a["id"]), rooms_a)

    def image_bytes(self):
        stream = io.BytesIO()
        Image.new("RGB", (20, 20), "green").save(stream, format="PNG")
        return stream.getvalue()

    def test_image_upload_broadcast_download_permissions_and_persistence(self):
        url = f"/api/rooms/{self.room['id']}/images"
        response = self.client.post(url, content=self.image_bytes(), headers=self.headers(self.a))
        self.assertEqual(response.status_code, 201)
        image_id = response.json()["id"]
        route = f"/ws/rooms/{self.room['id']}"
        with self.client.websocket_connect(route) as a, self.client.websocket_connect(route) as b:
            self.auth(a, self.a)
            self.auth(b, self.b)
            a.send_json({"image_id": image_id})
            sent = self.receive_message(a, "user")
            self.assertEqual(sent, self.receive_message(b, "user"))
            self.assertEqual(sent["image_id"], image_id)
            self.assertEqual(sent["content"], "")
            b.send_json({"image_id": image_id})
            self.assertEqual(b.receive_json()["type"], "error")
        response = self.client.get(f"{url}/{image_id}", headers=self.headers(self.b))
        self.assertEqual(response.headers["content-type"], "image/jpeg")
        with Image.open(io.BytesIO(response.content)) as picture:
            self.assertEqual(picture.size, (20, 20))
        self.assertEqual(Store(self.path).get_image(self.room["id"], image_id)["data"], response.content)
        outsider = self.user("群外")
        self.assertEqual(self.client.get(f"{url}/{image_id}").status_code, 401)
        self.assertEqual(self.client.get(f"{url}/{image_id}", headers=self.headers(outsider)).status_code, 403)
        self.assertEqual(self.client.post(url, content=self.image_bytes(), headers=self.headers(outsider)).status_code, 403)
        other = self.join(self.a, "其他图片群")
        self.assertEqual(self.client.get(f"/api/rooms/{other['id']}/images/{image_id}",
                                         headers=self.headers(self.a)).status_code, 404)
        with self.client.websocket_connect(f"/ws/rooms/{other['id']}") as ws:
            self.auth(ws, self.a)
            ws.send_json({"image_id": image_id})
            self.assertEqual(ws.receive_json()["type"], "error")

    def test_invalid_images_rejected_and_old_intro_commands_are_plain_messages(self):
        url = f"/api/rooms/{self.room['id']}/images"
        for body in (b"", b"<svg></svg>", self.image_bytes()[:20]):
            self.assertEqual(self.client.post(url, content=body, headers=self.headers(self.a)).status_code, 422)
        self.assertEqual(self.client.post(url, content=b"x" * (5 * 1024 * 1024 + 1),
                                          headers=self.headers(self.a)).status_code, 413)
        route = f"/ws/rooms/{self.room['id']}"
        with self.client.websocket_connect(route) as a, self.client.websocket_connect(route) as b:
            self.auth(a, self.a)
            self.auth(b, self.b)
            for command in ("/介绍", "/help"):
                a.send_json({"content": command})
                answer = self.receive_message(a, "user")
                self.assertEqual(answer, self.receive_message(b, "user"))
                self.assertEqual(command, answer["content"])
        self.assertEqual(self.agent.calls, [])
        history = self.app.state.store.history(self.room["id"])
        self.assertEqual([item["role"] for item in history], ["user", "user"])

    def test_existing_database_migration_preserves_messages(self):
        path = Path(self.tmp.name) / "legacy.db"
        with closing(sqlite3.connect(path)) as db:
            db.executescript("""
                CREATE TABLE messages (id INTEGER PRIMARY KEY, room_id INTEGER NOT NULL,
                    sender_id TEXT, nickname TEXT NOT NULL, role TEXT NOT NULL,
                    content TEXT NOT NULL, created_at TEXT NOT NULL, reply_to INTEGER);
                INSERT INTO messages VALUES (1,1,'old-user','老用户','user','旧消息','2026-09-16',NULL);
            """)
        migrated = Store(path)
        self.assertEqual(migrated.history(1)[0]["content"], "旧消息")
        self.assertIsNone(migrated.history(1)[0]["image_id"])
        self.assertEqual(Store(path).history(1), migrated.history(1))


if __name__ == "__main__":
    unittest.main()
