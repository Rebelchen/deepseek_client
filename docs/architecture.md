# 架构设计文档

> 本文档描述 DeepSeek 本地问答系统的整体架构设计、模块职责和关键设计决策。

## 架构概览

系统采用 **分层架构**，分为三层：

```
┌─────────────────────────────────────────────────────────────┐
│                    UI 层 (ui/)                                │
│                                                              │
│  ┌─────────────────────┐                                     │
│  │ webview_app.py      │                                     │
│  │ pywebview + Flask   │                                     │
│  │ SSE 流式 + 内联HTML  │                                     │
│  └─────────────────────┘                                     │
├─────────────────────────────────────────────────────────────┤
│                    核心层 (core/)                              │
│                                                              │
│  ┌──────────┐  ┌────────────┐  ┌──────────┐  ┌───────────┐  │
│  │ chat.py  │  │ history.py │  │ tools.py │  │html_render│  │
│  │ API 会话  │  │ 历史管理    │  │ 工具集   │  │ + prompts │  │
│  └──────────┘  └────────────┘  └──────────┘  └───────────┘  │
│        ┌───────────────────────────────────────────────────┐ │
│        │ search.py：联网搜索 + 网页抓取（Bing RSS 纯标准库）    │ │
│        └───────────────────────────────────────────────────┘ │
├─────────────────────────────────────────────────────────────┤
│                    配置层 (config.py)                          │
│           API 参数、路径、界面设置、版本号（密钥走环境变量）      │
└─────────────────────────────────────────────────────────────┘
```

### 分层职责

| 层级 | 模块 | 职责 |
|------|------|------|
| UI 层 | `ui/webview_app.py` | Flask 路由 + 内联 HTML 页面 + SSE 流式接口 |
| 核心层 | `core/chat.py` | DeepSeek API 会话管理、Function Calling 循环、自动重试 |
| 核心层 | `core/history.py` | 对话记录的保存（HTML/TXT）、加载、解析 |
| 核心层 | `core/tools.py` | 工具定义与执行（本地文件 read/write/list/info + 联网工具注册） |
| 核心层 | `core/search.py` | 联网搜索与网页抓取实现（Bing RSS / HTML 正文提取） |
| 核心层 | `core/html_renderer.py` | Markdown→HTML 渲染（Pygments 高亮 + KaTeX 公式） |
| 核心层 | `core/prompts.py` | 系统提示词构建（含联网搜索与公式格式说明） |
| 配置层 | `config.py` | 全局配置、版本号、路径管理 |

## 关键设计决策

### 1. 单一界面（pywebview + Flask）

系统只维护一套界面：pywebview（Edge WebView2）作为窗口容器，
Flask 在后台线程提供 SSE 流式接口，UI 全部为内联 HTML/CSS/JS。
界面层只做"胶水"对接，核心逻辑全部在 core/ 层。

一个进程可以开多个窗口（见下文「会话槽」），它们共用同一个 Flask 服务，
差别只在 URL 上的 `?slot=`。

### 2. 分层解耦

UI 层不直接调用 API，而是通过 `ChatSession` 接口与核心层通信。核心层不依赖任何 UI 代码，可以独立测试或替换为 CLI 界面。

```
用户输入 → UI → session.ask() → core/chat.py → DeepSeek API
                     ↑                    │
                     │    工具调用循环      │
                     └────────────────────┘
```

### 3. Function Calling 自动循环

`ChatSession.ask()` / `ask_stream()` 内部实现了工具调用自动循环：

```
用户提问 → AI 发出 tool_calls → 逐个执行工具
        → 工具结果追加到消息列表 → 再次请求 AI → 返回最终回答
```

如果 AI 多次调用工具，循环自动重复，直到 AI 返回纯文本回答。

### 4. SSE 流式架构（主方案）

```
用户输入 → POST /api/chat → generate() 生成器
                                  │
                          DeepSeek API 流式返回
                                  │
                    yield f"data: {json}\n\n"  (SSE 格式)
                                  │
                    前端 fetch() + ReadableStream
                                  │
                    逐 token 渲染到聊天区（打字机效果）
```

### 5. 历史记录持久化

- **存储格式：** HTML（主，保留公式渲染 + 代码高亮）+ TXT（回退）
- **文件名：** `yyyy-MM-dd_HH-mm-ss_智能标题.html`
- **解析方式：**
  - HTML：提取 `<script id="messages-data">` 中的 JSON
  - TXT：正则匹配 `【我】/【AI】` 标记还原消息列表
- **恢复会话：** `session.restore(parsed_messages)` 加载历史后可继续对话

### 6. 自动重试机制

```python
API_MAX_RETRIES = 3  # config.py 中配置

# 服务器错误（500/502/503/504/429）：指数退避 1s, 2s, 4s
# 客户端错误（参数错误等）：不重试，直接返回
# 所有重试耗尽 → 返回用户友好的错误消息
```

### 7. 路径安全

文件操作工具通过 `_resolve_path()` 统一路径解析：
- 相对路径 → 拼接项目根目录
- 绝对路径 → 直接使用（不限制目录范围，可自由访问本地文件）

`write_file` 额外做了一层安全约束：所有保存的文件统一重定向到项目的
「总结」目录（相对路径保留子目录、绝对路径只取文件名、`..` 越界被钳制），
避免 AI 写文件时覆盖任意路径。

### 8. 取消与自动保存（v1.3.0，按槽改造）

- **取消生成**：前端"停止"按钮通过 AbortController 中断 SSE，同时调用 `/api/cancel`
  （带 `slot`）置位该槽 ChatSession 的取消标记；`ask_stream()` 在下一个 chunk 边界退出，
  半截回复不写入会话、不落盘。取消只影响自己那条流，别的窗口照常生成。
