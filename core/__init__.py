"""
deepseek_client.core — 核心逻辑层

包含：
  - chat.py           DeepSeek API 会话管理（流式/非流式、Function Calling、自动重试、取消）
  - tools.py          AI 可调用的工具集（本地文件操作 + 联网搜索）
  - search.py         联网搜索与网页抓取实现（Bing RSS，纯标准库）
  - history.py        对话历史记录的保存/加载/解析（HTML + TXT 双格式）
  - html_renderer.py  Markdown → HTML 渲染引擎（Pygments 代码高亮 + KaTeX 公式）
  - prompts.py        系统提示词构建（UI 层共用）

依赖关系：
  tools.py          → config.py (PROJECT_ROOT / SAVE_DIR)
  tools.py          → core/search.py (web_search / fetch_webpage)
  chat.py           → config.py（所有 API 参数）
  prompts.py        → config.py (ENABLE_SEARCH)
  history.py        → 无其他模块依赖（独立工具类）
  html_renderer.py  → 无其他模块依赖（独立渲染引擎）
"""
