"""使用 LangChain create_agent 构建一个简单的命令行代理。"""

import argparse
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_openai import ChatOpenAI


@tool
def multiply(a: float, b: float) -> float:
    """计算两个数字的乘积；需要乘法计算时调用此工具。"""
    return a * b


@tool
def get_current_time() -> str:
    """获取运行程序的电脑的当前本地时间，包含 UTC 时区偏移。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def build_agent():
    """从项目目录的 .env 或已有环境变量读取配置并创建代理。"""
    load_dotenv(Path(__file__).with_name(".env"))
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key or api_key == "your_api_key_here":
        raise ValueError("请先复制 .env.example 为 .env，并填写 OPENAI_API_KEY。")

    model = ChatOpenAI(
        model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
        api_key=api_key,
        base_url=os.getenv("OPENAI_BASE_URL") or None,
        timeout=60,
        max_retries=2,
    )
    return create_agent(
        model=model,
        tools=[multiply, get_current_time],
        system_prompt=(
            "你是一个简洁、友好的中文助手。"
            "涉及乘法时使用 multiply 工具，涉及当前时间时使用 get_current_time 工具。"
            "根据工具返回的真实结果回答，不要编造时间或工具输出。"
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="一个带计算和时间工具的 LangChain 代理")
    parser.add_argument(
        "question",
        nargs="?",
        default="请计算 23 乘以 47，并告诉我当前时间。",
        help="要向代理提出的问题（含空格时请用引号包裹）",
    )
    args = parser.parse_args()
    if not args.question.strip():
        parser.error("问题不能为空")

    try:
        agent = build_agent()
        result = agent.invoke(
            {"messages": [{"role": "user", "content": args.question}]},
            config={"recursion_limit": 20},
        )
    except ValueError as exc:
        print(f"配置或输入错误：{exc}")
        return 1
    except Exception as exc:
        # 不直接输出服务端异常正文，避免将请求信息或凭据写入终端。
        print(f"代理运行失败（{type(exc).__name__}），请检查网络、模型名及 API 配置。")
        return 1

    print(result["messages"][-1].text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
