"""验证运行中服务的引用、图片、群列表与介绍命令，不调用模型。"""

import asyncio
import io
import json
from datetime import datetime

import httpx
from PIL import Image
from websockets.asyncio.client import connect


async def main():
    async with asyncio.timeout(30), httpx.AsyncClient(base_url="http://127.0.0.1:8000", trust_env=False) as client:
        for path in ("/", "/static/app.js?v=2", "/static/style.css?v=2"):
            response = await client.get(path)
            response.raise_for_status()
        people = []
        room_name = "功能验收-" + datetime.now().strftime("%Y%m%d-%H%M%S")
        for name in ("功能验收甲", "功能验收乙"):
            response = await client.post("/api/sessions", json={"nickname": name})
            response.raise_for_status()
            user = response.json()
            user["headers"] = {"Authorization": f"Bearer {user['token']}"}
            response = await client.post("/api/rooms", headers=user["headers"], json={"name": room_name})
            response.raise_for_status()
            room = response.json()
            people.append(user)
        path = f"/api/rooms/{room['id']}"
        url = f"ws://127.0.0.1:8000/ws/rooms/{room['id']}"
        async with connect(url, proxy=None) as a, connect(url, proxy=None) as b:
            for ws, person in zip((a, b), people):
                await ws.send(json.dumps({"token": person["token"]}))
                assert json.loads(await ws.recv())["type"] == "history"

            async def exchange(payload):
                await a.send(json.dumps(payload))
                first, second = await asyncio.gather(a.recv(), b.recv())
                assert json.loads(first) == json.loads(second)
                return json.loads(first)["message"]

            original = await exchange({"content": "引用这条测试消息"})
            quoted = await exchange({"content": "已引用", "reply_to": original["id"]})
            assert quoted["quote"]["content"] == original["content"]
            print("PASS: 双用户引用广播", flush=True)
            picture = io.BytesIO()
            Image.new("RGB", (32, 32), "green").save(picture, format="PNG")
            response = await client.post(f"{path}/images", headers=people[0]["headers"], content=picture.getvalue())
            response.raise_for_status()
            image_id = response.json()["id"]
            posted = await exchange({"image_id": image_id})
            assert posted["image_id"] == image_id
            response = await client.get(f"{path}/images/{image_id}", headers=people[1]["headers"])
            response.raise_for_status()
            assert response.headers["content-type"] == "image/jpeg"
            print("PASS: 图片上传、双端广播、成员下载", flush=True)
            await exchange({"content": "/介绍"})
            first, second = await asyncio.gather(a.recv(), b.recv())
            assert json.loads(first) == json.loads(second)
            assert "一起聊" in json.loads(first)["message"]["content"]
            print("PASS: 软件介绍命令", flush=True)
        response = await client.get("/api/rooms", headers=people[0]["headers"])
        response.raise_for_status()
        assert any(item["id"] == room["id"] for item in response.json())
        response = await client.get(f"{path}/messages", headers=people[0]["headers"])
        response.raise_for_status()
        assert len(response.json()) == 5
        print("PASS: 群列表与持久历史", flush=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}（未输出认证信息）")
        raise SystemExit(1)
