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


def _load_env(name: str) -> str:
    """读取环境变量中的密钥/配置项。

    安全约定：所有密钥只允许通过环境变量注入（推荐配置在虚拟环境的激活脚本中，
    见 scripts/setup_venv_keys.ps1）。本仓库任何文件都不保存真实密钥，也不读取 .env。
    """
    return os.environ.get(name, "").strip()


DEEPSEEK_API_KEY = _load_env("DEEPSEEK_API_KEY")
"""DeepSeek 官方 API 密钥。仅通过环境变量 DEEPSEEK_API_KEY 提供，禁止硬编码。"""

OPENCODE_GO_API_KEY = _load_env("OPENCODE_GO_API_KEY")
"""opencode GO 套餐 API Key。仅通过环境变量 OPENCODE_GO_API_KEY 提供。
   登录 https://opencode.ai/auth 订阅 Go 后在控制台复制，禁止硬编码。"""

ZBIGMODEL_API_KEY = _load_env("ZBIGMODEL_API_KEY")
"""智谱开放平台（BigModel）API Key。仅通过环境变量 ZBIGMODEL_API_KEY 提供。
   在 https://open.bigmodel.cn 控制台的「API Keys」页获取，格式为 xxx.yyy，禁止硬编码。"""

BAILIAN_TOKEN_PLAN_API_KEY = _load_env("BAILIAN_TOKEN_PLAN_API_KEY")
"""阿里云百炼（Model Studio）Token Plan 专用 API Key。仅通过环境变量提供。
   Token Plan 的 key 以 sk-sp- 开头，且必须配 Token Plan 专属端点使用：
   换成通用 DashScope 地址会改按量计费，换区域端点会 401，禁止硬编码。"""

STEPFUN_API_KEY = _load_env("STEPFUN_API_KEY")
"""阶跃星辰（StepFun）开放平台 API Key。仅通过环境变量 STEPFUN_API_KEY 提供。
   在 https://platform.stepfun.com 控制台的「API Keys」页创建，为无 sk- 前缀的
   长随机串（65 字符），禁止硬编码。"""

XIAOMI_MIMO_API_KEY = _load_env("XIAOMI_MIMO_API_KEY")
"""小米 MiMo 开放平台 API Key。仅通过环境变量 XIAOMI_MIMO_API_KEY 提供。
   在 https://platform.xiaomimimo.com 创建（sk- 开头 51 字符）。
   实测该端点同时接受 Authorization: Bearer 与 api-key 两种请求头。"""

SOURCE = _load_env("SOURCE") or "opencode_go"
"""默认 API 来源：opencode_go（opencode GO 套餐）/ official（DeepSeek 官方 API）
   / zbigmodel（智谱 BigModel）/ bailian_token_plan（阿里云百炼 Token Plan）
   / stepfun（阶跃星辰）/ xiaomi_mimo（小米 MiMo）。
   仅通过环境变量 SOURCE 覆盖。官方 API 涨价后可整体切到 opencode GO 或智谱。"""

