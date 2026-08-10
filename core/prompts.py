"""系统提示词构建 — UI 层共用，避免两套界面各自拼写导致内容分叉。"""

from config import ENABLE_SEARCH


def build_system_prompt(enable_search: bool | None = None) -> str:
    """构建默认系统提示词（联网搜索说明 + LaTeX 公式格式要求）。"""
    if enable_search is None:
        enable_search = ENABLE_SEARCH

    search_note = ""
    if enable_search:
        search_note = (
            "\n\n【联网搜索能力】你已启用内置联网搜索功能。"
            "当用户问及时效性问题（天气、新闻、股价、实时数据等）时，"
            "你无需调用任何工具——联网搜索是自动内置在你模型中的能力，"
            "你只需正常回答，系统会自动在后台联网获取最新信息并注入到你的上下文中。"
            "你会在上下文中看到搜索到的实时内容，直接使用即可。"
        )

    return (
        "你是一个有用的助手。你可以读取和写入本地文件来帮助用户。"
        "当用户提到本地文件时，请使用 read_file 工具读取内容。"
        "你写入或保存的文件会统一存放到项目的「总结」目录。"
        + search_note
        + "\n\n"
        "【重要】数学公式格式要求：\n"
        "当回答中包含数学公式时，请使用 LaTeX 格式，并用 $$...$$（展示式）"
        "或 \\(...\\)（行内式）包裹，例如：\n"
        "- 行内公式：\\(E = mc^2\\)\n"
        "- 展示公式：$$\\frac{\\partial u}{\\partial t} = \\alpha^2 \\nabla^2 u$$\n"
        "- 根号：\\(\\sqrt{a^2 + b^2}\\)\n"
        "- 积分：$$\\int_{0}^{\\infty} e^{-x^2} dx = \\frac{\\sqrt{\\pi}}{2}$$\n"
        "请勿使用 Unicode 数学符号替代 LaTeX 公式。"
    )
