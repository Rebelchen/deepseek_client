"""
DeepSeek API 对话会话管理

提供 ChatSession 类，封装与 DeepSeek API 的交互逻辑。
支持：
- 普通对话流式调用
- Function Calling（工具调用自动循环）
- 对话历史导出与重置
- HTTP 500 / 网络错误自动重试（指数退避）
- 用户点击"停止"时取消当前生成（cancel()）
"""

import json
import logging
import threading
import time
import uuid
from datetime import datetime
from openai import OpenAI
from config import (
    SOURCE, MODEL, SOURCES, API_TIMEOUT, API_MAX_RETRIES,
    TEMPERATURE, TOP_P, MAX_TOKENS, PRESENCE_PENALTY,
    FREQUENCY_PENALTY, STOP, REASONING_EFFORT, ENABLE_SEARCH, SHOW_REASONING,
)


logger = logging.getLogger(__name__)


class ChatSession:
    """
    管理一次对话的完整生命周期。

    用法：
        session = ChatSession()
        reply = session.ask("你好")
        session.ask("再问一个问题")  # 上下文自动延续
        session.reset()               # 开始新对话

    支持 Function Calling：
        session = ChatSession(tools=TOOLS, tool_executor=execute_tool)
        reply = session.ask("读取文件 test.txt")  # AI 会自动调用工具
    """

    def __init__(self, system_prompt: str = "你是一个有用的助手。",
                 tools: list = None,
                 tool_executor=None,
                 on_tool_call=None):
        """
        初始化会话。

        Args:
            system_prompt: 系统提示词，定义 AI 的角色和行为
            tools: OpenAI tool definitions 列表（见 core/tools.py）
            tool_executor: callable(name, args) -> str
                           收到工具的调用请求时执行并返回结果文本
            on_tool_call: callable(name, args) -> None
                          UI 回调，每次工具调用时触发（用于显示进度）
        """
        self.tools = tools
        self.tool_executor = tool_executor
        self.on_tool_call = on_tool_call
        self.messages = [{"role": "system", "content": system_prompt}]
        # 取消标记：UI 点击"停止"后置位，流式生成在下一个 chunk 处退出
        self._cancel_event = threading.Event()
        # 网关会话标识：每个对话一个稳定 uuid（部分网关要求会话头用于路由，
        # 见 config.SOURCES 的 session_header；新对话由新建 ChatSession 换新 id）
        self._session_id = uuid.uuid4().hex
        # 来源/模型：默认取 config，界面切换时通过 configure() 即时更新
        self.source = SOURCE
        self.model = MODEL
        self.client = None
        self.configure(source=SOURCE, model=MODEL)

    def configure(self, source: str = None, model: str = None):
        """切换 API 来源与模型（运行中即时生效，无需重启）。

        Args:
            source: SOURCES 的键（official / opencode_go / zbigmodel /
                    bailian_token_plan / stepfun / xiaomi_mimo）；None 表示保持当前
            model:  该来源 models 列表中的模型名；None 表示保持当前

        Raises:
            ValueError: 来源不存在 / 模型不属于该来源 / 缺少对应 API Key
        """
        source = source or self.source
        model = model or self.model
        info = SOURCES.get(source)
        if info is None:
            raise ValueError(f"未知来源: {source}")
        if model not in info["models"]:
            raise ValueError(
                f"来源「{info['label']}」不提供模型 {model}，可选: {', '.join(info['models'])}"
            )
        if not info["api_key"]:
            raise ValueError(
                f"未配置 {info['env_key']}（来源「{info['label']}」需要）。"
                f"请将其设置为环境变量（推荐配置在虚拟环境激活脚本，见 scripts/setup_venv_keys.ps1）。"
            )
        self.source = source
        self.model = model
        # 重建客户端（base_url / api_key 随来源变化）；
        # 来源要求会话头时携带稳定 uuid（如 opencode GO 的 x-opencode-session）
        session_header = info.get("session_header")
        self.client = OpenAI(
            api_key=info["api_key"],
            base_url=info["base_url"],
            timeout=API_TIMEOUT,
            max_retries=API_MAX_RETRIES,
            default_headers={session_header: self._session_id} if session_header else None,
        )
        return self

    @staticmethod
    def _is_server_error(e: Exception) -> bool:
        """判断异常是否为瞬时错误（应自动重试）。

        除 HTTP 5xx/429 外，也包含连接类瞬时故障：openai SDK 的
        APIConnectionError 消息为 "Connection error"（长对话工具循环中
        复用被网关关闭的 keep-alive 连接、网络抖动时最常见），可经退避
        重试恢复。
        """
        msg = str(e).lower()
        triggers = [
            "500",               # HTTP 500 Internal Server Error
            "502",               # Bad Gateway
            "503",               # Service Unavailable
            "504",               # Gateway Timeout
            "429",               # Too Many Requests
            "connectionreset",   # 远程主机强制关闭连接
            "connection refused",
            "connection reset",
            "connection error",  # openai APIConnectionError（连接建立/复用失败）
            "timed out",
            "timeout",
            "too many",
            "reqwest",           # Rust HTTP 客户端错误
            "hyper",             # HTTP 底层库错误
            "network io error",
            "remote host",
            "远程主机",
        ]
        return any(t in msg for t in triggers)

    @staticmethod
    def _clean_error(e: Exception) -> str:
        """将 API 错误转为简洁的人类可读消息。"""
        msg = str(e)
        # 提取 HTTP 状态码（如果有）
        import re
        status_match = re.search(r'HTTP Status: (\d+)', msg, re.IGNORECASE)
        code_msg = f" (HTTP {status_match.group(1)})" if status_match else ""

        # 提取核心错误描述
        if any(t in msg.lower() for t in ["connectionreset", "远程主机"]):
            return f"服务器连接被重置{code_msg}，一般重试即可恢复"
        if "500" in msg or "internal server" in msg.lower():
            return f"服务器内部错误{code_msg}，请稍后重试"
        if "502" in msg:
            return f"网关错误{code_msg}，请稍后重试"
        if "503" in msg:
            return f"服务暂不可用{code_msg}，请稍后重试"
        if "timeout" in msg.lower():
            return f"请求超时{code_msg}，服务器响应过慢"
        if "rate limit" in msg.lower():
            return f"请求频率过高{code_msg}，请稍后重试"

        # 默认截取前 120 字符
        clean = msg.strip().strip(".")
        if len(clean) > 120:
            clean = clean[:117] + "..."
        return clean

    def cancel(self) -> None:
        """取消当前正在进行的生成。下次 ask/ask_stream 会自动复位。"""
        self._cancel_event.set()

    def _reset_cancel(self) -> None:
        self._cancel_event.clear()

    @property
    def supports_vision(self) -> bool:
        """当前来源+模型是否支持图片输入（识图）。

        识图模型清单见 config.SOURCES 各来源的 vision_models。
        不支持时，发送前会把历史消息中的图片压平为文本占位符。
        """
        info = SOURCES.get(self.source, {})
        return self.model in info.get("vision_models", ())

    @staticmethod
    def _content_to_text(content) -> str:
        """把多模态 content（text / image_url 混合列表）压平为纯文本。

        图片转成 "[图片 xN]" 占位符：历史记录不落 base64 大图；
        不支持识图的模型也能带着占位符继续理解上下文，不会报 400。
        """
        if isinstance(content, str):
            return content
        texts, n_imgs = [], 0
        for part in content:
            if isinstance(part, str):
                texts.append(part)
            elif isinstance(part, dict):
                if part.get("type") == "text" and part.get("text"):
                    texts.append(str(part["text"]))
                elif part.get("type") == "image_url":
                    n_imgs += 1
        if n_imgs:
            texts.append(f"[图片 x{n_imgs}]")
        return "\n".join(texts)

    def _outgoing_messages(self) -> list:
        """构造发给 API 的消息（每次请求前清理，防御严格网关校验）。

        - 只保留协议允许的字段（剔除 timestamp 等多余键）
        - 修复"悬空"的 assistant(tool_calls)：若其后没有对应的 tool 消息
          （异常/中断可能残留），移除其 tool_calls，仅保留文本；
          若因此内容为空则整条丢弃——否则 OpenAI 兼容网关（如 opencode GO）
          会以 "assistant message with tool_calls must be followed by tool
          messages" 拒绝请求
        """
        clean = []
        for m in self.messages:
            e = {}
            if m.get("role") in ("system", "user", "assistant", "tool"):
                e["role"] = m["role"]
            if m.get("content") is not None:
                e["content"] = m["content"]
            if m.get("tool_calls"):
                e["tool_calls"] = m["tool_calls"]
            if m.get("tool_call_id"):
                e["tool_call_id"] = m["tool_call_id"]
            if m.get("reasoning_content"):
                e["reasoning_content"] = m["reasoning_content"]
            if e.get("role"):
                clean.append(e)

        result = []
        for i, m in enumerate(clean):
            if m.get("role") == "assistant" and m.get("tool_calls"):
                ids = {tc.get("id") for tc in m["tool_calls"]}
                # 紧随其后的应全是 tool 消息，且覆盖所有 tool_call_id
                j = i + 1
                covered = set()
                while j < len(clean) and clean[j].get("role") == "tool":
                    cid = clean[j].get("tool_call_id")
                    if cid in ids:
                        covered.add(cid)
                    j += 1
                if covered != ids:
                    m = {k: v for k, v in m.items() if k != "tool_calls"}
                    if not m.get("content"):
                        continue  # 悬空且无文本，整条丢弃
            result.append(m)

        # 不支持识图的来源：多模态 content 压平为纯文本（图片转占位符），
        # 避免网关对 image_url 内容报 400；识图模型保持原生列表格式
        if not self.supports_vision:
            for m in result:
                if isinstance(m.get("content"), list):
                    m["content"] = self._content_to_text(m["content"])
        return result

    def _build_kwargs(self, stream: bool = False) -> dict:
        """构造 API 请求参数（ask / ask_stream 共用，保证两者行为一致）。"""
        # 关键点：
        #  - 思考模式模型（官方 deepseek-flash / deepseek-v4-pro）不支持
        #    temperature/presence_penalty/frequency_penalty，传了也不生效，
        #    因此按来源配置的 reasoning_models 分支构造
        #  - 本地 web_search / fetch_webpage 工具与来源无关（见 core/tools.py）
        #  - DeepSeek 服务端自动搜索是私有参数，只能通过 extra_body 传递，
        #    且仅官方来源支持（GO 网关不支持时自动跳过）
        kwargs = dict(model=self.model, messages=self._outgoing_messages(), stream=stream)
        source_info = SOURCES.get(self.source, {})

        # 思考模式模型：不传采样参数，用 reasoning_effort 调节思考强度
        _is_reasoner = self.model in source_info.get("reasoning_models", ())
        if _is_reasoner:
            kwargs["max_tokens"] = MAX_TOKENS
            # reasoning_effort 仅部分来源支持（GO 网关为 OpenAI 兼容端点，传了可能报错）
            if REASONING_EFFORT and source_info.get("supports_reasoning_effort"):
                kwargs["reasoning_effort"] = REASONING_EFFORT
        else:
            kwargs.update(
                temperature=TEMPERATURE,
                top_p=TOP_P,
                max_tokens=MAX_TOKENS,
                presence_penalty=PRESENCE_PENALTY,
                frequency_penalty=FREQUENCY_PENALTY,
            )
            if stream:
                kwargs["stream_options"] = {"include_usage": False}

        if STOP is not None:
            kwargs["stop"] = STOP
        if self.tools:
            kwargs["tools"] = self.tools
            kwargs["tool_choice"] = "auto"
        if ENABLE_SEARCH and source_info.get("supports_search"):
            # DeepSeek 服务端自动搜索：通过 extra_body 传递非标准参数
            # （与本地 Function Calling 的 web_search 工具互为补充）
            kwargs["extra_body"] = {"enable_search": True}
        return kwargs

    def ask(self, user_input: str | list) -> str:
        """
        发送一条用户消息，返回 AI 的文本回复。

        如果启用了 Function Calling，内部会自动处理：
        AI 请求调用工具 → 执行工具 → 结果喂回 AI → 返回最终回答

        Args:
            user_input: 用户输入文本；或 OpenAI vision 多模态 content 列表
                        （[{"type": "text", ...}, {"type": "image_url", ...}]，含图片时）

        Returns:
            AI 回答文本，或错误提示
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.messages.append({"role": "user", "content": user_input, "timestamp": now})
        self._reset_cancel()

        retries_left = API_MAX_RETRIES
        while True:
            try:
                kwargs = self._build_kwargs(stream=False)
                resp = self.client.chat.completions.create(**kwargs)
                # 空响应（无 choices，网关偶发）不报错，按无内容处理
                if not resp.choices:
                    return ""
                msg = resp.choices[0].message

                # 思考模式返回的思维链（reasoning_content）。官方指南要求：
                # 请求携带 tools 时，历史轮次的思维链必须完整回传，否则 API 返回 400。
                # OpenAI SDK 未定义该字段，用 getattr + model_extra 兜底。
                reasoning = getattr(msg, "reasoning_content", None)
                if reasoning is None and getattr(msg, "model_extra", None):
                    reasoning = msg.model_extra.get("reasoning_content")

                # ---------- 情况 1：AI 请求调用工具 ----------
                if msg.tool_calls:
                    assistant_msg = {
                        "role": "assistant",
                        "content": msg.content or "",
                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments,
                                },
                            }
                            for tc in msg.tool_calls
                        ],
                    }
                    if reasoning:
                        assistant_msg["reasoning_content"] = reasoning
                    self.messages.append(assistant_msg)

                    # 逐个执行工具
                    for tc in msg.tool_calls:
                        name = tc.function.name
                        try:
                            args = json.loads(tc.function.arguments)
                        except json.JSONDecodeError:
                            args = {}

                        # UI 回调（显示"正在读取..."等灰色提示）
                        if self.on_tool_call:
                            self.on_tool_call(name, args)

                        # 执行工具
                        result = self.tool_executor(name, args) if self.tool_executor else "未注册工具执行器"
                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": str(result),
                        })

                    # 工具调用成功，重置重试计数
                    # 将工具结果回传给 AI，获取最终回答
                    retries_left = API_MAX_RETRIES
                    continue

                # ---------- 情况 2：普通文本回复 ----------
                reply = msg.content or ""
                reply_msg = {"role": "assistant", "content": reply, "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
                if reasoning:
                    reply_msg["reasoning_content"] = reasoning
                self.messages.append(reply_msg)
                return reply

            except Exception as e:
                is_server_err = self._is_server_error(e)
                if not is_server_err:
                    # 客户端错误（参数错误、鉴权失败等）重试无意义，直接返回
                    return f"请求失败: {self._clean_error(e)}"

                retries_left -= 1
                if retries_left <= 0:
                    # 全部重试用尽，返回简洁错误消息
                    clean = self._clean_error(e)
                    return f"请求失败（已重试 {API_MAX_RETRIES} 次）: {clean}"

                # 服务器错误：指数退避（1s, 2s, 4s...）
                wait = 2 ** (API_MAX_RETRIES - retries_left)  # 1, 2, 4...
                logger.warning(
                    "API 服务器错误，%.1fs 后重试（剩余 %d 次）: %s",
                    wait, retries_left, self._clean_error(e),
                )
                time.sleep(wait)

    def ask_stream(self, user_input: str | list):
        """发送消息，以生成器方式逐块产出 AI 回复文本。

        支持流式输出：每收到一个 token 就 yield 出去，前端可实时显示。
        工具调用（Function Calling）在内部自动处理，不中断文本流。

        Args:
            user_input: 用户输入文本，或 vision 多模态 content 列表（含图片时，
                        仅识图模型接受，见 supports_vision）

        Yields:
            str: 回复文本片段，前端逐个拼接即可得到完整回复
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.messages.append({"role": "user", "content": user_input, "timestamp": now})
        self._reset_cancel()

        retries_left = API_MAX_RETRIES
        while True:
            # 用户点击"停止"后，不再发起新一轮请求
            if self._cancel_event.is_set():
                yield "\x00CANCEL\x00"
                return
            try:
                kwargs = self._build_kwargs(stream=True)
                stream = self.client.chat.completions.create(**kwargs)

                # ── 收集流式响应 ──
                collected_content = ""
                collected_reasoning = ""
                tool_calls_buffer = {}  # index → {name, args}

                for chunk in stream:
                    # 取消：在下一个 chunk 边界处退出，不保存半截回复
                    if self._cancel_event.is_set():
                        yield "\x00CANCEL\x00"
                        return

                    # 忽略无 choices 的 chunk（OpenAI 兼容网关会发送 usage/结束等空块）
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta

                    # DeepSeek 思考过程（reasoning_content）
                    # 用 getattr + model_extra 兜底（OpenAI SDK 未定义此字段）
                    rc = getattr(delta, "reasoning_content", None)
                    if rc is None and hasattr(delta, "model_extra") and delta.model_extra:
                        rc = delta.model_extra.get("reasoning_content", None)
                    if rc:
                        collected_reasoning += rc
                        # 如果配置了显示，将思考过程 yield 给前端
                        if SHOW_REASONING:
                            yield f"\x00RSNG\x00{rc}\x00RSNG_END\x00"
                        # 不 continue — reasoning 和 content 可能在同个 chunk 返回

                    # 文本块
                    if delta.content:
                        collected_content += delta.content
                        yield delta.content

                    # 工具调用
                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index
                            if idx not in tool_calls_buffer:
                                tool_calls_buffer[idx] = {"name": "", "arguments": "", "id": None}
                            # 优先使用网关下发的真实 tool_call_id（严格网关要求 id 匹配）
                            if getattr(tc, "id", None) and not tool_calls_buffer[idx]["id"]:
                                tool_calls_buffer[idx]["id"] = tc.id
                            if tc.function and tc.function.name:
                                tool_calls_buffer[idx]["name"] += tc.function.name
                            if tc.function and tc.function.arguments:
                                tool_calls_buffer[idx]["arguments"] += tc.function.arguments

                # ── 检查是否有工具调用 ──
                if tool_calls_buffer:
                    # 取消后不再执行工具、不再回传模型
                    if self._cancel_event.is_set():
                        yield "\x00CANCEL\x00"
                        return

                    # 构造 assistant 消息（含 reasoning_content 避免 DeepSeek 400 错误）
                    tool_calls_list = []
                    for idx, tc_data in tool_calls_buffer.items():
                        tool_calls_list.append({
                            "id": tc_data["id"] or f"call_{idx}",
                            "type": "function",
                            "function": {
                                "name": tc_data["name"],
                                "arguments": tc_data["arguments"],
                            },
                        })

                    assistant_msg = {
                        "role": "assistant",
                        "content": collected_content,
                        "tool_calls": tool_calls_list,
                    }
                    # 保留 reasoning_content 避免 DeepSeek 400 错误
                    if collected_reasoning:
                        assistant_msg["reasoning_content"] = collected_reasoning
                    self.messages.append(assistant_msg)

                    # 执行工具
                    for idx, tc_data in tool_calls_buffer.items():
                        name = tc_data["name"]
                        try:
                            args = json.loads(tc_data["arguments"])
                        except json.JSONDecodeError:
                            args = {}

                        if self.on_tool_call:
                            self.on_tool_call(name, args)

                        result = self.tool_executor(name, args) if self.tool_executor else "未注册工具执行器"
                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": tc_data["id"] or f"call_{idx}",
                            "content": str(result),
                        })

                    # 工具执行后，重试以获取最终回答（重置重试计数）
                    retries_left = API_MAX_RETRIES
                    yield "\n\n[工具执行完成，继续生成...]\n\n"
                    continue

                # ── 普通文本回复完成 ──
                msg = {
                    "role": "assistant",
                    "content": collected_content,
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
                # reasoning_content 保存到消息中，避免 DeepSeek 400 错误
                if collected_reasoning:
                    msg["reasoning_content"] = collected_reasoning
                self.messages.append(msg)
                break

            except Exception as e:
                is_server_err = self._is_server_error(e)
                if not is_server_err:
                    # 客户端错误（参数错误、鉴权失败等）重试无意义
                    yield f"\n\n**错误**：请求失败: {self._clean_error(e)}"
                    break

                retries_left -= 1
                if retries_left <= 0:
                    clean = self._clean_error(e)
                    yield f"\n\n**错误**：请求失败（已重试 {API_MAX_RETRIES} 次）: {clean}"
                    break

                wait = 2 ** (API_MAX_RETRIES - retries_left)
                logger.warning(
                    "API 服务器错误，%.1fs 后重试（剩余 %d 次）: %s",
                    wait, retries_left, self._clean_error(e),
                )
                if self._cancel_event.is_set():
                    yield "\x00CANCEL\x00"
                    return
                time.sleep(wait)

    def get_messages(self) -> list:
        """
        获取对话历史（仅 user/assistant 消息）。

        用于保存聊天记录时使用，过滤掉 system 和 tool 消息，
        并移除 tool_calls 字段（工具调用过程不落盘）。
        """
        result = []
        for m in self.messages:
            if m["role"] in ("user", "assistant") and m.get("content"):
                entry = dict(m)
                entry.pop("tool_calls", None)
                # 多模态消息（含图片）压平为文本占位符：历史文件不内嵌 base64 大图
                if isinstance(entry.get("content"), list):
                    entry["content"] = self._content_to_text(entry["content"])
                result.append(entry)
        return result

    def reset(self, system_prompt: str = "你是一个有用的助手。"):
        """
        重置对话，开始新话题。

        清除所有历史消息，仅保留 system prompt。
        """
        self.messages = [{"role": "system", "content": system_prompt}]

    def restore(self, messages: list):
        """
        从历史记录恢复会话状态。

        将解析出的历史消息加载到当前会话中，
        之后调用 ask() 会延续这个对话继续提问。

        Args:
            messages: [{"role": "user"/"assistant", "content": str}, ...]
                      来自 parse_conversation() 的返回值
        """
        # 保留 system prompt，追加历史消息
        system = self.messages[0] if self.messages else {"role": "system", "content": "你是一个有用的助手。"}
        self.messages = [system] + messages
