"""core.chat 重试判定 / 请求参数构造 / 取消机制测试。"""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# 测试环境不要求真实密钥，但 ChatSession 构造 OpenAI 客户端时必须有值
os.environ.setdefault("DEEPSEEK_API_KEY", "sk-test-dummy")
os.environ.setdefault("OPENCODE_GO_API_KEY", "sk-go-dummy")
os.environ.setdefault("ZBIGMODEL_API_KEY", "zbig-test-dummy.dummy")
os.environ.setdefault("BAILIAN_TOKEN_PLAN_API_KEY", "sk-sp-test-dummy")
os.environ.setdefault("STEPFUN_API_KEY", "test-dummy-stepfun-key")
os.environ.setdefault("XIAOMI_MIMO_API_KEY", "sk-test-dummy-mimo")

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
            "Connection error",  # openai APIConnectionError，瞬时网络故障应重试
            "Error code: Connection error.",
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


class SessionHeaderTest(unittest.TestCase):
    """来源要求的网关会话头（如 opencode GO 的 x-opencode-session）。"""

    def _headers_of(self, source, model):
        with mock.patch("core.chat.OpenAI") as mock_openai:
            ChatSession().configure(source=source, model=model)
        return mock_openai.call_args.kwargs.get("default_headers")

    def test_opencode_go_sends_session_header(self):
        headers = self._headers_of("opencode_go", "deepseek-v4-flash")
        self.assertIn("x-opencode-session", headers)
        # 值为 32 位 uuid hex
        self.assertRegex(headers["x-opencode-session"], r"^[0-9a-f]{32}$")

    def test_official_has_no_session_header(self):
        self.assertIsNone(self._headers_of("official", "deepseek-flash"))

    def test_bailian_token_plan_has_no_session_header(self):
        """百炼 Token Plan 是标准 OpenAI 兼容端点，不需要网关会话头。"""
        self.assertIsNone(self._headers_of("bailian_token_plan", "qwen3.8-flash"))

    def test_stepfun_has_no_session_header(self):
        """阶跃开放平台是标准 OpenAI 兼容端点，不需要网关会话头。"""
        self.assertIsNone(self._headers_of("stepfun", "step-5-preview"))

    def test_xiaomi_mimo_has_no_session_header(self):
        """小米 MiMo 是标准 OpenAI 兼容端点（鉴权用 Bearer），不需要网关会话头。"""
        self.assertIsNone(self._headers_of("xiaomi_mimo", "mimo-v2.6-flash"))

    def test_session_id_stable_within_session(self):
        s = ChatSession()
        with mock.patch("core.chat.OpenAI") as mock_openai:
            s.configure(source="opencode_go", model="deepseek-v4-flash")
            h1 = mock_openai.call_args.kwargs["default_headers"]["x-opencode-session"]
            s.configure(source="opencode_go", model="deepseek-v4-pro")
            h2 = mock_openai.call_args.kwargs["default_headers"]["x-opencode-session"]
        # 同一会话内切模型，会话 id 保持稳定（网关按会话路由）
        self.assertEqual(h1, h2)
        # 新会话换新 id
        with mock.patch("core.chat.OpenAI") as mock_openai:
            ChatSession().configure(source="opencode_go", model="deepseek-v4-flash")
            h3 = mock_openai.call_args.kwargs["default_headers"]["x-opencode-session"]
        self.assertNotEqual(h2, h3)


