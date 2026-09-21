"""配置校验不读取真实 .env、不向模型服务发送请求。"""

import os
import unittest
from unittest.mock import patch

import agent


class AgentConfigTests(unittest.TestCase):
    def test_model_and_proxy_are_forwarded_without_changing_environment(self):
        env = {"OPENAI_API_KEY": "test-key", "OPENAI_MODEL": "provider-model-id",
               "OPENAI_BASE_URL": "https://provider.example/v1", "MODEL_PROXY_URL": "http://127.0.0.1:7890"}
        with patch.dict(os.environ, env, clear=True), patch.object(agent, "load_dotenv"), \
                patch.object(agent, "ChatOpenAI") as model, patch.object(agent, "create_agent"):
            agent.build_agent(group_chat=True)
            self.assertEqual(model.call_args.kwargs["model"], env["OPENAI_MODEL"])
            self.assertEqual(model.call_args.kwargs["openai_proxy"], env["MODEL_PROXY_URL"])
            self.assertNotIn("HTTP_PROXY", os.environ)

    def test_blank_proxy_keeps_existing_connection_behavior(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), \
                patch.object(agent, "load_dotenv"), patch.object(agent, "ChatOpenAI") as model, \
                patch.object(agent, "create_agent"):
            agent.build_agent()
            self.assertNotIn("openai_proxy", model.call_args.kwargs)

    def test_invalid_proxy_is_rejected_before_model_request(self):
        for proxy in ("127.0.0.1:7890", "file:///tmp/proxy", "http://"):
            with self.subTest(proxy=proxy), patch.dict(os.environ, {
                "OPENAI_API_KEY": "test-key", "MODEL_PROXY_URL": proxy,
            }, clear=True), patch.object(agent, "load_dotenv"), patch.object(agent, "ChatOpenAI") as model:
                with self.assertRaises(ValueError):
                    agent.build_agent()
                model.assert_not_called()
