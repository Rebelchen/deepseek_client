"""联网搜索与网页抓取 — 供 AI 通过 Function Calling 调用。

本模块提供两个工具：
  - web_search():    使用 Bing RSS 接口搜索网页，返回标题/链接/摘要
  - fetch_webpage(): 抓取指定网页并提取可读正文（HTML → 纯文本）

实现说明：
  - 全部使用 Python 标准库（urllib / xml / html），不引入第三方依赖
  - 所有网络请求均设超时与大小上限，失败时返回可读错误文本，
    AI 会把错误反馈给用户或改用其他思路
  - Bing RSS 适合个人非商业使用；如需更高稳定性，可在此处扩展
    Tavily / Brave / Bing Web Search API 等付费搜索源
"""

import html
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

# Bing RSS 搜索端点（无需 API Key）
BING_RSS_URL = "https://www.bing.com/search"

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

SEARCH_TIMEOUT = 15
"""搜索请求超时（秒）。"""

DEFAULT_MAX_RESULTS = 5
"""web_search 默认返回的结果条数。"""

MAX_RESULTS_LIMIT = 10
"""单次搜索最多返回的结果条数（防止一次塞太多内容进上下文）。"""

PAGE_FETCH_TIMEOUT = 15
"""网页抓取超时（秒）。"""

PAGE_MAX_BYTES = 2 * 1024 * 1024
"""网页抓取最大读取字节数（防止无限下载）。"""

DEFAULT_MAX_CHARS = 6000
"""fetch_webpage 默认返回的正文最大字符数。"""

MAX_PAGE_CHARS = 20000
"""fetch_webpage 允许的最大字符数上限。"""


def _clean(value: str | None) -> str:
    """清理 RSS/网页文本：去掉 HTML 标签、反转义实体、压缩空白。"""
    if not value:
        return ""
    text = re.sub(r"<[^>]+>", " ", value)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _http_get(url: str, timeout: int, max_bytes: int):
    """发起 GET 请求并返回 (response, bytes)，带 User-Agent 与大小上限。"""
    req = urllib.request.Request(url, headers={"User-Agent": DEFAULT_USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        data = data[:max_bytes]
    return resp, data


def _describe_error(exc: Exception) -> str:
    """把 urllib 异常转成简短、可读的错误描述。"""
    reason = getattr(exc, "reason", None)
    if reason is not None:
        return str(reason)[:200]
    return str(exc)[:200]


def _parse_rss(data: bytes) -> list[dict]:
    """解析 Bing RSS，返回 [{"title", "link", "snippet"}]。"""
    root = ET.fromstring(data)
    results: list[dict] = []
    for item in root.iter("item"):
        title = _clean(item.findtext("title"))
        link = _clean(item.findtext("link"))
        snippet = _clean(item.findtext("description"))
        if title or link:
            results.append({
                "title": title or link,
                "link": link or "",
                "snippet": snippet,
            })
    return results


def search_web(query: str, max_results: int = DEFAULT_MAX_RESULTS) -> str:
    """联网搜索网页，返回格式化文本给 AI 阅读。

    Args:
        query: 搜索关键词（建议具体、简洁，中英文均可）
        max_results: 返回结果条数（1-10，默认 5）

    Returns:
        搜索结果文本，或可读错误信息
    """
    query = (query or "").strip()
    if not query:
        return "错误：搜索关键词不能为空"

    try:
        max_results = max(1, min(int(max_results), MAX_RESULTS_LIMIT))
    except (TypeError, ValueError):
        max_results = DEFAULT_MAX_RESULTS

    params = urllib.parse.urlencode({
        "format": "rss",
        "q": query,
        "setlang": "zh-hans",
        "cc": "CN",
    })
    url = f"{BING_RSS_URL}?{params}"

    try:
        resp, data = _http_get(url, timeout=SEARCH_TIMEOUT, max_bytes=PAGE_MAX_BYTES)
    except Exception as e:
        return f"搜索失败: {_describe_error(e)}（请检查网络后重试）"

    if resp.status != 200:
        return f"搜索失败: HTTP {resp.status}"

    try:
        results = _parse_rss(data)
    except ET.ParseError as e:
        return f"搜索失败: 搜索结果解析错误（{e}）"

    if not results:
        return f"没有搜到与“{query}”相关的结果，请尝试更换关键词。"

    lines = [
        f"搜索关键词: {query}",
        f"来源: Bing（共返回 {len(results)} 条，显示前 {min(max_results, len(results))} 条）",
        "",
    ]
    for index, item in enumerate(results[:max_results], start=1):
        lines.append(f"结果 {index}：{item['title']}")
        lines.append(f"链接：{item['link']}")
        if item["snippet"]:
            lines.append(f"摘要：{item['snippet']}")
        lines.append("")
    return "\n".join(lines).strip()


def fetch_webpage(url: str, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """抓取网页并提取可读正文文本。

    Args:
        url: 完整网页地址（仅支持 http/https）
        max_chars: 最多返回的字符数（默认 6000，上限 20000）

    Returns:
        页面标题 + 正文纯文本，或可读错误信息
    """
    url = (url or "").strip()
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return "错误：只支持 http/https 网页地址"

    try:
        max_chars = max(500, min(int(max_chars), MAX_PAGE_CHARS))
    except (TypeError, ValueError):
        max_chars = DEFAULT_MAX_CHARS

    try:
        resp, data = _http_get(url, timeout=PAGE_FETCH_TIMEOUT, max_bytes=PAGE_MAX_BYTES)
    except Exception as e:
        return f"抓取网页失败: {_describe_error(e)}（请检查网络后重试）"

    charset = resp.headers.get_content_charset()
    if not charset:
        # 有些站点不在响应头声明编码，但 HTML <meta> 里有，尝试读取前 2KB 识别
        meta_charset = re.search(
            rb'<meta[^>]+charset=["\']?([\w-]+)', data[:2048], re.IGNORECASE
        )
        if meta_charset:
            charset = meta_charset.group(1).decode("ascii", "ignore")
    charset = charset or "utf-8"
    raw_text = data.decode(charset, errors="ignore")

    # 提取 <title>（在剥离标签前）
    title_match = re.search(r"<title[^>]*>(.*?)</title>", raw_text, re.IGNORECASE | re.DOTALL)
    title = _clean(title_match.group(1)) if title_match else ""

    # 去掉脚本/样式与 HTML 标签，转成纯文本
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw_text, flags=re.IGNORECASE | re.DOTALL)
    text = _clean(text)
    if not text:
        return f"页面: {url}\n未能提取到有效正文（可能是 JS 渲染页面或二进制内容）。"

    if len(text) > max_chars:
        text = text[:max_chars] + "\n...（内容过长，已截断）"

    header = f"页面: {url}\n"
    if title:
        header += f"标题: {title}\n"
    return header + text