class BuildKwargsTest(unittest.TestCase):
    def test_reasoner_kwargs(self):
        with (
            mock.patch("core.chat.REASONING_EFFORT", "high"),
            mock.patch("core.chat.ENABLE_SEARCH", True),
        ):
            session = ChatSession().configure(source="official", model="deepseek-flash")
            kw = session._build_kwargs(stream=True)
        # 思考模式模型不传采样参数，改用 reasoning_effort
        self.assertNotIn("temperature", kw)
        self.assertNotIn("top_p", kw)
        self.assertNotIn("presence_penalty", kw)
        self.assertEqual(kw["reasoning_effort"], "high")
        self.assertEqual(kw["extra_body"], {"enable_search": True})

    def test_official_pro_uses_reasoner_kwargs(self):
        with mock.patch("core.chat.REASONING_EFFORT", "high"):
            session = ChatSession().configure(source="official", model="deepseek-v4-pro")
            kw = session._build_kwargs(stream=True)
        self.assertNotIn("temperature", kw)
        self.assertEqual(kw["reasoning_effort"], "high")

    def test_normal_kwargs(self):
        with mock.patch("core.chat.ENABLE_SEARCH", False):
            session = ChatSession().configure(source="zbigmodel", model="glm-5.3-flash")
            kw = session._build_kwargs(stream=True)
        self.assertIn("temperature", kw)
        self.assertEqual(kw["stream_options"], {"include_usage": False})
        self.assertNotIn("extra_body", kw)

    def test_go_source_kwargs(self):
        """GO 来源：不支持联网搜索与 reasoning_effort，即使全局开关开启也不传。"""
        with (
            mock.patch("core.chat.ENABLE_SEARCH", True),
            mock.patch("core.chat.REASONING_EFFORT", "high"),
        ):
            session = ChatSession().configure(source="opencode_go", model="deepseek-v4-pro")
            kw = session._build_kwargs(stream=True)
        self.assertNotIn("extra_body", kw)
        self.assertNotIn("reasoning_effort", kw)
        self.assertEqual(kw["model"], "deepseek-v4-pro")

    def test_go_v41_flash_kwargs(self):
        """GO 新 Flash（deepseek-v4.1-flash）：思考模型，同样不传采样参数。"""
        with mock.patch("core.chat.REASONING_EFFORT", "high"):
            session = ChatSession().configure(source="opencode_go", model="deepseek-v4.1-flash")
            kw = session._build_kwargs(stream=True)
        self.assertNotIn("temperature", kw)
        self.assertNotIn("reasoning_effort", kw)
        self.assertEqual(kw["model"], "deepseek-v4.1-flash")

    def test_zbigmodel_source_kwargs(self):
        """智谱来源：OpenAI 兼容端点，不传 enable_search / reasoning_effort。"""
        with (
            mock.patch("core.chat.ENABLE_SEARCH", True),
            mock.patch("core.chat.REASONING_EFFORT", "medium"),
        ):
            session = ChatSession().configure(source="zbigmodel", model="glm-5.3-flash")
            kw = session._build_kwargs(stream=True)
        self.assertNotIn("extra_body", kw)
        self.assertNotIn("reasoning_effort", kw)
        self.assertEqual(kw["model"], "glm-5.3-flash")
        self.assertIn("temperature", kw)

    def test_bailian_token_plan_kwargs(self):
        """百炼 Token Plan：两款模型均为思考模型（不传采样参数），
        但不支持 DeepSeek 私有的 enable_search / reasoning_effort。"""
        for model in ("qwen3.8-flash", "qwen3.8-max"):
            with (
                mock.patch("core.chat.ENABLE_SEARCH", True),
                mock.patch("core.chat.REASONING_EFFORT", "high"),
            ):
                session = ChatSession().configure(source="bailian_token_plan", model=model)
                kw = session._build_kwargs(stream=True)
            self.assertNotIn("temperature", kw, model)
            self.assertNotIn("reasoning_effort", kw, model)
            self.assertNotIn("extra_body", kw, model)
            self.assertEqual(kw["model"], model)
            self.assertEqual(kw["stream"], True)

    def test_stepfun_kwargs(self):
        """阶跃 stepfun：思考模型不传采样参数，但官方支持 reasoning_effort 三档，
        且不支持 DeepSeek 私有的 enable_search。"""
        with (
            mock.patch("core.chat.ENABLE_SEARCH", True),
            mock.patch("core.chat.REASONING_EFFORT", "high"),
        ):
            session = ChatSession().configure(source="stepfun", model="step-5-preview")
            kw = session._build_kwargs(stream=True)
        self.assertNotIn("temperature", kw)
        self.assertNotIn("presence_penalty", kw)
        self.assertNotIn("extra_body", kw)
        self.assertEqual(kw["reasoning_effort"], "high")
        self.assertEqual(kw["model"], "step-5-preview")
        self.assertLessEqual(kw["max_tokens"], 64000)  # 官方最大输出 64k

    def test_xiaomi_mimo_kwargs(self):
        """小米 MiMo：思考默认开启 → 按思考分支构造（不下发被官方强制回默认的采样参数）；
        但只有 thinking 开关、无 reasoning_effort 档位，也不支持 enable_search。"""
        for model in ("mimo-v2.6-flash", "mimo-v2.6-pro"):
            with (
                mock.patch("core.chat.ENABLE_SEARCH", True),
                mock.patch("core.chat.REASONING_EFFORT", "high"),
            ):
                session = ChatSession().configure(source="xiaomi_mimo", model=model)
                kw = session._build_kwargs(stream=True)
            self.assertNotIn("temperature", kw, model)
            self.assertNotIn("top_p", kw, model)
            self.assertNotIn("reasoning_effort", kw, model)
            self.assertNotIn("extra_body", kw, model)
            self.assertEqual(kw["model"], model)
            # 官方 max_completion_tokens 上限 131072，客户端 40960 在范围内
            self.assertLessEqual(kw["max_tokens"], 131072)

    def test_configure_rejects_bad_source_or_model(self):
        session = ChatSession()
        with self.assertRaises(ValueError):
            session.configure(source="no_such_source")
        with self.assertRaises(ValueError):
            session.configure(source="official", model="deepseek-v4-flash")  # 旧模型名已下线
        with self.assertRaises(ValueError):
            session.configure(source="official", model="glm-5.3-flash")  # 模型归属智谱来源
        with self.assertRaises(ValueError):
            session.configure(source="opencode_go", model="qwen3.8-flash")  # 模型归属百炼来源
        with self.assertRaises(ValueError):
            session.configure(source="bailian_token_plan", model="glm-5.3-flash")  # 反之亦然
        with self.assertRaises(ValueError):
            session.configure(source="stepfun", model="qwen3.8-flash")  # 阶跃来源只挂 step-5-preview
        with self.assertRaises(ValueError):
            session.configure(source="xiaomi_mimo", model="step-5-preview")  # MiMo 只挂 mimo-* 模型


