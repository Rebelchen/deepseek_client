# DeepSeek 本地问答系统

> 一个轻盈、优雅的 DeepSeek 桌面客户端：流式输出、深度思考、文件操作、公式渲染，开箱即用。
>
> A lightweight desktop client for the DeepSeek API, powered by **pywebview + Flask + KaTeX**.

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-yellow)
![Platform](https://img.shields.io/badge/Platform-Windows-0078D6?logo=windows&logoColor=white)
![LLM](https://img.shields.io/badge/LLM-DeepSeek-blueviolet)
![CI](https://github.com/Rebelchen/deepseek_client/actions/workflows/ci.yml/badge.svg)

---

## ✨ 功能特性

### 智能对话
- **流式输出**：逐 token 实时显示，打字机效果
- **多对话并行**：点右上角「新窗口」在同一进程内再开一个独立对话窗口，各自一份上下文、各自一条流，可以同时在生成（会话按「槽 slot」隔离，最多 `MAX_WINDOWS` 个窗口），不用再 `--port` 多开进程
- **可中断生成**：回答过长时点击「停止」，立即节省 token，半截回复不落盘
- **深度思考展示**：支持 `deepseek-flash` / `deepseek-v4.1-flash` / `deepseek-v4-flash` / `deepseek-v4-pro` / `glm-5.3-flash` 的思考过程折叠面板
- **自动重试**：仅对服务器/网络错误（500/502/503/504/429）指数退避重试
- **图片识别**：支持给 AI 发送图片（🖼️ 按钮或直接粘贴截图），由识图模型分析；不支持识图的来源/模型会自动降级（图片转为占位符，不影响对话）
- **联网搜索**：内置 `web_search` / `fetch_webpage` 工具（基于 Bing RSS，无需额外 API Key），任何 API 来源都可用；DeepSeek 官方来源还可叠加服务端搜索
- **完成提示音**：回答结束后播放气泡音（Web Audio 合成，无需音频文件）

### 工具集（Function Calling）
AI 在对话中可主动调用工具：

| 工具 | 功能 | 说明 |
|------|------|------|
| `read_file` | 读取文本 | 自动识别 UTF-8/GBK，超长内容自动截断 |
| `write_file` | 保存文件 | **统一保存到「总结」目录**，自动创建子目录 |
| `list_files` | 列出目录 | 文件与子目录按字母排序 |
| `get_file_info` | 查看信息 | 类型、大小、修改时间 |
| `web_search` | 联网搜索 | Bing 结果（标题/链接/摘要），用于时效性问题与事实核实 |
| `fetch_webpage` | 抓取网页 | 提取网页正文，搜索结果摘要不够时读取完整内容 |

点击右上角「总结」按钮可直接在资源管理器中打开文件保存目录。

### 公式与代码渲染
- **LaTeX 公式**：KaTeX 引擎，效果与 DeepSeek 官网一致
- **多层容错**：兼容单行/多行 `$$...$$`、`$...$`、`\(...\)`、`\[...\]` 及模型输出的双重反斜杠
- **代码高亮**：Pygments 语法着色，支持 70+ 编程语言
- **Markdown**：标题、表格、引用、列表完整支持
- **离线可用**：KaTeX 资源本地化，断网也能渲染历史记录

### 历史记录管理
- **自动保存**：每轮回答完成后落盘为 HTML（保留公式渲染）
- **覆盖更新**：同一会话只维护一个文件，不产生重复副本
- **智能命名**：根据首条消息自动生成文件名
- **继续对话**：加载历史后可在原上下文基础上继续提问

---

## 🖥 界面预览

![界面预览](docs/images/screenshot.png)

---

## 🚀 快速开始

### 环境要求
- Windows 10/11（依赖 Edge WebView2，Win11 自带）
- Python 3.10+

### 1. 安装依赖

```bash
# 创建并激活虚拟环境（推荐，密钥配置在虚拟环境中）
python -m venv .venv
.\.venv\Scripts\Activate.ps1    # PowerShell
# 或 .\.venv\Scripts\activate   # CMD

pip install -r requirements.txt
# 或安装为可执行命令：
pip install -e .
```

### 2. 配置 API Key

密钥**只通过环境变量注入**：代码不读取任何 `.env` 文件，仓库中不保存真实密钥。
支持的来源、各自的密钥变量名与端点/模型清单见 [docs/API.md](docs/API.md)（或直接看 `config.py` 的 `SOURCES`）。

推荐用脚本一次性写进虚拟环境激活脚本（激活即生效，`启动.bat` 会自动激活）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_venv_keys.ps1
```

临时测试也可以只在当前会话设置，例如 `$env:OPENCODE_GO_API_KEY = "…"`。


### 3. 启动

```bash
python main.py                 # 默认端口 5000（先在激活的 venv 中运行）
python main.py --port 5001     # 需要再起一个独立进程时指定端口（日常并行请直接用界面「新窗口」）
deepseek-client                # 使用 pip install -e . 安装后可直接运行
```

### Windows 一键启动

- 双击项目根目录的 **`启动.bat`**：自动激活 `.venv`（注入密钥）并用 `pythonw` 静默启动，无控制台窗口
- 或先激活 venv 再运行 `python main.py`

---

## 📖 使用指南

### 基础对话
1. 底部输入框输入消息，`Enter` 发送；`Shift+Enter` / `Ctrl+Enter` / `Alt+Enter` 均可换行
2. AI 回复流式显示；推理模型的思考过程默认折叠，可点击展开
3. 回答过长可随时点击「停止」中断
4. **多个对话同时进行**：点右上角「新窗口」再开一个独立会话（同进程、同服务），各窗口可分别选来源/模型、各自记住上次选择；一条在长回答时，另一条照常可问
5. **页面缩放**：按住 `Ctrl` + 滚轮缩放界面（50%~200%，与网页缩放效果一致），也可用 `Ctrl+=` / `Ctrl+-` / `Ctrl+0`（放大/缩小/复位）；比例自动记住，重启后保持

### 图片识别
- 使用识图模型时，输入框左侧出现 🖼️ 按钮：可点选图片（PNG/JPG/WebP/GIF，最多 4 张、单张 ≤5MB），也可以直接在输入框粘贴截图
  - **DeepSeek 官方 API**：`deepseek-flash`（`deepseek-v4-pro` 不支持）
  - **opencode GO**：`deepseek-v4.1-flash`（`deepseek-v4-flash` / `deepseek-v4-pro` 不支持）
  - **智谱 BigModel**：`glm-5.3-flash`
  - **百炼 Token Plan**：`qwen3.8-flash` / `qwen3.8-max`（该网关要求图片宽高大于 10px）
  - **阶跃 Step 5**：`step-5-preview`（官方另支持视频输入，客户端暂未接入）
  - **小米 MiMo**：`mimo-v2.6-flash` / `mimo-v2.6-pro`（官方另支持音频/视频输入，客户端暂未接入）
- 发送后图片随消息显示，AI 会分析图片内容（识图、翻译、OCR 等）
- 切换到**不支持识图**的模型（如 opencode GO 的 `deepseek-v4-flash`，或官方/GO 的 `deepseek-v4-pro`）时，图片入口自动隐藏；历史会话中的图片会以「[图片 xN]」占位符保留，不会内嵌 base64

### 历史记录
- 点击左上角 ☰ 打开历史侧栏
- 点击条目加载对话（可继续提问），悬停可 ✕ 删除
- 点击右上角「新对话」开始新话题

### 文件操作
直接告诉 AI 即可，例如：
- "读取 D:\data\report.txt"
- "帮我写一个 Python 脚本"（保存到总结目录）
- "列出当前目录下的文件"

---

## ⚙️ 配置说明

所有参数集中在 `config.py`（API Key 除外，见上文）。常用项：

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `SOURCE` | 默认 API 来源：`opencode_go` / `official` / `zbigmodel` / `bailian_token_plan` / `stepfun` / `xiaomi_mimo` | `opencode_go` |
| `MODEL` | 默认模型（按 `SOURCE` 自动解析） | `deepseek-v4-flash` |
| `SHOW_REASONING` | 是否展示思考过程 | `True` |
| `REASONING_EFFORT` | 推理强度（官方 `low`/`high`/`max`，阶跃 `low`/`medium`/`high`；仅这两个来源下发） | `high` |
| `ENABLE_SEARCH` | 联网工具总开关（`web_search` / `fetch_webpage`） | `True` |
| `TEMPERATURE` | 温度 (0-2) | `0.7` |
| `MAX_TOKENS` | 单次回答最大 token | `40960` |
| `API_TIMEOUT` | 请求超时（秒） | `60` |
| `API_MAX_RETRIES` | 最大重试次数 | `3` |
| `MAX_WINDOWS` | 单进程内最多同时打开的会话窗口数（「新窗口」按钮的上限） | `4` |

> **一键换源**：运行中点击窗口顶部「来源」/「模型」下拉框即可即时切换（官方 API ⇄ opencode GO ⇄ 智谱 BigModel ⇄ 百炼 Token Plan ⇄ 阶跃 Step 5 ⇄ 小米 MiMo），无需重启。
> `SOURCE` 只决定启动时的默认值。

完整参数见 [docs/API.md](docs/API.md)。

---

## 📁 项目结构

```
deepseek_client/
├── main.py                # 程序入口（含 --port 与 API Key 校验）
├── config.py              # 全局配置（版本号读取自 pyproject.toml）
├── pyproject.toml         # 项目元数据 & 依赖 & 命令行入口
├── requirements.txt       # 依赖清单
├── README.md / CHANGELOG.md / LICENSE
├── .env.example           # API Key 配置模板
│
├── core/                  # 核心逻辑层（不依赖任何 UI 代码）
│   ├── chat.py            # DeepSeek 会话管理：流式/工具循环/重试/取消
│   ├── tools.py           # Function Calling 工具集（文件 + 联网）
│   ├── search.py          # 联网搜索与网页抓取（Bing RSS，纯标准库）
│   ├── history.py         # 历史记录保存/加载/解析（HTML+TXT）
│   ├── html_renderer.py   # Markdown→HTML 渲染引擎（Pygments+KaTeX，多层容错）
│   └── prompts.py         # 系统提示词构建
│
├── ui/
│   └── webview_app.py     # 主界面：pywebview + Flask + SSE + 内联 HTML
│
├── static/katex/          # KaTeX 本地资源（断网可用）
├── tests/                 # 单元测试（unittest）
├── docs/                  # 架构设计 & API 参考
├── assets/                # 图标等静态素材
├── history/               # 对话记录（自动生成，已 gitignore）
└── 总结/                   # AI 生成文件的统一保存目录（自动创建，已 gitignore）
```

---

## 🛠 开发指南

### 运行测试

```bash
python -m unittest discover -s tests -v
```

GitHub Actions 会在 `main` 分支推送时自动在 Python 3.10/3.11/3.12 上运行测试。

### 添加新工具
1. 在 `core/tools.py` 中编写函数并注册到 `TOOLS`（OpenAI tool schema）与 `TOOL_MAP`
2. 工具会自动被 `ChatSession` 的 Function Calling 循环调用
3. 如需界面反馈，通过 `on_tool_call` 回调实现

### 添加新路由
1. 在 `ui/webview_app.py` 中用 `@app.route(...)` 定义
2. 前端 JS 在 `CHAT_HTML` 中通过 `fetch()` 调用

### 打包 exe

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name "DeepSeek问答" --icon icon.ico main.py
```

---

## ❓ 常见问题

- **端口被占用？** 程序会自动挑选空闲端口，无需手动处理。
- **公式显示异常？** 已内置多层容错（单行/多行公式、双反斜杠、下划线保护），如仍异常请提交 Issue 并附上原始回答。
- **离线还能用吗？** 历史记录渲染完全离线；DeepSeek API 与联网搜索需要网络。
- **密钥安全吗？** 密钥只通过环境变量注入（推荐写入虚拟环境激活脚本，见 `scripts/setup_venv_keys.ps1`），代码不读取 `.env`，仓库中不保存任何真实密钥。如曾泄露，请立即在对应平台吊销并更换。

---

## 📌 版本管理

- 版本号单一事实来源：`pyproject.toml` 的 `[project].version`，`config.py` 自动读取
- 遵循 [语义化版本](https://semver.org/lang/zh-CN/) 与 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)
- 变更历史见 [CHANGELOG.md](CHANGELOG.md)

## 📄 许可证

[MIT](LICENSE) © 2026 Rebelchen

## 🙏 致谢

- [DeepSeek](https://www.deepseek.com/) — 大模型 API
- [pywebview](https://pywebview.flowrl.com/) — 桌面窗口容器
- [KaTeX](https://katex.org/) — 公式渲染
- [Pygments](https://pygments.org/) — 代码高亮
