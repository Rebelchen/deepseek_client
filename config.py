"""
DeepSeek 本地问答系统 — 全局配置

所有可调参数集中在此文件，方便维护和版本管理。
修改参数后重启程序生效。

维护规约：
  - 所有常量用大写命名，添加类型注解和说明文档
  - 涉及敏感信息（API Key）不要提交到 Git
  - 新增配置先问自己：这个值用户可能想改吗？→ 是则放这里，否则就近定义
"""

import os
from pathlib import Path

try:
    import tomllib
except ImportError:  # Python 3.10 及以下
    tomllib = None

# ============================================================
# 版本信息
# ============================================================


def _load_version() -> str:
    """从 pyproject.toml 读取版本号（单一事实来源），读取失败时回退。"""
    pyproject_path = Path(__file__).parent / "pyproject.toml"
    try:
        if tomllib is None:
            raise OSError("tomllib 不可用（Python < 3.11）")
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
        return str(data["project"]["version"])
    except Exception:
        return "1.4.0"


VERSION = _load_version()
"""当前版本号。单一事实来源为 pyproject.toml 的 [project].version"""

# ============================================================
# 网络 & 镜像
# ============================================================

# HuggingFace 镜像（国内加速）
# 如果遇到模型下载慢，可换其他镜像源（如 hf-mirror.com / hf.llm.zone）
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

# ============================================================
# DeepSeek API 配置
# ============================================================


def _load_api_key() -> str:
    """读取 DeepSeek API Key，按优先级：环境变量 DEEPSEEK_API_KEY > 项目根 .env。

    密钥属于敏感信息，严禁硬编码进代码/提交到 Git：
      - 环境变量：`set DEEPSEEK_API_KEY=sk-...`（PowerShell）或导出后运行
      - .env 文件：项目根目录新建 `.env`（已被 .gitignore 排除），格式：
          DEEPSEEK_API_KEY=sk-...
    """
    # 1) 环境变量优先
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key

    # 2) 回退到项目根目录的 .env（简单 key=value 解析，不引入额外依赖）
    env_path = Path(__file__).parent / ".env"
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("DEEPSEEK_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
                if key:
                    return key
    except FileNotFoundError:
        pass
    return ""


API_KEY = _load_api_key()
"""DeepSeek API 密钥。通过环境变量 DEEPSEEK_API_KEY 或项目根 .env 提供，禁止硬编码。"""

BASE_URL = "https://api.deepseek.com"
"""API 端点地址。DeepSeek 官方接口"""

MODEL = "deepseek-v4-flash"
"""使用的模型名称。
   deepseek-chat       — 普通对话模型（无深度思考过程）
   deepseek-reasoner   — 深度思考模型（展示思考链）
   deepseek-v4-flash   — 最新推理模型（展示思考链）
"""

API_TIMEOUT = 60
"""API 请求超时时间（秒）。网络差时可适当调大"""

API_MAX_RETRIES = 3
"""API 调用失败时的最大重试次数"""

# ── 深度思考（Reasoning） ──

SHOW_REASONING = True
"""是否在界面中展示 AI 的深度思考过程（reasoning_content）。
   仅推理模型（deepseek-reasoner, deepseek-v4 等）支持此功能。
   关闭后思考过程不显示，但仍会传递给 API 以避免报错。"""

REASONING_EFFORT = "medium"
"""推理强度（low / medium / high）。
   仅 deepseek-reasoner 模型支持此参数。
   控制 AI 在回答前的"思考深度"。
   注意：deepseek-v4-flash 不支持此参数，会静默忽略。"""

ENABLE_SEARCH = True
"""是否开启 DeepSeek 联网搜索功能。
   设为 True 后，AI 可以联网获取最新信息来回答时效性问题。
   注意：
     - 需 API Key 在 DeepSeek 开发者平台已开通联网搜索权限
     - 响应延迟会增加 2-5 秒（搜索需要时间）
     - 此功能通过 extra_body 传递，仅 DeepSeek API 支持"""

TEMPERATURE = 0.7
"""生成温度（0-2）。值越低回答越确定/保守，越高越有创造力。默认 0.7"""

TOP_P = 1.0
"""核采样概率阈值（0-1）。与 temperature 二选一使用，通常保持默认 1.0 即可"""

MAX_TOKENS = 40960
"""每次回答的最大 token 数。限制输出长度，节省 token 消耗"""

PRESENCE_PENALTY = 0.0
"""话题重复惩罚（-2 到 2）。正值鼓励讨论新话题，负值让 AI 更集中在已有话题"""

FREQUENCY_PENALTY = 0.0
"""频率惩罚（-2 到 2）。正值减少重复用词，负值允许更多重复"""

STOP = None
"""停止序列。遇到指定字符串时停止生成。可设字符串或列表，例如 ["\n\n", "结束"]"""

# ============================================================
# 文件路径
# ============================================================

# 项目根目录（config.py 所在目录）
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

# 对话历史存储目录
HISTORY_DIR = os.path.join(PROJECT_ROOT, "history")

# ============================================================
# 应用界面
# ============================================================

APP_TITLE = "DeepSeek 本地问答"
"""窗口标题"""

WINDOW_WIDTH = 1200
"""窗口默认宽度（像素）"""

WINDOW_HEIGHT = 800
"""窗口默认高度（像素）"""
