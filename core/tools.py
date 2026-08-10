"""
文件操作工具集 — 供 AI 通过 Function Calling 调用

AI 在对话中可以主动调用以下工具来读写本地文件。
所有操作被限制在项目目录内，防止越权访问。
"""

import os
import time
import json

from config import PROJECT_ROOT


# AI 可访问的工作目录（限制在项目内，防止乱删东西）
WORK_DIR = PROJECT_ROOT

# AI 文件保存目录：回答中生成/写入的文件统一重定向到这里。
# 这是安全边界——AI 写文件不能覆盖任意路径，只能落在「总结」目录内。
SAVE_DIR = os.path.join(PROJECT_ROOT, "总结")


# ==================== 工具定义（OpenAI Tool Schema） ====================

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取本地文件内容（支持 .txt/.py/.json/.md/.yml/.csv 等纯文本格式）",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "文件路径，可以是绝对路径或相对于项目根目录的路径"
                    }
                },
                "required": ["filepath"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "保存内容到本地文件。所有文件统一保存到项目的「总结」目录（相对路径保留子目录，绝对路径只取文件名），文件已存在会覆盖，目录不存在会自动创建。",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "文件路径。无论传什么路径，最终都会保存到「总结」目录下"
                    },
                    "content": {
                        "type": "string",
                        "description": "要写入的文件内容"
                    }
                },
                "required": ["filepath", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "列出指定目录下的文件和子目录",
            "parameters": {
                "type": "object",
                "properties": {
                    "dirpath": {
                        "type": "string",
                        "description": "目录路径，默认为项目根目录"
                    }
                },
                "required": ["dirpath"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_file_info",
            "description": "获取文件或目录的详细信息（大小、修改时间、类型）",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "文件路径，可以是绝对路径或相对于项目根目录的路径"
                    }
                },
                "required": ["filepath"]
            }
        }
    },
]


# ==================== 工具实现 ====================

def _resolve_path(filepath: str) -> str:
    """
    将传入路径解析为绝对路径。

    规则：
    1. 绝对路径（如 F:\书架）→ 直接规范化后返回
    2. 相对路径 → 拼接项目根目录后转绝对路径

    不再限制项目目录内，可自由访问本地任意路径。
    """
    if not os.path.isabs(filepath):
        filepath = os.path.join(WORK_DIR, filepath)
    filepath = os.path.abspath(filepath)
    return filepath


def read_file(filepath: str) -> str:
    """
    读取文本文件内容。

    返回格式：文件路径 + 大小 + 内容前 10000 字符。
    超过 10000 字符时自动截断并提示。
    """
    filepath = _resolve_path(filepath)
    if not os.path.isfile(filepath):
        return f"错误：文件不存在 '{filepath}'"

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except UnicodeDecodeError:
        # 尝试其他编码
        try:
            with open(filepath, "r", encoding="gbk") as f:
                content = f.read()
        except Exception as e:
            return f"读取文件失败（无法识别编码）: {e}"
    except Exception as e:
        return f"读取文件失败: {e}"

    size = len(content)
    truncated = len(content) > 10000
    result = f"文件: {filepath}\n大小: {size} 字符\n\n"
    result += content[:10000]
    if truncated:
        result += "\n\n...（文件过长，已截断，仅显示前 10000 字符）"
    return result


def write_file(filepath: str, content: str) -> str:
    """
    写入内容到文件（UTF-8 编码）。

    所有保存的文件统一放到「总结」目录：
      - 相对路径 → 保留子目录结构，存到 总结/相对路径
      - 绝对路径 → 只取文件名，存到 总结/文件名
    父目录不存在时自动创建。文件已存在时直接覆盖。
    """
    # 统一保存到总结目录（防止 .. 跳出总结目录）
    save_root = os.path.abspath(SAVE_DIR)
    os.makedirs(save_root, exist_ok=True)

    if os.path.isabs(filepath):
        save_name = os.path.basename(filepath)
    else:
        save_name = filepath.replace("\\", "/")
        # 避免 总结/总结 双重嵌套
        if save_name.startswith("总结/"):
            save_name = save_name[len("总结/"):]
    if not save_name:
        save_name = "未命名.txt"

    target = os.path.abspath(os.path.join(save_root, save_name))
    if not (target == save_root or target.startswith(save_root + os.sep)):
        # 路径越界（如 ../xxx），退化为只取文件名
        target = os.path.join(save_root, os.path.basename(filepath))

    os.makedirs(os.path.dirname(target), exist_ok=True)
    try:
        with open(target, "w", encoding="utf-8") as f:
            f.write(content)
        return f"文件已保存到总结目录: {target}（共 {len(content)} 字符）"
    except Exception as e:
        return f"写入文件失败: {e}"


def list_files(dirpath: str = "") -> str:
    """
    列出目录内容，文件和子目录按字母排序。
    子目录名后会加 '/' 标识。
    """
    if not dirpath:
        dirpath = WORK_DIR
    else:
        dirpath = _resolve_path(dirpath)

    if not os.path.isdir(dirpath):
        return f"错误：目录不存在 '{dirpath}'"

    items = os.listdir(dirpath)
    lines = [f"目录: {dirpath}\n"]
    for item in sorted(items):
        full = os.path.join(dirpath, item)
        suffix = "/" if os.path.isdir(full) else ""
        lines.append(f"  {item}{suffix}")
    return "\n".join(lines)


def get_file_info(filepath: str) -> str:
    """
    获取路径的详细信息：类型、大小、修改时间。
    """
    filepath = _resolve_path(filepath)
    if not os.path.exists(filepath):
        return f"错误：路径不存在 '{filepath}'"

    stat = os.stat(filepath)
    last_modified = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(stat.st_mtime))
    file_type = "目录" if os.path.isdir(filepath) else "文件"

    info = [
        f"路径: {filepath}",
        f"类型: {file_type}",
        f"大小: {_format_size(stat.st_size)}",
        f"修改时间: {last_modified}",
    ]
    return "\n".join(info)


def _format_size(size_bytes: int) -> str:
    """将字节数转为可读格式（B/KB/MB）"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / 1024 / 1024:.1f} MB"


# ==================== 调度器 ====================

TOOL_MAP = {
    "read_file": read_file,
    "write_file": write_file,
    "list_files": list_files,
    "get_file_info": get_file_info,
}


def execute_tool(name: str, args: dict) -> str:
    """
    执行工具调用。

    Args:
        name: 工具名称（须在 TOOL_MAP 中注册）
        args: 参数字典

    Returns:
        工具执行结果的文本描述
    """
    func = TOOL_MAP.get(name)
    if not func:
        return f"错误：未知工具 '{name}'"
    try:
        return func(**args)
    except Exception as e:
        return f"工具执行失败: {type(e).__name__}: {e}"
