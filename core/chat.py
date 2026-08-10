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
from datetime import datetime
from openai import OpenAI
from config import (
    API_KEY, BASE_URL, MODEL, API_TIMEOUT, API_MAX_RETRIES,
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
        self.client = OpenAI(
            api_key=API_KEY,
            base_url=BASE_URL,
            timeout=API_TIMEOUT,
            max_retries=API_MAX_RETRIES,
        )
        self.tools = tools
        self.tool_executor = tool_executor
        self.on_tool_call = on_tool_call
        self.messages = [{"role": "system", "content": system_prompt}]
        # 取消标记：UI 点击"停止"后置位，流式生成在下一个 chunk 处退出
        self._cancel_event = threading.Event()

    @staticmethod
    def _is_server_error(e: Exception) -> bool:
        """判断异常是否为服务器端错误（应自动重试）。"""
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

    def _build_kwargs(self, stream: bool = False) -> dict:
        """构造 API 请求参数（ask / ask_stream 共用，保证两者行为一致）。"""
        # 关键点：
        #  - 推理模型（deepseek-v4 等）不支持 temperature/top_p/presence_penalty，
        #    传了会报 400，因此按模型分支构造
        #  - 联网搜索是 DeepSeek 私有参数，只能通过 extra_body 传递
        kwargs = dict(model=MODEL, messages=self.messages, stream=stream)

        # 推理模型（deepseek-reasoner, deepseek-v4 等）不支持 temperature/top_p 等参数
        _is_reasoner = any(x in MODEL.lower() for x in ("reasoner", "v4", "r1"))
        if _is_reasoner:
            kwargs["max_tokens"] = MAX_TOKENS
            if REASONING_EFFORT:
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
        if ENABLE_SEARCH:
            # 联网搜索：DeepSeek 通过 extra_body 传递非标准参数
            kwargs["extra_body"] = {"enable_search": True}
        return kwargs

    def ask(self, user_input: str) -> str:
        """
        发送一条用户消息，返回 AI 的文本回复。

        如果启用了 Function Calling，内部会自动处理：
        AI 请求调用工具 → 执行工具 → 结果喂回 AI → 返回最终回答

        Args:
            user_input: 用户输入文本

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
                msg = resp.choices[0].message

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
                self.messages.append({"role": "assistant", "content": reply, "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
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

    def ask_stream(self, user_input: str):
        """发送消息，以生成器方式逐块产出 AI 回复文本。

        支持流式输出：每收到一个 token 就 yield 出去，前端可实时显示。
        工具调用（Function Calling）在内部自动处理，不中断文本流。

        Args:
            user_input: 用户输入文本

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
                                tool_calls_buffer[idx] = {"name": "", "arguments": ""}
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
                            "id": f"call_{idx}",
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
                            "tool_call_id": f"call_{idx}",
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

        用于保存聊天记录时使用，过滤掉 system 和 tool 消息。
        """
        result = []
        for m in self.messages:
            if m["role"] in ("user", "assistant") and m.get("content"):
                result.append(m)
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
