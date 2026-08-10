# API 参考文档

> 本文档提供 DeepSeek 本地问答系统各模块的完整 API 参考。

---

## config — 全局配置

**文件：** [config.py](file:///d:/pythonProject/deepseek_client/config.py)

### 常量

| 名称 | 类型 | 说明 |
|------|------|------|
| `VERSION` | str | 当前版本号 |
| `API_KEY` | str | DeepSeek API 密钥（环境变量 `DEEPSEEK_API_KEY` 或 `.env` 提供，禁止硬编码） |
| `BASE_URL` | str | API 端点地址 |
| `MODEL` | str | 模型名称 |
| `API_TIMEOUT` | int | 请求超时（秒） |
| `API_MAX_RETRIES` | int | 最大重试次数 |
| `TEMPERATURE` | float | 生成温度 (0-2) |
| `TOP_P` | float | 核采样阈值 (0-1) |
| `MAX_TOKENS` | int | 最大 token 数 |
| `PRESENCE_PENALTY` | float | 话题重复惩罚 (-2~2) |
| `FREQUENCY_PENALTY` | float | 频率惩罚 (-2~2) |
| `STOP` | str \| list \| None | 停止序列 |
| `PROJECT_ROOT` | str | 项目根目录绝对路径 |
| `HISTORY_DIR` | str | 历史记录目录路径 |
| `APP_TITLE` | str | 窗口标题 |
| `WINDOW_WIDTH` | int | 窗口宽度（像素） |
| `WINDOW_HEIGHT` | int | 窗口高度（像素） |

---

## core.prompts — 系统提示词

**文件：** [core/prompts.py](file:///d:/pythonProject/deepseek_client/core/prompts.py)

#### `build_system_prompt(enable_search=None) → str`

构建默认系统提示词（联网搜索说明 + LaTeX 公式格式要求）。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enable_search` | bool \| None | None | 是否包含联网搜索说明；None 时取 `config.ENABLE_SEARCH` |

---

## core.chat — API 会话管理

**文件：** [core/chat.py](file:///d:/pythonProject/deepseek_client/core/chat.py)

### ChatSession

管理一次对话的完整生命周期。支持上下文延续、Function Calling 自动循环。

#### `__init__(system_prompt, tools, tool_executor, on_tool_call)`

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `system_prompt` | str | `"你是一个有用的助手。"` | 系统提示词 |
| `tools` | list \| None | None | OpenAI tool definitions 列表 |
| `tool_executor` | callable \| None | None | `(name, args) → str` 工具执行函数 |
| `on_tool_call` | callable \| None | None | `(name, args) → None` UI 回调 |

#### `ask(user_input) → str`

发送用户消息，返回 AI 文本回复。内部自动处理 Function Calling 循环。

| 参数 | 类型 | 说明 |
|------|------|------|
| `user_input` | str | 用户输入文本 |

**返回：** AI 回答文本，或错误提示（重试耗尽后）。

#### `get_messages() → list`

获取对话历史（仅 user/assistant 消息，过滤 system/tool）。

**返回：** `[{"role": str, "content": str, "timestamp": str}, ...]`

#### `reset(system_prompt)`

重置对话，清除所有历史消息。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `system_prompt` | str | `"你是一个有用的助手。"` | 新的系统提示词 |

#### `restore(messages)`

从历史记录恢复会话状态。

| 参数 | 类型 | 说明 |
|------|------|------|
| `messages` | list | `[{"role": "user"/"assistant", "content": str}, ...]` |

#### `cancel()`

取消当前正在进行的生成（流式接口在下一个 chunk 边界退出，半截回复不保存）。
下次 `ask()` / `ask_stream()` 会自动复位取消标记。

---

## core.history — 聊天记录管理

**文件：** [core/history.py](file:///d:/pythonProject/deepseek_client/core/history.py)

### 函数

#### `generate_title(messages) → str`

根据第一条用户消息生成智能标题。

| 参数 | 类型 | 说明 |
|------|------|------|
| `messages` | list | 消息列表 |

**返回：** 清理后的标题字符串（最多 30 字符）。

#### `save_conversation(messages, history_dir) → str`

将对话保存为 TXT 文件。

| 参数 | 类型 | 说明 |
|------|------|------|
| `messages` | list | 消息列表 |
| `history_dir` | str | 存储目录路径 |

**返回：** 保存的文件完整路径。

#### `save_conversation_html(messages, history_dir, chat_html) → str`

将对话保存为 HTML 文件（保留公式渲染 + 代码高亮，内嵌 JSON 可恢复会话）。

| 参数 | 类型 | 说明 |
|------|------|------|
| `messages` | list | 消息列表 |
| `history_dir` | str | 存储目录路径 |
| `chat_html` | str | 已渲染的聊天区 HTML |

**返回：** 保存的文件完整路径。

#### `overwrite_conversation_html(filepath, messages, chat_html) → str`

用同一文件路径覆盖保存对话（同一会话增量保存，不产生重复副本）。

| 参数 | 类型 | 说明 |
|------|------|------|
| `filepath` | str | 已存在的历史文件完整路径 |
| `messages` | list | 消息列表 |
| `chat_html` | str | 已渲染的聊天区 HTML |

#### `list_conversations(history_dir) → list`

列出所有历史对话文件，按修改时间倒序排列。

| 参数 | 类型 | 说明 |
|------|------|------|
| `history_dir` | str | 历史目录路径 |

**返回：** `[(文件名, 文件路径), ...]`

#### `load_conversation(filepath) → str`

读取历史对话文件全部内容。

| 参数 | 类型 | 说明 |
|------|------|------|
| `filepath` | str | TXT 文件路径 |

**返回：** 文件全部文本。

#### `parse_conversation(filepath) → list`

将历史对话 TXT 解析为消息列表。

| 参数 | 类型 | 说明 |
|------|------|------|
| `filepath` | str | TXT 文件路径 |

**返回：** `[{"role": "user"/"assistant", "content": str}, ...]`

---

## core.tools — 文件操作工具箱

**文件：** [core/tools.py](file:///d:/pythonProject/deepseek_client/core/tools.py)

### 常量

| 名称 | 类型 | 说明 |
|------|------|------|
| `TOOLS` | list | OpenAI tool definitions（用于 Function Calling） |
| `TOOL_MAP` | dict | 工具名称到函数的映射 |

### 工具函数

#### `read_file(filepath) → str`

读取文本文件内容（UTF-8/GBK 自适应），超过 10000 字符自动截断。

| 参数 | 类型 | 说明 |
|------|------|------|
| `filepath` | str | 文件路径（绝对或相对） |

#### `write_file(filepath, content) → str`

保存内容到文件（UTF-8 编码）。所有文件统一保存到项目的「总结」目录：
相对路径保留子目录结构，绝对路径只取文件名，父目录不存在时自动创建。

| 参数 | 类型 | 说明 |
|------|------|------|
| `filepath` | str | 文件路径 |
| `content` | str | 文件内容 |

#### `list_files(dirpath) → str`

列出目录下的文件和子目录（按字母排序）。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `dirpath` | str | 项目根目录 | 目录路径 |

#### `get_file_info(filepath) → str`

获取路径的详细信息（类型、大小、修改时间）。

| 参数 | 类型 | 说明 |
|------|------|------|
| `filepath` | str | 文件或目录路径 |

### 调度函数

#### `execute_tool(name, args) → str`

执行工具调用，自动从 `TOOL_MAP` 查找并调用对应函数。

| 参数 | 类型 | 说明 |
|------|------|------|
| `name` | str | 工具名称 |
| `args` | dict | 参数字典 |

---

## ui.webview_app — pywebview + Flask 界面（当前主 UI）

**文件：** [ui/webview_app.py](file:///d:/pythonProject/deepseek_client/ui/webview_app.py)

### 启动函数

#### `launch(port=5000)`

启动 Flask 服务器 + pywebview 窗口。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `port` | int | 5000 | Flask 监听端口 |

**启动时序：**
1. 初始化 ChatSession（含 system prompt、工具注册）
2. Flask 后台线程启动
3. 轮询探测 Flask 就绪（端口冲突时自动切换空闲端口）
4. 创建 pywebview 窗口，加载 `http://127.0.0.1:{port}/`
5. 进入事件循环（阻塞，窗口关闭后返回）
6. 自动保存当前对话

### Flask 路由

| 路由 | 方法 | 说明 |
|------|------|------|
| `/` | GET | 返回内联 HTML 聊天页面（CHAT_HTML） |
| `/api/chat` | POST | SSE 流式聊天接口（generate() 生成器） |
| `/api/reset` | POST | 重置对话会话 |
| `/api/history` | GET | 列出所有历史对话 |
| `/api/history/<filename>` | GET | 加载指定历史对话 |
| `/api/history/<filename>` | DELETE | 删除指定历史对话 |
| `/api/restore` | POST | 恢复历史会话上下文 |
| `/api/cancel` | POST | 取消当前生成（停止按钮） |
| `/api/open-summary` | POST | 在资源管理器中打开总结目录 |

### 内部函数（供扩展参考）

| 函数 | 说明 |
|------|------|
| `_init_session()` | 初始化/重置 ChatSession（含 system prompt） |
| `_save_current_session()` | 将当前会话保存为 HTML 文件 |
| `_render_messages_html(messages)` | 将消息列表渲染为聊天区 HTML |
| `run_server(port)` | 在后台线程运行 Flask 服务器 |
| `_find_free_port(preferred)` | 优先使用指定端口，被占用时挑选空闲端口 |
| `_wait_server_ready(port)` | 轮询探测 Flask 是否就绪 |

### SSE 事件格式

`/api/chat` 返回 `text/event-stream`，每行格式为 `data: {json}\n\n`：

| type 字段 | 说明 |
|-----------|------|
| `chunk` | 普通文本片段，拼接到当前消息 |
| `reasoning` | 深度思考过程文本 |
| `reasoning_end` | 思考过程结束 |
| `render` | 服务端渲染的完整 HTML（用于替换流式内容） |
| `tool` | 工具调用日志 |
| `cancelled` | 用户停止生成，流结束（半截回复不保存） |
| `error` | 错误信息 |
| `[DONE]` | 流式响应结束标记 |
