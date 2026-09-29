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
  默认来源的 API Key（默认 opencode GO 的 OPENCODE_GO_API_KEY，或官方 DEEPSEEK_API_KEY）
  仅通过环境变量提供（推荐配置在虚拟环境激活脚本，见 scripts/setup_venv_keys.ps1）；
  其余参数集中在 config.py，修改后重启生效。运行中可在界面顶部下拉框一键切换来源/模型。

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

    # 启动前校验默认来源的 API Key：缺失时给出明确指引，避免运行时才报错
    # （放在参数解析之后，保证 --help 等操作无需密钥也能执行）
    # 默认来源（config.SOURCE）见 config.py，界面内可随时切换其他来源
    default_info = config.SOURCES.get(config.SOURCE, {})
    if not default_info.get("api_key"):
        msg = (
            f"未配置 {default_info.get('env_key', 'API Key')}（默认来源「{default_info.get('label', config.SOURCE)}」需要）。\n\n"
            f"请将其设置为环境变量后重新启动。\n"
            f"推荐运行 scripts/setup_venv_keys.ps1 写入虚拟环境激活脚本。"
        )
        logging.error("%s", msg)
        # pythonw 启动时无控制台窗口，弹窗提示避免"点了没反应"
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("DeepSeek 本地问答 - 缺少 API Key", msg)
            root.destroy()
        except Exception:
            pass
        sys.exit(1)

    launch(port=args.port)


if __name__ == "__main__":
    main()
