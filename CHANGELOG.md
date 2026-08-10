# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)
与 [语义化版本](https://semver.org/lang/zh-CN/)。版本号单一事实来源为 `pyproject.toml`。

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