# 各来源的完整配置。界面下拉框与 ChatSession.configure() 均以此为唯一事实来源。
SOURCES = {
    "official": {
        "label": "DeepSeek 官方 API",
        "base_url": "https://api.deepseek.com",
        "api_key": DEEPSEEK_API_KEY,
        "env_key": "DEEPSEEK_API_KEY",
        "default_model": "deepseek-flash",
        # 官网模型清单（https://api-docs.deepseek.com/zh-cn/quick_start/pricing）：
        #   deepseek-flash    — DeepSeek-V4.1-Flash，思考模式默认开启，支持识图
        #   deepseek-v4-pro   — DeepSeek-V4-Pro，更强的推理模型，不支持识图
        # 旧模型名 deepseek-v4-flash / deepseek-v4-flash-vision-exp / deepseek-chat /
        # deepseek-reasoner 已下线，不再列出
        "models": ["deepseek-flash", "deepseek-v4-pro"],
        # 思考模式模型清单：不支持 temperature 等采样参数，而是用 reasoning_effort 调强度
        "reasoning_models": ["deepseek-flash", "deepseek-v4-pro"],
        # 是否支持 DeepSeek 服务端私有 enable_search 参数（与本地 web_search 工具无关）
        "supports_search": True,
        "supports_reasoning_effort": True,
        # 识图模型清单：列表内的模型支持图片输入，不在清单内的模型发图会被降级/拒绝
        # 官方仅 deepseek-flash 支持图像理解（PNG/JPG/GIF/WebP，base64 data URL 或 URL）
        "vision_models": ["deepseek-flash"],
    },
    "opencode_go": {
        "label": "opencode GO",
        "base_url": "https://opencode.ai/zen/go/v1",
        "api_key": OPENCODE_GO_API_KEY,
        "env_key": "OPENCODE_GO_API_KEY",
        "default_model": "deepseek-v4-flash",
        # 模型清单来自 GO 网关目录（https://opencode.ai/zen/go/v1）：
        #   deepseek-v4-flash      — 常规模型（不支持图片）
        #   deepseek-v4.1-flash    — 2026-09-10 上架的新 Flash，支持图片输入
        #   deepseek-v4-pro        — 更强推理模型（不支持图片）
        "models": ["deepseek-v4-flash", "deepseek-v4.1-flash", "deepseek-v4-pro"],
        # GO 网关的 v4 系列为思考模型（网关不暴露 reasoning_effort 开关）
        "reasoning_models": ["deepseek-v4-flash", "deepseek-v4.1-flash", "deepseek-v4-pro"],
        # GO 网关为 OpenAI 兼容端点，不支持 DeepSeek 私有的 enable_search / reasoning_effort
        # 参数；但本地 Function Calling 的 web_search / fetch_webpage 工具不受影响
        "supports_search": False,
        "supports_reasoning_effort": False,
        # 网关要求携带会话头用于路由（缺失时报 400 MissingSessionID）；
        # ChatSession 会为每个对话生成稳定的 uuid 作为头值
        "session_header": "x-opencode-session",
        # 识图模型清单：网关目录中 attachment=true 的模型
        "vision_models": ["deepseek-v4.1-flash"],
    },
    "zbigmodel": {
        "label": "智谱 BigModel",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "api_key": ZBIGMODEL_API_KEY,
        "env_key": "ZBIGMODEL_API_KEY",
        "default_model": "glm-5.3-flash",
        "models": ["glm-5.3-flash"],
        # GLM-5.3-flash 为普通对话模型，正常传 temperature 等采样参数
        "reasoning_models": [],
        # 智谱为 OpenAI 兼容端点，同样不支持 DeepSeek 私有的 enable_search /
        # reasoning_effort 参数；本地 web_search / fetch_webpage 工具不受影响
        "supports_search": False,
        "supports_reasoning_effort": False,
        # GLM-5.3-flash 支持图片输入（OpenAI vision 多模态格式，base64 data URL）
        "vision_models": ["glm-5.3-flash"],
    },
    "bailian_token_plan": {
        "label": "百炼 Token Plan",
        # Token Plan 专属端点，不能换成通用 DashScope 地址（会改按量计费）。
        # 实测：国内 key 打新加坡端点 token-plan.ap-southeast-1 返回 401 invalid_api_key
        "base_url": "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        "api_key": BAILIAN_TOKEN_PLAN_API_KEY,
        "env_key": "BAILIAN_TOKEN_PLAN_API_KEY",
        "default_model": "qwen3.8-flash",
        # 同端点两款 Flash/Max，实测能力一致（均默认思考、均可识图），Max 更强更贵
        "models": ["qwen3.8-flash", "qwen3.8-max"],
        # 实测两者默认开启思考：响应含 reasoning_content（usage 里 reasoning_tokens>0），
        # 流式的 delta 同样有 reasoning_content，界面思考面板可正常显示
        "reasoning_models": ["qwen3.8-flash", "qwen3.8-max"],
        # OpenAI 兼容端点：不支持 DeepSeek 私有的 enable_search；
        # 百炼调思考强度用 enable_thinking 而非 reasoning_effort，故不下发
        # （本地 web_search / fetch_webpage 工具不受影响）
        "supports_search": False,
        "supports_reasoning_effort": False,
        # 实测两者都支持图片输入（128px / 256px / 512px 纯色图均正确识别）。注意网关限制
        # 图片宽高必须大于 10px，否则 400 invalid_parameter_error（客户端单张 ≤5MB 不变）
        "vision_models": ["qwen3.8-flash", "qwen3.8-max"],
    },
    "stepfun": {
        "label": "阶跃 Step 5",
        # 按量计费端点（OpenAI 兼容）。同一 key 打 Step Plan 通道 /step_plan/v1
        # 会返回 400 "you have no active step plan subscription"，故不配订阅地址
        "base_url": "https://api.stepfun.com/v1",
        "api_key": STEPFUN_API_KEY,
        "env_key": "STEPFUN_API_KEY",
        "default_model": "step-5-preview",
        # 账号 /v1/models 列出 36 个模型（含 step-3.5-flash / step-3.7-flash /
        # step-2x-large 及语音、图像类），这里只挂旗舰 Step 5 Preview
        "models": ["step-5-preview"],
        # 思考模型。实测响应同时带 reasoning 与 reasoning_content 两个字段且内容一致
        # （官方称 reasoning_content 是为兼容 DeepSeek 风格保留的字段名），
        # 客户端现有的 reasoning_content 读取路径无需适配即可显示思考过程
        "reasoning_models": ["step-5-preview"],
        # OpenAI 兼容端点：不支持 DeepSeek 私有的 enable_search。官方联网搜索是独立的
        # REST 接口（POST /v1/search），且文档标注 step-5-preview 不支持内置
        # type=web_search 工具；本地 web_search / fetch_webpage 工具不受影响（实测可用）
        "supports_search": False,
        # 官方文档：step-5-preview 支持 reasoning_effort 三档 low/medium/high。
        # 实测同一问题思考长度 122/166/198 字符随档位递增，参数确实生效
        "supports_reasoning_effort": True,
        # 实测识图可用（64px 纯色图正确辨色）。官方还标注支持视频输入
        # （content 类型 video_url，MP4 <128MB），客户端暂未实现视频上传
        "vision_models": ["step-5-preview"],
    },
    "xiaomi_mimo": {
        "label": "小米 MiMo",
        # 按量计费端点（OpenAI 兼容）。另有套餐通道 token-plan-cn/sgp/ams.xiaomimimo.com，
        # 实测这把按量 key 打套餐端点返回 401 invalid_key，故只配按量地址
        "base_url": "https://api.xiaomimimo.com/v1",
        "api_key": XIAOMI_MIMO_API_KEY,
        "env_key": "XIAOMI_MIMO_API_KEY",
        "default_model": "mimo-v2.6-flash",
        # 账号 /v1/models 共 9 个（mimo-v2.5 / v2.5-pro / v2.6-pro / v2.6-pro-ultraspeed /
        # v2.6-flash + asr/tts 系列），这里挂 v2.6 的两款主力对话模型。
        # 官方文档标注 mimo-v2.5 与 mimo-v2.5-pro 将于 2026-10-21 下线，不要往清单里加
        "models": ["mimo-v2.6-flash", "mimo-v2.6-pro"],
        # 官方文档：v2.6 全系 thinking 默认 enabled，且思考模式下 temperature/top_p 会被
        # 强制回默认值（1.0 / 0.95）——按思考模型分支构造请求，不下发采样参数
        "reasoning_models": ["mimo-v2.6-flash", "mimo-v2.6-pro"],
        # OpenAI 兼容端点，不支持 DeepSeek 私有的 enable_search；
        # 本地 web_search / fetch_webpage 工具不受影响（实测 tools 正常）
        "supports_search": False,
        # 官方只有 thinking.type = enabled/disabled 两态，无 low/medium/high 档位，
        # reasoning_effort 未列入参数表 → 不下发（实测传了也不改变思考长度）
        "supports_reasoning_effort": False,
        # 实测两款都能正确辨色（200px 纯色 PNG → 答「橙色」）。官方输入支持文本/图片/音频/视频
        "vision_models": ["mimo-v2.6-flash", "mimo-v2.6-pro"],
    },
}

