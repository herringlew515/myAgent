"""使用 LangChain create_agent 构建一个简单的命令行代理。"""

import argparse
from agent import build_agent


def run_chat(agent) -> int:
    """在内存中保留完整消息历史，成功完成一轮后再更新历史。"""
    messages = []
    print("开始对话，输入 exit 或 quit 退出；输入 /clear 清空本次对话。")
    while True:
        try:
            question = input("你：").strip()
            if question.lower() in {"exit", "quit"}:
                break
            if not question:
                continue
            if question == "/clear":
                messages = []
                print("已清空本次对话。")
                continue
            try:
                result = agent.invoke(
                    {"messages": [*messages, {"role": "user", "content": question}]},
                    config={"recursion_limit": 20},
                )
                reply = result["messages"][-1].text
            except Exception as exc:
                print(f"本轮对话失败（{type(exc).__name__}），请检查网络、模型名及 API 配置后重试。")
                continue
            messages = result["messages"]
            print(f"助手：{reply}")
        except (EOFError, KeyboardInterrupt):
            print()
            break
    print("对话已结束。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="一个带计算和时间工具的 LangChain 代理")
    parser.add_argument(
        "question",
        nargs="?",
        help="可选：单次提问后退出；不传问题则进入多轮对话（含空格时请用引号包裹）",
    )
    args = parser.parse_args()
    if args.question is not None and not args.question.strip():
        parser.error("问题不能为空")

    try:
        agent = build_agent()
        if args.question is None:
            return run_chat(agent)
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