class VisionSupportTest(unittest.TestCase):
    """图片输入（识图）：仅 vision_models 清单内的模型支持，其余来源自动降级。"""

    DATA_URL = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="

    def test_supports_vision_flags(self):
        self.assertTrue(ChatSession().configure(source="official", model="deepseek-flash").supports_vision)
        self.assertTrue(ChatSession().configure(source="opencode_go", model="deepseek-v4.1-flash").supports_vision)
        self.assertTrue(ChatSession().configure(source="zbigmodel", model="glm-5.3-flash").supports_vision)
        self.assertTrue(ChatSession().configure(source="bailian_token_plan", model="qwen3.8-flash").supports_vision)
        self.assertTrue(ChatSession().configure(source="bailian_token_plan", model="qwen3.8-max").supports_vision)
        self.assertTrue(ChatSession().configure(source="stepfun", model="step-5-preview").supports_vision)
        self.assertTrue(ChatSession().configure(source="xiaomi_mimo", model="mimo-v2.6-flash").supports_vision)
        self.assertTrue(ChatSession().configure(source="xiaomi_mimo", model="mimo-v2.6-pro").supports_vision)
        self.assertFalse(ChatSession().configure(source="official", model="deepseek-v4-pro").supports_vision)
        self.assertFalse(ChatSession().configure(source="opencode_go", model="deepseek-v4-pro").supports_vision)
        self.assertFalse(ChatSession().configure(source="opencode_go", model="deepseek-v4-flash").supports_vision)

    def test_vision_content_passed_through_for_vision_model(self):
        """识图模型：多模态 content 原样发送给 API。"""
        for source, model in (
            ("official", "deepseek-flash"),
            ("opencode_go", "deepseek-v4.1-flash"),
            ("zbigmodel", "glm-5.3-flash"),
            ("bailian_token_plan", "qwen3.8-flash"),
            ("bailian_token_plan", "qwen3.8-max"),
            ("stepfun", "step-5-preview"),
            ("xiaomi_mimo", "mimo-v2.6-flash"),
            ("xiaomi_mimo", "mimo-v2.6-pro"),
        ):
            session = ChatSession().configure(source=source, model=model)
            session.messages.append({"role": "user", "content": [
                {"type": "text", "text": "这是什么？"},
                {"type": "image_url", "image_url": {"url": self.DATA_URL}},
            ]})
            outgoing = session._outgoing_messages()
            self.assertIsInstance(outgoing[-1]["content"], list)
            self.assertEqual(outgoing[-1]["content"][1]["type"], "image_url")

    def test_vision_content_flattened_for_other_models(self):
        """非识图模型：图片压平为占位符，避免网关 400。"""
        session = ChatSession().configure(source="official", model="deepseek-v4-pro")
        session.messages.append({"role": "user", "content": [
            {"type": "text", "text": "看看这张图"},
            {"type": "image_url", "image_url": {"url": self.DATA_URL}},
            {"type": "image_url", "image_url": {"url": self.DATA_URL}},
        ]})
        outgoing = session._outgoing_messages()
        self.assertEqual(outgoing[-1]["content"], "看看这张图\n[图片 x2]")

    def test_get_messages_flattens_vision_content(self):
        """历史保存：图片不落盘，只留占位符。"""
        session = ChatSession().configure(source="zbigmodel", model="glm-5.3-flash")
        session.messages.append({"role": "user", "content": [
            {"type": "text", "text": "看图"},
            {"type": "image_url", "image_url": {"url": self.DATA_URL}},
        ]})
        history = session.get_messages()
        self.assertEqual(history[-1]["content"], "看图\n[图片 x1]")

    def test_stream_accepts_multimodal_input(self):
        """ask_stream 接受多模态 content 列表。"""
        session = ChatSession().configure(source="zbigmodel", model="glm-5.3-flash")
        session.client.chat.completions.create = mock.Mock(return_value=iter([
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(
                content="这是张图", tool_calls=None, reasoning_content=None, model_extra=None))]),
        ]))
        out = "".join(session.ask_stream([
            {"type": "text", "text": "看图"},
            {"type": "image_url", "image_url": {"url": self.DATA_URL}},
        ]))
        self.assertEqual(out, "这是张图")
        sent = session.client.chat.completions.create.call_args.kwargs["messages"]
        self.assertIsInstance(sent[-1]["content"], list)