# 兼容旧代码：按默认来源解析出端点与模型（界面切换后以 ChatSession 内部值为准）
BASE_URL = SOURCES[SOURCE]["base_url"]
"""API 端点地址（默认来源）。"""

MODEL = SOURCES[SOURCE]["default_model"]
"""默认模型名称（默认来源）。切换来源/模型后以 ChatSession 内部值为准。
   deepseek-flash      — DeepSeek 官方主力模型（DeepSeek-V4.1-Flash，思考模式默认开启，支持识图）
   deepseek-v4-pro     — DeepSeek-V4-Pro：官方/GO 均为更强推理模型（不支持识图）
   deepseek-v4-flash   — opencode GO 常规模型（思考链展示，不支持图片）
   deepseek-v4.1-flash — opencode GO 新 Flash（思考链展示，支持识图）
   glm-5.3-flash       — 智谱 GLM 快速响应模型（zbigmodel 专供）
   qwen3.8-flash       — 百炼 Token Plan 的通义千问 Flash（思考链展示，支持识图）
   qwen3.8-max         — 百炼 Token Plan 的通义千问 Max（同上，能力更强、成本更高）
   step-5-preview      — 阶跃星辰 Step 5 Preview 旗舰基模（思考链展示，支持识图，1M 上下文）
   mimo-v2.6-flash     — 小米 MiMo v2.6 Flash（思考链展示，支持识图，默认模型）
   mimo-v2.6-pro       — 小米 MiMo v2.6 Pro 旗舰（同上，能力更强）
"""

