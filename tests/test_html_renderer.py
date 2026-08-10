"""core.html_renderer Markdown → HTML 管道测试。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.html_renderer import markdown_to_html


class MarkdownToHtmlTest(unittest.TestCase):
    def test_display_math_preserved(self):
        html = markdown_to_html("公式：\n\n$$E = mc^2$$\n")
        self.assertIn("$$E = mc^2$$", html)

    def test_code_block_highlighted(self):
        html = markdown_to_html("```python\nprint(1)\n```")
        self.assertIn("<code", html)
        self.assertIn("print", html)  # Pygments 会拆成带 span 的 token

    def test_bare_latex_wrapped(self):
        html = markdown_to_html(r"\frac{a}{b}")
        self.assertIn(r"$\frac{a}{b}$", html)

    def test_table_rendered(self):
        html = markdown_to_html("| A | B |\n|---|---|\n| 1 | 2 |")
        self.assertIn("<table>", html)
        self.assertIn("<th>A</th>", html)

    def test_pricing_dollar_not_math(self):
        html = markdown_to_html("价格 $50，成本 $10。")
        # 非数学美元符号会被插入零宽空格，避免 KaTeX 误渲染
        self.assertIn("$\u200b50", html)

    def test_single_line_display_math_does_not_swallow_following_content(self):
        html = markdown_to_html(
            "前文\n\n$$E = mc^2$$\n\n## 后面的标题\n\n- 列表项一\n- 列表项二\n\n| A | B |\n|---|---|\n| 1 | 2 |"
        )
        self.assertIn("$$E = mc^2$$", html)
        self.assertIn("<h2", html)           # 标题结构保留
        self.assertIn("<ul>", html)          # 列表结构保留
        self.assertIn("<table>", html)       # 表格结构保留

    def test_doubled_backslash_delimiters_normalized(self):
        html = markdown_to_html(r"行内 \\(E = mc^2\\) 公式")
        self.assertIn("$E = mc^2$", html)
        self.assertNotIn(r"\\(E", html)

    def test_doubled_backslash_commands_normalized(self):
        html = markdown_to_html(r"分数 \\frac{a}{b} 结尾")
        self.assertIn(r"$\frac{a}{b}$", html)
        self.assertNotIn(r"\\frac", html)

    def test_math_dollar_starting_with_digit_not_treated_as_price(self):
        html = markdown_to_html("弦论紧化产生约 $10^{500}$ 个真空")
        self.assertIn("$10^{500}$", html)
        self.assertNotIn("$\u200b10", html)

    def test_math_with_digit_space_and_command_not_treated_as_price(self):
        html = markdown_to_html(r"税后 $2 \div 1.03 \approx 1.94$ 元")
        self.assertIn(r"$2 \div 1.03 \approx 1.94$", html)
        self.assertNotIn("$\u200b2", html)

    def test_math_with_degree_not_treated_as_price(self):
        html = markdown_to_html(r"南天 $\5^\circ$ 冷斑")
        self.assertNotIn("$\u200b5", html)

    def test_matrix_row_separator_preserved(self):
        html = markdown_to_html(
            r"$$\begin{aligned} a &= b \\ c &= d \end{aligned}$$"
        )
        self.assertIn(r"\begin{aligned}", html)
        self.assertIn(r"a &amp;= b \\ c", html)  # 行分隔符 \\ 不能被归一化吃掉

    def test_underscores_in_display_math_not_turned_into_em(self):
        html = markdown_to_html(
            r"$$\mathcal{P}_{\zeta}(k) = \frac{1}{8\pi^2}\,\frac{H^2}{\varepsilon\, M_{\rm Pl}^2}\Bigg|_{k=aH}$$"
        )
        self.assertIn(
            r"$$\mathcal{P}_{\zeta}(k) = \frac{1}{8\pi^2}\,\frac{H^2}{\varepsilon\, M_{\rm Pl}^2}\Bigg|_{k=aH}$$",
            html,
        )
        self.assertNotIn("<em>", html)  # 数学内部的下划线不能被 markdown 转成斜体

    def test_underscores_in_inline_math_not_turned_into_em(self):
        html = markdown_to_html(r"功率谱 $\mathcal{P}_{\zeta}$ 如下")
        self.assertIn(r"$\mathcal{P}_{\zeta}$", html)
        self.assertNotIn("<em>", html)

    def test_prose_emphasis_still_works(self):
        html = markdown_to_html("**加粗** 和 _斜体_ 正常")
        self.assertIn("<strong>加粗</strong>", html)
        self.assertIn("<em>斜体</em>", html)