class EmptyChoicesChunkTest(unittest.TestCase):
    """OpenAI 兼容网关（如 opencode GO）会发送 choices 为空的 chunk，必须跳过而非报错。"""

    def make_stream(self, chunks):
        class FakeStream:
            def __init__(self, chunks):
                self._chunks = iter(chunks)

            def __iter__(self):
                return self

            def __next__(self):
                return next(self._chunks)

        return FakeStream(chunks)

    def make_chunk(self, content=None, choices=None):
        if choices is None:
            delta = SimpleNamespace(
                content=content, tool_calls=None,
                reasoning_content=None, model_extra=None,
            )
            choices = [SimpleNamespace(delta=delta)]
        return SimpleNamespace(choices=choices)

    def test_stream_skips_empty_choices_chunk(self):
        session = ChatSession().configure(source="opencode_go", model="deepseek-v4-flash")
        session.client.chat.completions.create = mock.Mock(
            return_value=self.make_stream([
                self.make_chunk(choices=[]),                       # usage/结束块
                self.make_chunk(content="hello"),
                self.make_chunk(choices=[]),                       # 中间空块
                self.make_chunk(content=" world"),
            ])
        )
        out = "".join(session.ask_stream("hi"))
        self.assertNotIn("list index out of range", out)
        self.assertEqual(out, "hello world")

    def test_ask_handles_empty_choices(self):
        session = ChatSession().configure(source="opencode_go", model="deepseek-v4-flash")
        resp = SimpleNamespace(choices=[])
        session.client.chat.completions.create = mock.Mock(return_value=resp)
        self.assertEqual(session.ask("hi"), "")