- **自动保存**：每轮回答完成后调用 `_save_slot(slot)`，
  同一会话内覆盖保存到该槽首次生成的历史文件（`overwrite_conversation_html`），
  历史目录不再堆积重复副本；进程退出时 `_save_all_slots()` 兜底。

### 9. 会话槽（slot）与多对话并行

一个窗口 = 一个会话槽 = 一份独立上下文。`ui/webview_app.py` 里的 `Slot` 持有
`ChatSession` + 私有锁 + 该会话的历史文件名；`SLOTS` 按槽 id 索引（`main` 是主槽，
「新窗口」依次分配 `s2`、`s3`…，编号只增不复用，避免新窗口接上旧窗口的上下文）。

```
窗口 A (?slot=main)   ── /api/chat {slot:main} ──► Slot(main).session ─► API 流 ①
窗口 B (?slot=s2)     ── /api/chat {slot:s2}   ──► Slot(s2).session   ─► API 流 ②   ①② 同时进行
```

- **core 层零改动**：`ChatSession` 的 `messages` / `_cancel_event` / `client` /
  `_session_id` 本就是实例状态，多实例即天然并发。
- **锁的用法**：`slot.lock` 只在"改结构"的瞬间持有（建会话、换源、抢占生成权），
  绝不横跨整个流式期间；生成期间由 `slot.busy` 标记独占该槽。
  这样既保证"一个槽同时只有一条回答"（上下文不会被两条流写坏，第二次请求返回 409），
  又让 `/api/cancel` 能立刻拿到锁 —— 旧版全程持全局锁，取消请求会排队到回答结束。
  `finally` 里归还生成权，客户端 abort / 异常 / 正常结束都会走到。
- **前端注入**：`CHAT_HTML` 里统一包装 `window.fetch`，给所有 `/api/*` 带上本窗口的
  `slot`（GET 走 query，POST 合并进 JSON body）。每窗口的 JS 上下文天然隔离，
  所以并行不需要动流式渲染那套单流状态机。
- **上限**：`config.MAX_WINDOWS = 4`。每个窗口一路 SSE 长连接，
  WebView2 对同一 host 的并发连接约 6，要给换源/历史等普通请求留余量。
- **限流提醒**：多窗口并发打同一个来源时，`_is_server_error` 会把 429 当瞬时错误
  做指数退避重试，几路一起撞限流会互相放大（订阅制来源尤其注意），
  各窗口分开选来源更稳。


## 数据流

### 发送消息流程（webview_app.py）

```
用户输入文本 → 点击发送
    │
    ▼
前端 JS: sendMessage()
    ├─ 显示用户消息到聊天区
    ├─ 禁用发送按钮
    ├─ fetch POST /api/chat (SSE)
    │
    ▼
Flask 路由: /api/chat (body: {message, images, slot})
    ├─ get_slot(slot) 抢占生成权（slot.busy；该槽已有流在跑 → 409）
    ├─ 调用 slot.session.ask_stream(user_input)
    │   ├─ 追加用户消息到 messages[]
    │   ├─ 调用 DeepSeek API (stream=True)
    │   ├─ yield 逐 token (SSE data: ...)
    │   ├─ (如有工具调用) 循环执行工具
    │   │   └─ yield 工具调用日志
    │   └─ yield "[DONE]"
    │
    ▼
前端 JS: processChunk()
    ├─ 逐 token 渲染到聊天区（打字机效果）
    ├─ (如有 reasoning) 展示深度思考过程
    ├─ (完成) 启用发送按钮
    ├─ KaTeX 重新渲染公式
    └─ 自动滚动到最新消息
```

### 启动流程

```
main.py → launch(port=5000)
    ├─ 1. get_slot("main")     → 建主会话槽（ChatSession + system prompt + 该槽上次的来源）
    ├─ 2. run_server(daemon)   → Flask 在后台线程启动（所有窗口共用这一个服务）
    ├─ 3. _wait_server_ready() → 轮询等待 Flask 就绪（端口冲突自动换空闲端口）
    ├─ 4. _open_slot_window    → 主窗口加载 http://127.0.0.1:5000/?slot=main
    ├─ 5. webview.start()      → 事件循环（阻塞，直到所有窗口都关闭）
    │      └─ 「新窗口」按钮 → POST /api/new-window → 同进程再开一个 ?slot=sN 的窗口
    └─ 6. _save_all_slots()    → 退出前逐槽兜底保存
```


## 扩展指南

### 添加新工具
1. 在 `core/tools.py` 中：
   - 编写工具函数（输入参数、返回文本）
   - 在 `TOOLS` 列表中添加 OpenAI tool schema
   - 在 `TOOL_MAP` 中注册
2. 工具函数自动被 `ChatSession` 的 Function Calling 循环调用
3. 如需在 UI 显示进度，利用 `on_tool_call` 回调

### 添加新 API 路由
1. 在 `ui/webview_app.py` 中添加 `@app.route(...)` 方法
2. 前端 HTML/JS 在 `CHAT_HTML` 中通过 `fetch()` 调用

### 替换 API 提供商
1. 修改 `config.py` 中的 `BASE_URL` 和 `MODEL`
2. 如果 API 接口不兼容 OpenAI 格式，修改 `core/chat.py` 的调用逻辑

### 修改前端界面
1. 直接在 `ui/webview_app.py` 的 `CHAT_HTML` 中编辑 HTML/CSS/JS
2. 不支持热重载，修改后需重启应用
