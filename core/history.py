"""
聊天记录管理 — 保存/加载/列出/解析历史对话

格式：
  - .html（推荐，保留公式渲染 + 代码高亮）
  - .txt（纯文本回退）
"""

import os
import re
import json
from datetime import datetime


def generate_title(messages: list) -> str:
    """
    根据对话内容生成有意义的标题。

    策略：
    1. 取第一条用户消息中的有效内容（去掉文件路径等噪声）
    2. 如果过长或太短，取前 30 字符
    3. 如果第一条消息是纯路径/文件名，尝试取第二条

    Returns:
        清理后的标题字符串（不含时间戳）
    """
    # 找第一条有实质内容的用户消息
    for m in messages:
        if m["role"] != "user":
            continue
        text = m["content"].strip()
        # 去掉纯文件路径（太长且无意义）
        if text and not text.startswith(("D:\\", "C:\\", "E:\\", "/")):
            # 去掉路径中的文件名部分
            text = re.sub(r'[A-Z]:\\.*?\\([^\\]+)', r'\1', text)
            # 取前 30 个字符，去掉首尾空格
            title = text[:30].strip()
            # 如果截断了，加省略号
            if len(text) > 30:
                title += "…"
            return title if title else "对话"

    return "对话"


def save_conversation(messages: list, history_dir: str) -> str:
    """
    将一次对话保存为 TXT 文件。

    文件名格式：yyyy-MM-dd_HH-mm-ss_标题.txt
    标题由 generate_title 自动生成，更可读。

    Args:
        messages: [{"role": str, "content": str}, ...] 消息列表
        history_dir: 历史记录存储目录

    Returns:
        保存的文件完整路径
    """
    os.makedirs(history_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    title = generate_title(messages)

    filename = f"{timestamp}_{title}.txt"
    # 移除 Windows 文件名非法字符（保留中文）
    filename = "".join(
        c for c in filename if c not in '<>:"/\\|?*\n\r'
    ).strip()
    # 如果清理后为空或有其他问题，回退
    if not filename or filename == ".txt":
        filename = f"{timestamp}_对话.txt"
    filepath = os.path.join(history_dir, filename)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"对话时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("=" * 50 + "\n\n")
        for msg in messages:
            role = "我" if msg["role"] == "user" else "AI"
            ts = msg.get("timestamp", "")
            if ts:
                # 只显示 HH:MM:SS，简洁
                time_part = ts[-8:] if len(ts) >= 8 else ts
                f.write(f"[{time_part}] 【{role}】\n{msg['content']}\n\n")
            else:
                f.write(f"【{role}】\n{msg['content']}\n\n")

    return filepath


def save_conversation_html(messages: list, history_dir: str, chat_html: str = "") -> str:
    """
    将一次对话保存为 HTML 文件（保留公式渲染 + 代码高亮）。

    格式：
      - 渲染后的聊天内容（打开即看）
      - 内嵌 <script id="messages-data"> JSON 数据（用于恢复会话）

    Args:
        messages: [{"role": str, "content": str, ...}, ...]
        history_dir: 历史记录存储目录
        chat_html: 已渲染的聊天区 HTML（不含<html>/<body>）

    Returns:
        保存的文件完整路径
    """
    os.makedirs(history_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    title = generate_title(messages)

    filename = f"{timestamp}_{title}.html"
    filename = "".join(c for c in filename if c not in '<>:"/\\|?*\n\r').strip()
    if not filename or filename == ".html":
        filename = f"{timestamp}_对话.html"
    filepath = os.path.join(history_dir, filename)

    html = _render_conversation_html(messages, chat_html, title)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html)

    return filepath


def overwrite_conversation_html(filepath: str, messages: list, chat_html: str = "") -> str:
    """
    用同一文件路径覆盖保存对话（文件名与首次保存时一致）。

    用于同一会话的增量保存：第一轮生成文件，后续每轮回答结束
    都覆盖同一个文件，避免历史目录堆积重复副本。

    Args:
        filepath: 已存在的历史文件完整路径
        messages: 消息列表
        chat_html: 已渲染的聊天区 HTML

    Returns:
        写入的文件完整路径
    """
    title = generate_title(messages)
    html = _render_conversation_html(messages, chat_html, title)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html)
    return filepath


def _render_conversation_html(messages: list, chat_html: str, title: str) -> str:
    """渲染完整历史对话 HTML 页面（不含落盘逻辑）。"""
    # 序列化 messages（排除 tool 调用、system 等）
    save_msgs = []
    for m in messages:
        if m["role"] in ("user", "assistant"):
            entry = {"role": m["role"], "content": m.get("content", "")}
            # 保留 reasoning_content（DeepSeek 推理模型要求回传）
            if m.get("reasoning_content"):
                entry["reasoning_content"] = m["reasoning_content"]
            save_msgs.append(entry)
    messages_json = json.dumps(save_msgs, ensure_ascii=False)

    rendered = chat_html or "<p>对话内容</p>"

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<link rel="stylesheet" href="../static/katex/katex.min.css">
<script defer src="../static/katex/katex.min.js"></script>
<script defer src="../static/katex/contrib/auto-render.min.js"
    onload="renderMathInElement(document.body, {{
        delimiters: [
            {{left: '$$', right: '$$', display: true}},
            {{left: '$', right: '$', display: false}},
            {{left: '\\\\(', right: '\\\\)', display: false}},
            {{left: '\\\\[', right: '\\\\]', display: true}}
        ],
        throwOnError: false,
        trust: true
    }});">