class ToolCallProtocolTest(unittest.TestCase):
    """发给 API 的消息必须满足 OpenAI 工具调用协议（严格网关会拒绝悬空 tool_calls）。"""

    def make_chunk(self, content=None, tool_calls=None):
        delta = SimpleNamespace(content=content, tool_calls=tool_calls, reasoning_content=None, model_extra=None)
        return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])

    def test_outgoing_messages_repairs_dangling_tool_calls(self):
        session = ChatSession()
        session.messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi", "timestamp": "2026-01-01 00:00:00"},
            {"role": "assistant", "content": "好的，我来看一下", "tool_calls": [
                {"id": "call_x", "type": "function",
                 "function": {"name": "read_file", "arguments": "{}"}}
            ]},
        ]
        outgoing = session._outgoing_messages()
        self.assertEqual(outgoing[-1], {"role": "assistant", "content": "好的，我来看一下"})
        self.assertNotIn("timestamp", outgoing[1])

    def test_outgoing_messages_drops_empty_dangling(self):
        session = ChatSession()
        session.messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "call_x", "type": "function",
                 "function": {"name": "read_file", "arguments": "{}"}}
            ]},
        ]
        self.assertEqual(len(session._outgoing_messages()), 2)

    def test_outgoing_messages_keeps_complete_tool_round(self):
        session = ChatSession()
        session.messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "call_x", "type": "function",
                 "function": {"name": "read_file", "arguments": "{}"}}
            ]},
            {"role": "tool", "tool_call_id": "call_x", "content": "结果"},
        ]
        outgoing = session._outgoing_messages()
        self.assertEqual(len(outgoing), 4)
        self.assertIn("tool_calls", outgoing[2])

    def test_stream_uses_real_tool_call_ids(self):
        session = ChatSession().configure(source="opencode_go", model="deepseek-v4-flash")

        def fake_create(**kwargs):
            if fake_create.round == 0:
                fake_create.round = 1
                fake_create.last_messages = kwargs["messages"]
                tc = SimpleNamespace(
                    index=0, id="call_real_1", type="function",
                    function=SimpleNamespace(name="read_file", arguments='{"filepath": "a.txt"}'),
                )
                return iter([self.make_chunk(tool_calls=[tc])])
            fake_create.last_messages = kwargs["messages"]
            return iter([self.make_chunk(content="done")])

        fake_create.round = 0
        fake_create.last_messages = None
        session.client.chat.completions.create = mock.Mock(side_effect=fake_create)
        list(session.ask_stream("hi"))

        msgs = fake_create.last_messages
        assistant = [m for m in msgs if m["role"] == "assistant"][-1]
        tool = [m for m in msgs if m["role"] == "tool"][-1]
        self.assertEqual(assistant["tool_calls"][0]["id"], "call_real_1")
        self.assertEqual(tool["tool_call_id"], "call_real_1")


class ReasoningEchoTest(unittest.TestCase):
    """思考模式 + tools：官方要求回传历史轮次的 reasoning_content，否则报 400。"""

    def test_ask_echoes_reasoning_content_for_tool_rounds(self):
        session = ChatSession(
            tools=[{"type": "function", "function": {"name": "read_file", "parameters": {}}}],
            tool_executor=lambda name, args: "文件内容",
        ).configure(source="official", model="deepseek-flash")

        calls = []

        def fake_create(**kwargs):
            calls.append(kwargs["messages"])
            if len(calls) == 1:
                tc = SimpleNamespace(
                    id="call_r1", type="function",
                    function=SimpleNamespace(name="read_file", arguments="{}"),
                )
                msg = SimpleNamespace(content="", tool_calls=[tc], reasoning_content="先读文件")
                return SimpleNamespace(choices=[SimpleNamespace(message=msg)])
            msg = SimpleNamespace(content="完成", tool_calls=None, reasoning_content="可以回答了")
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

        session.client.chat.completions.create = mock.Mock(side_effect=fake_create)
        self.assertEqual(session.ask("读文件"), "完成")

        # 第二次请求必须带回第一轮 assistant 的 reasoning_content
        assistant = [m for m in calls[1] if m["role"] == "assistant"][0]
        self.assertEqual(assistant["reasoning_content"], "先读文件")


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
