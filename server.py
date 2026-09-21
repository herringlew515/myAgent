"""运行：python -m uvicorn server:app --host 127.0.0.1 --port 8000。"""

import asyncio
import json
import re
from collections import defaultdict
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from agent import build_agent
from storage import Store
from image_upload import MAX_IMAGE_BYTES, normalize_image

ROOT = Path(__file__).parent
MENTION = re.compile(r"@ai(?![a-zA-Z0-9_])", re.IGNORECASE)


class Nickname(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    nickname: str = Field(min_length=1, max_length=24)


class RoomName(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=40)


class ChatMessage(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    content: str = Field(default="", max_length=4000)
    reply_to: int | None = Field(default=None, gt=0, strict=True)
    image_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")

    @model_validator(mode="after")
    def has_content(self):
        if not self.content and not self.image_id:
            raise ValueError("消息不能为空")
        return self


def create_app(db_path=None, agent_factory=None):
    factory = agent_factory or (lambda: build_agent(group_chat=True))

    @asynccontextmanager
    async def lifespan(app):
        app.state.store = Store(db_path or ROOT / "data" / "chat.db")
        app.state.hub = defaultdict(set)
        app.state.locks = defaultdict(asyncio.Lock)
        app.state.queues = {}
        app.state.workers = set()
        app.state.agent = None
        app.state.agent_lock = asyncio.Lock()
        yield
        for task in app.state.workers:
            task.cancel()
        await asyncio.gather(*app.state.workers, return_exceptions=True)

    app = FastAPI(title="AI 群聊", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

    def authenticate(authorization):
        token = authorization[7:] if authorization and authorization.startswith("Bearer ") else ""
        user = app.state.store.authenticate(token)
        if not user:
            raise HTTPException(401, "会话已失效，请重新进入。")
        return user

    async def broadcast(room_id, event):
        sockets = list(app.state.hub[room_id])

        async def deliver(ws):
            try:
                await asyncio.wait_for(ws.send_json(event), timeout=3)
            except Exception:
                app.state.hub[room_id].discard(ws)
                with suppress(Exception):
                    await ws.close()

        await asyncio.gather(*(deliver(ws) for ws in sockets))

    async def reply_worker(room_id, queue):
        while True:
            trigger = await queue.get()
            try:
                async with app.state.locks[room_id]:
                    await broadcast(room_id, {"type": "ai_status", "status": "thinking", "reply_to": trigger})
                history = app.state.store.history(room_id, limit=40, through=trigger)
                messages = []
                for item in history:
                    if item["role"] == "user":
                        messages.append({"role": "user", "content": json.dumps({
                            "sender_id": item["sender_id"], "nickname": item["nickname"],
                            "text": item["content"],
                            **({"quoted_message": item["quote"]} if item["quote"] else {}),
                            **({"attachment": "用户发送了一张图片，图片内容未提供给模型。"} if item["image_id"] else {}),
                        }, ensure_ascii=False)})
                    elif item["role"] == "assistant":
                        messages.append({"role": "assistant", "content": item["content"]})
                try:
                    async with asyncio.timeout(90):
                        async with app.state.agent_lock:
                            if app.state.agent is None:
                                app.state.agent = await asyncio.to_thread(factory)
                        result = await app.state.agent.ainvoke(
                            {"messages": messages}, config={"recursion_limit": 20})
                        content = result["messages"][-1].text
                        if not isinstance(content, str) or not content.strip():
                            raise ValueError("empty response")
                    role = "assistant"
                except Exception:
                    content = "AI 暂时无法回复，请检查模型配置或网络后重新 @AI。"
                    role = "system"
                async with app.state.locks[room_id]:
                    message = app.state.store.add_message(room_id, content, reply_to=trigger, role=role)
                    await broadcast(room_id, {"type": "message", "message": message})
                    await broadcast(room_id, {"type": "ai_status", "status": "done", "reply_to": trigger})
            finally:
                queue.task_done()

    @app.get("/")
    async def index():
        return FileResponse(ROOT / "static" / "index.html")

    @app.post("/api/sessions", status_code=201)
    async def create_session(body: Nickname):
        if body.nickname.casefold() == "ai":
            raise HTTPException(422, "请使用 AI 以外的昵称。")
        return app.state.store.create_user(body.nickname)

    @app.post("/api/rooms")
    async def join_room(body: RoomName, authorization: str | None = Header(default=None)):
        user = authenticate(authorization)
        return app.state.store.join_room(body.name, user["id"])

    @app.get("/api/rooms")
    async def my_rooms(authorization: str | None = Header(default=None)):
        user = authenticate(authorization)
        return app.state.store.rooms_for(user["id"])

    @app.get("/api/rooms/{room_id}/messages")
    async def history(room_id: int, authorization: str | None = Header(default=None),
                      before: int | None = Query(default=None, gt=0)):
        user = authenticate(authorization)
        if not app.state.store.is_member(room_id, user["id"]):
            raise HTTPException(403, "请先加入该群。")
        return app.state.store.history(room_id, through=before - 1 if before else None)

    @app.post("/api/rooms/{room_id}/images", status_code=201)
    async def upload_image(room_id: int, request: Request,
                           authorization: str | None = Header(default=None)):
        user = authenticate(authorization)
        if not app.state.store.is_member(room_id, user["id"]):
            raise HTTPException(403, "请先加入该群。")
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > MAX_IMAGE_BYTES:
                raise HTTPException(413, "图片不能超过 5 MB。")
            raw.extend(chunk)
        try:
            data = await asyncio.to_thread(normalize_image, bytes(raw))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        image_id = app.state.store.add_image(room_id, user["id"], data)
        return {"id": image_id}

    @app.get("/api/rooms/{room_id}/images/{image_id}")
    async def get_image(room_id: int, image_id: str, authorization: str | None = Header(default=None)):
        user = authenticate(authorization)
        if not app.state.store.is_member(room_id, user["id"]):
            raise HTTPException(403, "请先加入该群。")
        attachment = app.state.store.get_image(room_id, image_id)
        if not attachment:
            raise HTTPException(404, "图片不存在。")
        return Response(attachment["data"], media_type="image/jpeg",
                        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @app.websocket("/ws/rooms/{room_id}")
    async def chat(ws: WebSocket, room_id: int):
        origin = ws.headers.get("origin")
        if origin:
            parsed = urlsplit(origin)
            if parsed.scheme not in ("http", "https") or parsed.netloc != ws.headers.get("host"):
                await ws.close(code=1008)
                return
        await ws.accept()
        try:
            auth = await asyncio.wait_for(ws.receive_json(), timeout=10)
            token = auth.get("token") if isinstance(auth, dict) else None
            if not isinstance(token, str) or len(token) > 200:
                await ws.close(code=1008)
                return
            user = app.state.store.authenticate(token)
            if not user or not app.state.store.is_member(room_id, user["id"]):
                await ws.close(code=1008)
                return
            async with app.state.locks[room_id]:
                await ws.send_json({"type": "history", "messages": app.state.store.history(room_id)})
                app.state.hub[room_id].add(ws)
            while True:
                raw = await ws.receive_text()
                try:
                    if len(raw) > 24000:
                        raise ValueError("payload too large")
                    body = ChatMessage.model_validate_json(raw)
                except (ValidationError, ValueError):
                    async with app.state.locks[room_id]:
                        await ws.send_json({"type": "error", "detail": "请输入 1 至 4000 字的消息。"})
                    continue
                async with app.state.locks[room_id]:
                    if body.reply_to and not app.state.store.message(room_id, body.reply_to):
                        await ws.send_json({"type": "error", "detail": "只能引用当前群中存在的消息。"})
                        continue
                    if body.image_id:
                        attachment = app.state.store.get_image(room_id, body.image_id)
                        if not attachment or attachment["owner_id"] != user["id"]:
                            await ws.send_json({"type": "error", "detail": "图片不可用，请重新上传。"})
                            continue
                    message = app.state.store.add_message(room_id, body.content, user=user,
                                                          reply_to=body.reply_to, image_id=body.image_id)
                    await broadcast(room_id, {"type": "message", "message": message})
                    if MENTION.search(body.content):
                        if room_id not in app.state.queues:
                            queue = asyncio.Queue(maxsize=10)
                            app.state.queues[room_id] = queue
                            task = asyncio.create_task(reply_worker(room_id, queue))
                            app.state.workers.add(task)
                        queue = app.state.queues[room_id]
                        if queue.full():
                            failure = app.state.store.add_message(
                                room_id, "AI 等待队列已满，请稍后重新 @AI。", role="system", reply_to=message["id"])
                            await broadcast(room_id, {"type": "message", "message": failure})
                        else:
                            queue.put_nowait(message["id"])
                            await broadcast(room_id, {"type": "ai_status", "status": "queued", "reply_to": message["id"]})
        except (WebSocketDisconnect, TimeoutError, ValueError, RuntimeError):
            pass
        finally:
            app.state.hub[room_id].discard(ws)

    return app


app = create_app()
