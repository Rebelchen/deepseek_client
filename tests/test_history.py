"""core.history 保存 / 解析 / 覆盖保存测试。"""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core import history


MESSAGES = [
    {"role": "user", "content": "解释质能方程", "timestamp": "2026-08-03 12:00:00"},
    {
        "role": "assistant",
        "content": "$$E=mc^2$$",
        "timestamp": "2026-08-03 12:00:05",
        "reasoning_content": "先回忆相对论基本结论",
    },
]


class HtmlRoundTripTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="t_hist_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_save_and_parse_roundtrip(self):
        path = history.save_conversation_html(MESSAGES, self.tmp, "<p>渲染</p>")
        self.assertTrue(path.endswith(".html"))
        parsed = history.parse_conversation(path)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0]["role"], "user")
        self.assertEqual(parsed[1]["content"], "$$E=mc^2$$")
        self.assertEqual(parsed[1]["reasoning_content"], "先回忆相对论基本结论")

    def test_overwrite_same_path_no_duplicates(self):
        path = history.save_conversation_html(MESSAGES, self.tmp, "<p>1</p>")
        more = MESSAGES + [
            {"role": "user", "content": "再问一个", "timestamp": "2026-08-03 12:01:00"}
        ]
        path2 = history.overwrite_conversation_html(path, more, "<p>2</p>")
        self.assertEqual(path, path2)
        parsed = history.parse_conversation(path)
        self.assertEqual(len(parsed), 3)
        html_files = [f for f in os.listdir(self.tmp) if f.endswith(".html")]
        self.assertEqual(len(html_files), 1)  # 覆盖不产生重复副本


class TxtRoundTripTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="t_hist_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_txt_roundtrip(self):
        path = history.save_conversation(MESSAGES, self.tmp)
        parsed = history.parse_conversation(path)
        self.assertEqual(len(parsed), 2)
        self.assertTrue(parsed[0]["content"].startswith("解释质能方程"))
        self.assertEqual(parsed[1]["content"], "$$E=mc^2$$")


class TitleTest(unittest.TestCase):
    def test_truncate_long(self):
        long_msg = [
            {"role": "user", "content": "请尽量系统地介绍白莲教并给出完整历史脉络和相关文献综述" * 3}
        ]
        title = history.generate_title(long_msg)
        self.assertLessEqual(len(title), 31)  # 30 字符 + 省略号
        self.assertTrue(title.endswith("…"))

    def test_fallback_default(self):
        self.assertEqual(history.generate_title([]), "对话")
