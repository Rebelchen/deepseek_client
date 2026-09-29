# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)
与 [语义化版本](https://semver.org/lang/zh-CN/)。版本号单一事实来源为 `pyproject.toml`。

## [Unreleased]

### 新增
- 多对话并行：会话改为按「槽（slot）」隔离，点右上角「新窗口」在**同一进程**内再开一个独立对话窗口（各自一份上下文、各自一条流、可同时在生成），不必再 `python main.py --port 5001` 多开进程；上限 `config.MAX_WINDOWS = 4`（每窗口一路 SSE 长连接，WebView2 对同一 host 的并发连接约 6）
- 所有 `/api/*` 按 slot 定位会话（GET 用 `?slot=`，POST 用请求体 `slot`；缺省 `main` 即改造前的唯一会话，老行为不变）；新增 `/api/new-window`，新窗口沿用发起方的来源/模型但不共用上下文，槽编号只增不复用
- `ui_state.json` 的来源/模型改为按槽分键（`slots.{槽id}.source/model`），各窗口记住自己的来源；原扁平记录视为主槽记录继续兼容读取，`pageZoom` 仍全局共享
- 同一个窗口内再次发起生成会被拒（409）并提示先停止或等待结束——防止两条流并发写坏同一份上下文
- 新增小米 MiMo 来源（`https://api.xiaomimimo.com/v1`，OpenAI 兼容端点），模型 `mimo-v2.6-flash` / `mimo-v2.6-pro`（1M 上下文、最大输出 128k、思考链展示、支持识图）；密钥通过环境变量 `XIAOMI_MIMO_API_KEY` 提供。实测该 Key 只认按量端点，打 `token-plan-cn.xiaomimimo.com` 返回 401 `invalid_key`；思考默认开启且该端点会忽略采样参数（`temperature` / `top_p` 被强制回默认值），又无 `reasoning_effort` 档位（只有 `thinking.type` 开/关），故本来源 `supports_reasoning_effort=False`
- 新增阶跃星辰 StepFun 来源（`https://api.stepfun.com/v1`，OpenAI 兼容端点），模型 `step-5-preview`（Step 5 Preview：1M 上下文、最大输出 64k、思考链展示、支持识图）；密钥通过环境变量 `STEPFUN_API_KEY` 提供。实测该 Key 走按量端点可用，打 Step Plan 订阅通道 `/step_plan/v1` 返回 400 `you have no active step plan subscription`；`reasoning_effort` 三档 `low`/`medium`/`high` 生效（思考长度随档位递增），故本来源 `supports_reasoning_effort=True`
- opencode GO 新增模型 `deepseek-v4.1-flash`（网关 2026-09-10 上架，支持图片输入，价格与 `deepseek-v4-flash` 相同）
- 多 API 来源支持：新增 opencode GO 套餐来源（`https://opencode.ai/zen/go/v1`），模型 `deepseek-v4-flash` / `deepseek-v4-pro`
- 新增智谱 BigModel 来源（`https://open.bigmodel.cn/api/paas/v4`，OpenAI 兼容端点），模型 `glm-5.3-flash`；密钥通过环境变量 `ZBIGMODEL_API_KEY` 提供
- 新增阿里云百炼 Token Plan 来源（`https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`，OpenAI 兼容端点），模型 `qwen3.8-flash` / `qwen3.8-max`（均默认开启思考、支持识图）；密钥通过环境变量 `BAILIAN_TOKEN_PLAN_API_KEY` 提供。Token Plan 的 `sk-sp-` Key 必须走套餐专属端点，跨区域端点返回 401
- 图片识别：`glm-5.3-flash` 支持发送图片（🖼️ 按钮选图 / 输入框直接粘贴截图，PNG/JPG/WebP/GIF，最多 4 张、单张 ≤5MB）；不支持识图的来源/模型自动降级——图片转为「[图片 xN]」占位符继续对话，历史记录不落盘 base64
- 页面缩放：`Ctrl+滚轮` / `Ctrl+=` / `Ctrl+-` / `Ctrl+0` 整页等比缩放（50%~200%，效果与浏览器网页缩放一致），比例持久化到本地 `ui_state.json`（跨端口/重启稳定），无记录时默认 100%
- 输入框换行：`Shift+Enter` / `Ctrl+Enter` / `Alt+Enter` 均可换行（仅裸 `Enter` 发送）；输入法候选确认的 Enter 不再误发送
- 一键换源：窗口顶部「来源 / 模型」下拉框，运行中即时切换（官方 API ⇄ opencode GO ⇄ 智谱 BigModel），无需重启；自动记住上次使用的来源/模型，下次启动恢复（记录失效或密钥缺失时回退默认）
- 新增 `OPENCODE_GO_API_KEY`、`SOURCE` 配置项（环境变量），`SOURCE` 决定启动时的默认来源
- 新增 `scripts/setup_venv_keys.ps1`：一键把密钥写入虚拟环境激活脚本，`启动.bat` 启动时自动激活 venv 并注入
- 新增本地 Function Calling 联网工具 `web_search`（Bing RSS 搜索）与 `fetch_webpage`（网页正文提取），无需额外 API Key，任何来源都能联网
- 系统提示词改为指导 AI 主动调用联网工具查证时效性问题（不再声称“没有联网能力”）

### 安全
- 移除 `.env` 文件读取：密钥只允许通过环境变量注入，代码不读取任何 `.env`，仓库中不保存真实密钥
- 删除项目根目录的真实密钥文件 `.env`，密钥迁移至虚拟环境激活脚本
- 密钥缺失时弹窗提示（`pythonw` 静默启动也能看到错误）

### 变更
- 默认来源改为 `opencode_go`（DeepSeek 官方 API 涨价后成本更低）
- 对齐 DeepSeek 官网最新模型：官方来源模型改为 `deepseek-flash`（DeepSeek-V4.1-Flash，思考模式默认开启）与 `deepseek-v4-pro`，移除已下线的旧模型名 `deepseek-v4-flash` / `deepseek-chat` / `deepseek-reasoner`
- 识图能力扩充：官方 `deepseek-flash`、opencode GO `deepseek-v4.1-flash` 支持图片输入（官方/GO 的 `deepseek-v4-pro` 与 GO 的 `deepseek-v4-flash` 不支持），图片入口的灰色提示同步更新
- 思考模型改由 `SOURCES[*].reasoning_models` 显式声明（替代模型名子串匹配，避免 `glm-5.3-flash` 被误判为思考模型），`REASONING_EFFORT` 取值对齐官网 `low` / `high` / `max`，默认 `high`
- `ChatSession` 新增 `configure(source, model)`，按来源区分能力开关（联网搜索 / `reasoning_effort` 仅官方来源生效）
- 系统提示词随来源自动刷新（GO 来源不再提示联网搜索能力）

### 修复
- 修复「停止」的后端取消请求被排队：旧版 `/api/chat` 在整个流式期间持有全局会话锁，`/api/cancel` 拿不到锁只能等回答结束（实际只靠前端 fetch abort 兜底）。现改为每槽 `busy` 标记独占生成权 + 短时持锁，取消立即返回（本机实测 0.016s）
- 修复多开窗口会互相污染的问题：上下文、来源/模型、取消标记、历史落盘文件现在全部按会话槽独立
- 修复思考模式 + 工具调用时非流式 `ask()` 未回传历史轮次 `reasoning_content` 的问题：官方「思考模式」指南要求携带 `tools` 的请求必须完整回传，否则 API 返回 400
- 连接类瞬时错误（`Connection error`，网络抖动/网关 keep-alive 连接失效）纳入指数退避重试：此前被当作客户端错误直接放弃，长对话工具循环中一次抖动即整轮失败
- 修复点击 AI 回复/历史记录中的外部链接在应用窗口内导航的问题：外链统一改用系统默认浏览器打开（`/api/open-external`，仅允许 http/https），并开启 pywebview `OPEN_EXTERNAL_LINKS_IN_BROWSER`；窗口意外离开应用页面时自动拉回（此前链接目标网络不通时，整个界面会被浏览器错误页顶掉，只能重启）
- 修复 opencode GO 网关新增校验后所有请求报 400 `MissingSessionID` 的问题：来源配置新增 `session_header`，ChatSession 为每个对话生成稳定 uuid 并以 `x-opencode-session` 请求头携带（新对话自动换新 id）
- 修复 OpenAI 兼容网关（opencode GO）流式响应中带 `choices` 为空的数据块（usage/结束块）时抛 `list index out of range` 的问题，现自动跳过空块
- 修复严格网关拒绝请求的问题（`assistant tool_calls 必须紧随 tool 消息`）：发送前自动修复会话中的悬空 tool_calls（异常中断残留），并剔除 timestamp 等非协议字段
- 流式工具调用改用网关下发的真实 `tool_call_id`（不再本地编造），提高与严格 OpenAI 兼容网关的兼容性

## [1.4.0] - 2026-08-10

### 安全
- API Key 不再硬编码：改为环境变量 `DEEPSEEK_API_KEY` 或项目根 `.env` 提供，启动时校验
- 新增 `.env.example` 配置模板

### 修复（公式与排版）
- 修复 markdown 将数学公式内的成对下划线/星号误判为斜体/加粗，导致 KaTeX 无法渲染的问题
- 修复单行 `$$...$$` 公式被误认为未闭合块、吞并后续正文/标题/表格的问题
- 兼容模型输出的双重反斜杠（`\\(...\\)`、`\\frac` 等），渲染前自动归一化
- 修复 `$10^{500}$` 等公式被"价格转义"逻辑误伤的问题
- 修复 KaTeX 手动渲染未传定界符配置、导致 `$...$` 行内公式不渲染的问题
- 移除页面外层滚动条，聊天区独立滚动

### 变更
- 删除已弃用的 Tkinter 备用界面（`ui/app.py`）与 matplotlib 公式图片渲染器（`core/math_render.py`）
- 清理调试/测试残留文件与备份目录，移除对应可选依赖
- 新增 GitHub Actions CI（Python 3.10 / 3.11 / 3.12 运行单元测试）
- 新增 `deepseek-client` 命令行入口，完善项目元数据（关键词、分类器、许可证）
- 新增 MIT `LICENSE` 与 `.editorconfig`

## [1.3.0] - 2026-08-03

### 新增
- 回答完成气泡音通知（Web Audio 合成）
- 界面"停止"按钮：可中断长回答，节省 token，半截回复不落盘
- 历史记录删除按钮、总结目录"打开文件夹"入口
- 流式显示增强：标题 / 列表 / 引用 / 表格 / 链接渲染
- KaTeX 本地化（`static/katex/`，断网可用，历史 HTML 同步切换）
- `--port` 参数（多开实例）
- 会话自动保存：每轮回答完成后落盘，同一会话覆盖同一文件，不再产生重复副本
- 端口冲突自动切换空闲端口；Flask 就绪由轮询探测替代固定 sleep
- 单元测试（`tests/`，运行：`python -m unittest discover -s tests -v`）

### 优化
- 重试仅针对服务器/网络错误，客户端错误（400/401 等）立即返回
- `ask()` 与 `ask_stream()` 请求参数统一（reasoning_effort、联网搜索、stream_options）
- 系统提示词抽离到 `core/prompts.py`
- 版本号单一事实来源：`pyproject.toml`

## [1.2.0] - 2026-06-24

### 重大更新 — HTML 渲染引擎
- 聊天显示区重构为 HTML 渲染（tkinterweb 嵌入式浏览器）
- Markdown 渲染 — 标题、列表、表格、引用等完整支持
- Pygments 代码高亮 — 70+ 语言的语法着色（github-dark 风格）
- KaTeX 公式渲染 — LaTeX 数学公式的官网级渲染效果（无需安装 LaTeX）
- 仿 DeepSeek 官网 CSS — 消息气泡、头像、颜色方案
- 欢迎页面展示功能引导
- 新增 `core/html_renderer.py` 渲染引擎模块

### 废弃
- `core/math_render.py` 的 matplotlib 图片渲染不再用于聊天显示

## [1.1.0] - 2026-06-24

### 新增
- LaTeX 数学公式图片渲染 — 使用 matplotlib 将 `\(...\)` / `\[...\]` 公式渲染为图片嵌入聊天区
- 含中文公式自动回退到 Unicode 数学符号（α、β、²、·）
- 公式渲染缓存 — 相同公式不重复渲染
- 新增 `core/math_render.py` 数学公式渲染模块

### 优化
- 项目目录重命名 `deepseek client` → `deepseek_client`（消除空格路径问题）
- `requirements.txt` → `pyproject.toml`（现代 Python 项目标准）
- 新增 `.gitignore`，屏蔽缓存 / 历史 / 虚拟环境
- `CHANGELOG.txt` → `CHANGELOG.md`（Markdown 格式）

### 文档
- `docs/architecture.md` — 新增架构设计文档
- `docs/API.md` — 新增 API 参考文档
- `README.md` — 项目索引

## [1.0.0] - 2026-06-08

### 新增
- 完整的 Tkinter 图形聊天界面
- 基于 DeepSeek API 的本地问答
- Function Calling 文件操作工具箱（读/写/列目录/文件信息）
- 对话历史自动保存 TXT 到 `history/` 目录
- 左侧历史记录浏览、加载、删除
- 国内 HuggingFace 镜像加速
- API 调用超时和自动重试机制
- 中文字体优化（微软雅黑）
