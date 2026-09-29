"""core.search 联网搜索与网页抓取测试（使用 mock，不依赖真实网络）。"""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock
from urllib.error import URLError

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core import search


RSS_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel>
  <item>
    <title>DeepSeek Platform</title>
    <link>https://platform.deepseek.com/</link>
    <description>Join DeepSeek API platform to access our AI models.</description>
  </item>
  <item>
    <title>DeepSeek Docs</title>
    <link>https://api-docs.deepseek.com/</link>
    <description>&lt;b&gt;Latest&lt;/b&gt; API pricing and docs.</description>
  </item>
</channel></rss>"""


class FakeResponse:
    """模拟 urllib 响应对象（含状态码与响应头）。"""

    def __init__(self, status=200, body=b"", charset="utf-8"):
        self.status = status
        self.body = body
        self.charset = charset

    def read(self, size=-1):
        if size < 0:
            return self.body
        return self.body[:size]

    @property
    def headers(self):
        return SimpleNamespace(get_content_charset=lambda: self.charset)


class SearchWebTest(unittest.TestCase):
    def test_search_web_returns_formatted_results(self):
        with mock.patch.object(search, "_http_get", return_value=(FakeResponse(body=RSS_XML), RSS_XML)):
            result = search.search_web("deepseek v4 pricing", max_results=2)
        self.assertIn("搜索关键词: deepseek v4 pricing", result)
        self.assertIn("DeepSeek Platform", result)
        self.assertIn("https://platform.deepseek.com/", result)
        self.assertIn("Latest API pricing", result)
        self.assertNotIn("结果 3", result)

    def test_search_web_empty_query(self):
        self.assertIn("不能为空", search.search_web("   "))

    def test_search_web_network_error_returns_readable_message(self):
        with mock.patch.object(search, "_http_get", side_effect=URLError("network down")):
            result = search.search_web("test")
        self.assertIn("搜索失败", result)

    def test_search_web_clamps_max_results(self):
        with mock.patch.object(search, "_http_get", return_value=(FakeResponse(body=RSS_XML), RSS_XML)):
            result = search.search_web("test", max_results=99)
        self.assertIn("结果 2", result)
        self.assertNotIn("结果 3", result)


class FetchWebpageTest(unittest.TestCase):
    HTML = (
        b"<html><head><title>Pricing - DeepSeek</title></head>"
        b"<body><script>bad()</script><style>.x{}</style>"
        b"<p>input $0.27 / 1M tokens</p></body></html>"
    )

    def test_fetch_webpage_extracts_text(self):
        resp = FakeResponse(body=self.HTML)
        with mock.patch.object(search, "_http_get", return_value=(resp, self.HTML)):
            result = search.fetch_webpage("https://api-docs.deepseek.com/")
        self.assertIn("Pricing - DeepSeek", result)
        self.assertIn("input $0.27", result)
        self.assertNotIn("<script>", result)

    def test_fetch_webpage_rejects_non_http(self):
        result = search.fetch_webpage("file:///etc/passwd")
        self.assertIn("只支持 http/https", result)


class ToolRegistryTest(unittest.TestCase):
    def test_search_tools_registered(self):
        names = {tool["function"]["name"] for tool in __import__("core.tools", fromlist=["TOOLS"]).TOOLS}
        self.assertIn("web_search", names)
        self.assertIn("fetch_webpage", names)


if __name__ == "__main__":
    unittest.main()
