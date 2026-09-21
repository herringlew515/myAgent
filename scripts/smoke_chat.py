"""对运行中的服务做双用户验证；--live-ai 会发送一次真实模型请求。"""

import argparse
import asyncio
import json
from datetime import datetime

import httpx
from websockets.asyncio.client import connect


async def receive(ws, role):
    while True:
        event = json.loads(await ws.recv())
        if event["type"] == "message" and event["message"]["role"] in role:
            return event["message"]


async def smoke(url, live_ai):
    async with asyncio.timeout(110):
        async with httpx.AsyncClient(base_url=url, trust_env=False) as client:
            page = await client.get("/")
            page.raise_for_status()
            assert "一起聊" in page.text
            room_name = "验收-" + datetime.now().strftime("%Y%m%d-%H%M%S")
            users = []
            for nickname in ("验收小王", "验收小李"):
                response = await client.post("/api/sessions", json={"nickname": nickname})
                response.raise_for_status()
                user = response.json()
                response = await client.post("/api/rooms", json={"name": room_name},
                                             headers={"Authorization": f"Bearer {user['token']}"})
                response.raise_for_status()
                room = response.json()
                users.append(user)
            ws_url = url.replace("http", "ws", 1) + f"/ws/rooms/{room['id']}"
            async with connect(ws_url, proxy=None) as a, connect(ws_url, proxy=None) as b:
                for ws, user in zip((a, b), users):
                    await ws.send(json.dumps({"token": user["token"]}))
                    assert json.loads(await ws.recv())["type"] == "history"
                await a.send(json.dumps({"content": "请记住两个数字：23 和 47。"}))
                first = await receive(a, {"user"})
                assert first == await receive(b, {"user"})
                await b.send(json.dumps({"content": "@AI 请使用乘法工具计算刚才两个数字的乘积。" if live_ai else "收到，我们继续聊。"}))
                second = await receive(a, {"user"})
                assert second == await receive(b, {"user"})
                print("PASS: HTTP 页面、双用户 WebSocket 消息同步", flush=True)
                if live_ai:
                    reply = await receive(a, {"assistant", "system"})
                    assert reply == await receive(b, {"assistant", "system"})
                    if reply["role"] != "assistant":
                        raise RuntimeError("AI 返回失败提示，请检查模型配置和网络")
                    assert reply["reply_to"] == second["id"]
                    assert "1081" in reply["content"].replace(",", "").replace("，", "")
                    print("PASS: 真实 AI 读取另一位用户的上下文并返回 1081，两个用户均收到回答", flush=True)
            history = await client.get(f"/api/rooms/{room['id']}/messages",
                                       headers={"Authorization": f"Bearer {users[0]['token']}"})
            history.raise_for_status()
            assert len(history.json()) == (3 if live_ai else 2)
            print("PASS: HTTP 历史包含完整对话", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--live-ai", action="store_true", help="调用真实模型，可能产生 API 费用")
    args = parser.parse_args()
    try:
        asyncio.run(smoke(args.url.rstrip("/"), args.live_ai))
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}（未输出服务端或认证信息）", flush=True)
        raise SystemExit(1)