</script>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: 'Cambria Math', 'STIX Two Text', -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans SC', 'Microsoft YaHei', sans-serif; background: #f5f5f5; color: #1a1a2e; font-size: 15px; line-height: 1.7; padding: 16px; }}
.chat {{ max-width: 800px; margin: 0 auto; display: flex; flex-direction: column; gap: 12px; }}
.msg {{ padding: 12px 16px; border-radius: 12px; background: #fff; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
.msg.user {{ background: #e8f4fd; }}
.msg-header {{ font-size: 13px; font-weight: 600; color: #666; margin-bottom: 4px; }}
.msg-body h1, .msg-body h2, .msg-body h3 {{ margin: 16px 0 8px; }}
.msg-body h1 {{ font-size: 20px; border-bottom: 1px solid #eee; padding-bottom: 6px; }}
.msg-body h2 {{ font-size: 18px; }}
.msg-body h3 {{ font-size: 16px; }}
.msg-body p {{ margin-bottom: 8px; }}
.msg-body ul, .msg-body ol {{ padding-left: 24px; margin: 8px 0; }}
.msg-body blockquote {{ border-left: 3px solid #1a73e8; padding: 8px 16px; margin: 8px 0; background: #f8f9fa; }}
.msg-body code {{ font-family: 'Cascadia Code', 'Fira Code', monospace; font-size: 13px; background: #f0f0f0; padding: 2px 6px; border-radius: 4px; color: #d63384; }}
.msg-body pre {{ background: #0d1117; border-radius: 8px; padding: 16px; margin: 8px 0; overflow-x: auto; }}
.msg-body pre code {{ background: transparent; padding: 0; color: #e6edf3; }}
.katex-display {{ text-align: center; margin: 12px 0; overflow-x: auto; }}
.reasoning {{ font-size: 13px; border-left: 3px solid #e0a800; padding-left: 8px; }}
.reasoning summary {{ cursor: pointer; color: #b8860b; font-weight: 600; }}
.reasoning summary:hover {{ background: #fff8e1; }}
.reasoning-content {{ background: #fffbef; padding: 8px 12px; border-radius: 0 8px 8px 8px; color: #666; line-height: 1.6; max-height: 500px; overflow-y: auto; font-family: 'Cascadia Code', 'Fira Code', 'Consolas', monospace; white-space: pre-wrap; font-size: 12px; }}
.timestamp {{ color: #999; font-size: 11px; text-align: right; margin-top: 8px; }}
</style>
<script id="messages-data" type="application/json">{messages_json}</script>
</head>
<body>
<div class="chat">
<div class="meta" style="text-align:center;color:#999;font-size:12px;padding:8px;">
  保存时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
</div>
{rendered}
</div>
</body>
</html>"""
    return html


def list_conversations(history_dir: str) -> list:
    """
    列出所有历史对话文件。

    Returns:
        [(文件名, 文件路径), ...]
        按文件修改时间倒序排列（最新的在前）
    """
    if not os.path.exists(history_dir):
        return []

    files = []
    for fname in os.listdir(history_dir):
        if fname.endswith((".txt", ".html")):
            path = os.path.join(history_dir, fname)
            files.append((fname, path))

    files.sort(key=lambda x: os.path.getmtime(x[1]), reverse=True)
    return files


def load_conversation(filepath: str) -> str:
    """
    读取历史对话文件的全部文本内容（用于显示）。

    Args:
        filepath: TXT 文件路径

    Returns:
        文件全部文本
    """
    with open(filepath, "r", encoding="utf-8") as f:
        return f.read()


def parse_conversation(filepath: str) -> list:
    """
    将历史对话文件解析为消息列表，用于恢复会话。

    TXT 格式：
        【我】
        消息内容（可多行）

        【AI】
        回复内容（可多行）

    HTML 格式：提取 <script id="messages-data"> 中的 JSON

    Returns:
        [{"role": "user"/"assistant", "content": str}, ...]
        不包含 system 消息。
    """
    messages = []
    text = load_conversation(filepath)

    # ── HTML 格式：从 JSON script 标签提取结构化数据 ──
    if filepath.endswith(".html"):
        match = re.search(r'<script id="messages-data"[^>]*type="application/json">(.*?)</script>', text, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(1))
                return [m for m in data
                        if m.get("role") in ("user", "assistant") and m.get("content")]
            except json.JSONDecodeError:
                pass  # JSON 解析失败，回退到 TXT 解析

    # ── TXT 格式解析：按【角色】分割 ──
    # parts 结构：["对话时间...\n===\n", "我", "消息内容\n\n", "AI", "回复内容\n\n", ...]
    # 从 index 1 开始成对读取（index 0 是元信息头）
    parts = re.split(r'【(我|AI)】', text)

    for i in range(1, len(parts) - 1, 2):
        role_label = parts[i]      # "我" 或 "AI"
        content = parts[i + 1]     # 消息正文（可能含换行）

        role = "user" if role_label == "我" else "assistant"
        content = content.strip()

        if content:
            messages.append({"role": role, "content": content})

    return messages
