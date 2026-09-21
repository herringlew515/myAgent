"""验证对话状态和命令行行为，不发送模型请求。"""

import io
import unittest
from unittest.mock import Mock, patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

import test as app


class ChatTests(unittest.TestCase):
    def run_chat(self, agent, inputs):
        output = io.StringIO()
        with patch("builtins.input", side_effect=inputs), patch("sys.stdout", output):
            self.assertEqual(app.run_chat(agent), 0)
        return output.getvalue()

    def test_follow_up_preserves_tool_history_and_clear_resets_it(self):
        history = [
            HumanMessage(content="23 * 47"),
            AIMessage(content="", tool_calls=[{
                "name": "multiply", "args": {"a": 23, "b": 47}, "id": "multiply-1",
            }]),
            ToolMessage(content="1081", tool_call_id="multiply-1"),
            AIMessage(content="1081"),
        ]
        agent = Mock()
        agent.invoke.side_effect = [
            {"messages": history},
            {"messages": [*history, HumanMessage(content="再乘以 2"), AIMessage(content="2162")]},
            {"messages": [AIMessage(content="你好")]},
        ]
        output = self.run_chat(agent, ["23 * 47", "再乘以 2", "/clear", "你好", "exit"])
        calls = agent.invoke.call_args_list
        self.assertEqual(calls[1].args[0]["messages"][:-1], history)
        self.assertEqual(calls[1].args[0]["messages"][-1]["content"], "再乘以 2")
        self.assertEqual(calls[2].args[0]["messages"], [{"role": "user", "content": "你好"}])
        self.assertIn("助手：2162", output)

    def test_failed_turn_is_not_added_to_history(self):
        history = [HumanMessage(content="你好"), AIMessage(content="你好")]
        agent = Mock()
        agent.invoke.side_effect = [
            {"messages": history}, RuntimeError("private server detail"),
            {"messages": [*history, AIMessage(content="恢复成功")]},
        ]
        output = self.run_chat(agent, ["你好", "失败的问题", "重试", "quit"])
        self.assertEqual(agent.invoke.call_args_list[2].args[0]["messages"],
                         [*history, {"role": "user", "content": "重试"}])
        self.assertIn("RuntimeError", output)
        self.assertNotIn("private server detail", output)
        self.assertIn("恢复成功", output)

    def test_blank_input_and_exit_do_not_call_model(self):
        agent = Mock()
        self.run_chat(agent, ["  ", " EXIT "])
        agent.invoke.assert_not_called()

    def test_terminal_interrupts_exit_cleanly(self):
        for interrupt in (EOFError(), KeyboardInterrupt()):
            with self.subTest(interrupt=type(interrupt).__name__):
                self.run_chat(Mock(), [interrupt])

    def test_cli_default_enters_chat(self):
        with patch("sys.argv", ["test.py"]), patch.object(app, "build_agent") as build, \
                patch.object(app, "run_chat", return_value=0) as chat:
            self.assertEqual(app.main(), 0)
            chat.assert_called_once_with(build.return_value)

    def test_cli_question_still_runs_once(self):
        agent = Mock()
        agent.invoke.return_value = {"messages": [AIMessage(content="100")]}
        with patch("sys.argv", ["test.py", "12.5 * 8"]), \
                patch.object(app, "build_agent", return_value=agent), \
                patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(app.main(), 0)
            self.assertEqual(output.getvalue().strip(), "100")
        agent.invoke.assert_called_once()


if __name__ == "__main__":
    unittest.main()
