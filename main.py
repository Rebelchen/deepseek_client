#!/usr/bin/env python3
"""
DeepSeek 本地问答系统 — 程序入口

调用链：
    main.py
      → ui/webview_app.launch()
          ├─ 初始化 ChatSession（系统提示词 + 工具注册）
          ├─ 后台线程启动 Flask（SSE 流式接口）
          ├─ 轮询等待 Flask 就绪
          └─ 打开 pywebview 桌面窗口（Edge WebView2）
                └─ core/chat.py → DeepSeek API

架构说明：
  - UI 层（ui/）与核心层（core/）解耦，核心层不依赖任何界面代码
  - 界面为内联 HTML/CSS/JS，通过 HTTP + SSE 与 Flask 后端异步通信
  - 核心能力：流式输出、深度思考展示、Function Calling、KaTeX 公式渲染

启动方式：
    python main.py                        # 默认启动（端口 5000）
    python main.py --port 5001            # 指定端口（多开实例）
    pip install -e . && deepseek-client   # 安装后可直接用命令启动

配置：
  API Key 通过环境变量 DEEPSEEK_API_KEY 或项目根 .env 提供（见 .env.example）；
  其余参数集中在 config.py，修改后重启生效。

文档：
  docs/ 目录包含架构设计与 API 参考；tests/ 为单元测试。

维护提示：
  - 新功能优先在 core/ 层实现，再通过 Flask 路由 / 前端 JS 对接
  - 修改前端界面直接编辑 ui/webview_app.py 中的 CHAT_HTML
"""

import argparse
import logging
import os
import sys

# 确保项目根目录在 Python 路径中，`from core.xxx import xxx` 才能正确解析
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from ui.webview_app import launch


def main():
    """启动聊天界面（pywebview + Flask 桌面窗口）。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="DeepSeek 本地问答系统")
    parser.add_argument(
        "--port", type=int, default=5000,
        help="Flask 监听端口（默认 5000），多开实例时可改用不同端口",
    )
    args = parser.parse_args()

    # 启动前校验 API Key：缺失时给出明确指引，避免运行时才报错
    # （放在参数解析之后，保证 --help 等操作无需密钥也能执行）
    if not config.API_KEY:
        logging.error(
            "未配置 DEEPSEEK_API_KEY。"
            "请设置环境变量 DEEPSEEK_API_KEY，或在项目根目录创建 .env（参考 .env.example）。"
        )
        sys.exit(1)

    launch(port=args.port)


if __name__ == "__main__":
    main()