API_KEY = DEEPSEEK_API_KEY
"""向后兼容别名：DeepSeek 官方 API 密钥。新代码请改用 SOURCES。"""

API_TIMEOUT = 60
"""API 请求超时时间（秒）。网络差时可适当调大"""

API_MAX_RETRIES = 3
"""API 调用失败时的最大重试次数"""

# ── 深度思考（Reasoning） ──

SHOW_REASONING = True
"""是否在界面中展示 AI 的深度思考过程（reasoning_content）。
    仅思考模式模型（deepseek-flash, deepseek-v4-pro 等）支持此功能。
    关闭后思考过程不显示，但仍会传递给 API 以避免报错。"""

REASONING_EFFORT = "high"
"""推理强度（官方文档取值：low / high / max；medium 会被映射为 high）。
    仅官方来源（supports_reasoning_effort=True）生效。
    官方模型默认开启思考模式：此时 temperature / presence_penalty /
    frequency_penalty 不生效（传入也不报错），top_p 下限为 0.95。"""

ENABLE_SEARCH = True
"""是否开启 AI 联网搜索能力。

设为 True 后：
  - 向模型注册 web_search / fetch_webpage 两个本地工具（任何 API 来源都可用）
  - 若当前来源是 DeepSeek 官方 API，还会额外传递服务端 enable_search 参数

注意：
  - 官方来源的服务端搜索需 API Key 在 DeepSeek 开发者平台已开通权限
  - 本地工具基于 Bing RSS，无需额外 API Key，但搜索结果质量取决于网络环境
"""

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

MAX_WINDOWS = 4
"""单进程内最多同时打开的会话窗口数。

每个窗口一路 SSE 长连接，而 WebView2 对同一 host 的并发连接上限约 6，
要给换源/历史等普通请求留余量，故不放开到 6。
"""
