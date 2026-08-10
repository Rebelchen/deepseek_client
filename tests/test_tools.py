"""core.tools 路径重定向与工具执行测试。"""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import core.tools as tools


class WriteFileRedirectTest(unittest.TestCase):
    """write_file 应统一保存到 SAVE_DIR（总结目录）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="t_tools_")
        self.old_save_dir = tools.SAVE_DIR
        tools.SAVE_DIR = self.tmp

    def tearDown(self):
        tools.SAVE_DIR = self.old_save_dir
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_relative_path_keeps_subdir(self):
        result = tools.write_file("sub/dir/a.txt", "x")
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "sub", "dir", "a.txt")))
        self.assertIn("总结", result)

    def test_absolute_path_uses_basename(self):
        result = tools.write_file(r"E:\somewhere\report.md", "# hi")
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "report.md")))
        self.assertIn("总结", result)

    def test_parent_dir_traversal_is_clamped(self):
        tools.write_file("../escape.txt", "y")
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "escape.txt")))
        self.assertFalse(
            os.path.isfile(os.path.join(os.path.dirname(self.tmp), "escape.txt"))
        )

    def test_summary_prefix_not_doubled(self):
        tools.write_file("总结/notes.md", "z")
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "notes.md")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "总结")))

    def test_chinese_filename(self):
        tools.write_file("测试.txt", "ok")
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "测试.txt")))


class ToolDispatchTest(unittest.TestCase):
    def test_unknown_tool_returns_error(self):
        self.assertIn("未知工具", tools.execute_tool("no_such_tool", {}))

    def test_read_file_missing(self):
        self.assertIn("文件不存在", tools.read_file(r"Z:\definitely\missing.txt"))
