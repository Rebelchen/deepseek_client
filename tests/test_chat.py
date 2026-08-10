"""core.chat 重试判定 / 请求参数构造 / 取消机制测试。"""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# 测试环境不要求真实密钥，但 ChatSession 构造 OpenAI 客户端时必须有值
os.environ.setdefault("DEEPSEEK_API_KEY", "sk-test-dummy")

from core.chat import ChatSession


class ServerErrorDetectionTest(unittest.TestCase):
    def test_server_errors(self):
        for msg in (
            "HTTP 500 Internal Server Error",
            "502 Bad Gateway",
            "503 Service Unavailable",
            "504 Gateway Timeout",
            "429 Too Many Requests",
            "ConnectionResetError",
            "connection reset",
            "timeout",
        ):
            self.assertTrue(ChatSession._is_server_error(Exception(msg)), msg)

    def test_client_errors(self):
        for msg in (
            "HTTP 400 Bad Request",
            "401 Unauthorized",
            "404 Not Found",
            "invalid api key",
        ):
            self.assertFalse(ChatSession._is_server_error(Exception(msg)), msg)


class BuildKwargsTest(unittest.TestCase):
    @mock.patch("core.chat.MODEL", "deepseek-v4-flash")
    def test_reasoner_kwargs(self):
        with (
            mock.patch("core.chat.REASONING_EFFORT", "medium"),
            mock.patch("core.chat.ENABLE_SEARCH", True),
        ):
            kw = ChatSession()._build_kwargs(stream=True)
        self.assertNotIn("temperature", kw)
        self.assertEqual(kw["reasoning_effort"], "medium")
        self.assertEqual(kw["extra_body"], {"enable_search": True})

    @mock.patch("core.chat.MODEL", "deepseek-chat")
    def test_normal_kwargs(self):
        with mock.patch("core.chat.ENABLE_SEARCH", False):
            kw = ChatSession()._build_kwargs(stream=True)
        self.assertIn("temperature", kw)
        self.assertEqual(kw["stream_options"], {"include_usage": False})
        self.assertNotIn("extra_body", kw)


class CancelTest(unittest.TestCase):
    def test_cancel_mid_stream(self):
        """流式生成中途取消：下一个 chunk 边界处应产出取消标记，不保存半截回复。"""
        session = ChatSession()

        def make_chunk(text):
            delta = SimpleNamespace(
                content=text, tool_calls=None,
                reasoning_content=None, model_extra=None,
            )
            return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])

        class FakeStream:
            def __init__(self, chunks):
                self._chunks = iter(chunks)

            def __iter__(self):
                return self

            def __next__(self):
                return next(self._chunks)

        session.client.chat.completions.create = mock.Mock(
            return_value=FakeStream([make_chunk("a"), make_chunk("b")])
        )

        gen = session.ask_stream("hi")
        self.assertEqual(next(gen), "a")
        session.cancel()
        self.assertEqual(next(gen), "\x00CANCEL\x00")
        with self.assertRaises(StopIteration):
            next(gen)
