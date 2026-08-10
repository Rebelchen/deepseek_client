r"""
HTML 渲染引擎 — 将 Markdown + LaTeX 转换为仿 DeepSeek 官网风格的 HTML

核心目标：把 AI 的 Markdown + LaTeX 输出安全、稳定地转成 HTML，
让 KaTeX 在前端能正确配对每个公式定界符。

渲染管道（按执行顺序）：
  Layer 0   —— 双重反斜杠归一化：兼容模型输出的 \\(...\\)、\\frac 等
  Layer 3   —— 裸 LaTeX 命令包裹：给无定界符的 \frac{}{} 等自动加 $...$
  Layer 3.5 —— 压平 $$...$$ 块内换行 + 保护 \\、\{、\}
  Layer 3.6 —— 保护数学定界符内部的 _ 和 *（防 markdown 误判为斜体/加粗）
  标准 markdown 转换（代码高亮、表格、引用等，使用 Pygments）
  恢复被保护的转义序列
  Layer 3.4 —— 转义价格型 $数字（$50），避免 KaTeX 误配
  Layer 2   —— 检测代码块内的 LaTeX，替换为 $$...$$
  （Layer 1 —— 数学字体基线，在 CSS 中全局生效）

主要入口：
    markdown_to_html(text)      — 单条消息内容 → 片段 HTML
    format_chat_html(messages)  — 消息列表 → 完整 HTML 页面
"""

import re
import html as html_mod
from datetime import datetime
from typing import Optional

import markdown
from pygments import highlight
from pygments.lexers import get_lexer_by_name, guess_lexer, TextLexer
from pygments.formatters import HtmlFormatter

# ──────────────────────────────────────────────────────────
# Pygments 样式配置
# ──────────────────────────────────────────────────────────

CODE_CSS = HtmlFormatter(style="github-dark", noclasses=False).get_style_defs(".codehilite")

# Pygments 行号标记
PYGMENTS_CSS = """
.codehilite { background: #0d1117; border-radius: 8px; padding: 16px; margin: 8px 0; overflow-x: auto; font-size: 14px; line-height: 1.5; }
.codehilite pre { margin: 0; font-family: 'Cascadia Code', 'Fira Code', 'Consolas', monospace; }
.codehilite .linenos { color: #8b949e; user-select: none; padding-right: 16px; border-right: 1px solid #30363d; margin-right: 16px; }
"""

# ──────────────────────────────────────────────────────────
# CSS — 仿 DeepSeek 官网风格
# ──────────────────────────────────────────────────────────

CHAT_CSS = f"""
/* Reset & Base */
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    font-family: 'Cambria Math', 'STIX Two Text', 'Asana Math', 'XITS Math',
                 'Latin Modern Math', 'Noto Sans Math',
                 -apple-system, BlinkMacSystemFont, 'Segoe UI',
                 'Noto Sans SC', 'Microsoft YaHei', sans-serif;
    background: #f5f5f5;
    color: #1a1a2e;
    font-size: 15px;
    line-height: 1.7;
    padding: 16px;
    max-width: 100%;
    overflow-x: hidden;
}}

/* KaTeX inline math: slightly larger for readability */
.katex {{ font-size: 1.1em; }}
.katex-display {{ text-align: center; margin: 12px 0; overflow-x: auto; overflow-y: hidden; }}

/* Message Container */
.chat-container {{
    display: flex;
    flex-direction: column;
    gap: 12px;
    max-width: 800px;
    margin: 0 auto;
}}

/* Message Bubbles */
.message {{
    display: flex;
    flex-direction: column;
    animation: fadeIn 0.3s ease;
}}
@keyframes fadeIn {{
    from {{ opacity: 0; transform: translateY(8px); }}
    to {{ opacity: 1; transform: translateY(0); }}
}}

.message-header {{
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 4px;
    padding: 0 4px;
}}
.message-avatar {{
    width: 28px; height: 28px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 14px;
    font-weight: 600;
    flex-shrink: 0;
}}
.message-role {{
    font-size: 13px;
    font-weight: 600;
    color: #666;
}}
.message-time {{
    font-size: 11px;
    color: #999;
    margin-left: auto;
}}

/* User Message */
.message.user .message-avatar {{
    background: #e8f4fd;
    color: #1a73e8;
}}
.message.user .message-body {{
    background: #e8f4fd;
    color: #1a1a2e;
    border-radius: 12px 12px 4px 12px;
    padding: 12px 16px;
    margin-left: 36px;
}}

/* Assistant Message */
.message.assistant .message-avatar {{
    background: #e8f5e9;
    color: #0d652d;
}}
.message.assistant .message-body {{
    background: #ffffff;
    color: #1a1a2e;
    border-radius: 12px 12px 12px 4px;
    padding: 12px 16px;
    margin-right: 36px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.08);
}}
.message.assistant .message-body p {{
    margin-bottom: 8px;
}}
.message.assistant .message-body p:last-child {{
    margin-bottom: 0;
}}

/* System (tool call) Message */
.message.system .message-body {{
    color: #888;
    font-size: 13px;
    font-style: italic;
    padding: 4px 12px;
    margin-left: 36px;
}}
.message.system .message-avatar {{
    display: none;
}}

/* Markdown Content Styles */
.message-body h1, .message-body h2, .message-body h3 {{
    margin: 16px 0 8px;
    color: #1a1a2e;
}}
.message-body h1 {{ font-size: 20px; border-bottom: 1px solid #eee; padding-bottom: 6px; }}
.message-body h2 {{ font-size: 18px; }}
.message-body h3 {{ font-size: 16px; }}
.message-body ul, .message-body ol {{ padding-left: 24px; margin: 8px 0; }}
.message-body li {{ margin: 4px 0; }}
.message-body blockquote {{
    border-left: 3px solid #1a73e8;
    padding: 8px 16px;
    margin: 8px 0;
    background: #f8f9fa;
    border-radius: 0 8px 8px 0;
    color: #555;
}}
.message-body table {{
    border-collapse: collapse;
    width: 100%;
    margin: 12px 0;
    font-size: 14px;
}}
.message-body th, .message-body td {{
    border: 1px solid #e0e0e0;
    padding: 8px 12px;
    text-align: left;
}}
.message-body th {{
    background: #f5f5f5;
    font-weight: 600;
}}
.message-body tr:nth-child(even) {{
    background: #fafafa;
}}
.message-body a {{
    color: #1a73e8;
    text-decoration: none;
}}
.message-body a:hover {{
    text-decoration: underline;
}}
.message-body strong {{
    font-weight: 600;
}}
.message-body em {{
    font-style: italic;
}}
.message-body hr {{
    border: none;
    border-top: 1px solid #eee;
    margin: 16px 0;
}}

/* Code blocks (inline & block) */
.message-body code {{
    font-family: 'Cascadia Code', 'Fira Code', 'Consolas', monospace;
    font-size: 13px;
    background: #f0f0f0;
    padding: 2px 6px;
    border-radius: 4px;
    color: #d63384;
}}
.message-body pre code {{
    background: transparent;
    padding: 0;
    color: inherit;
}}

/* Inline code in dark blocks */
.codehilite code {{
    background: transparent;
    color: #e6edf3;
    padding: 0;
}}

{CODE_CSS}
{PYGMENTS_CSS}
"""

# ──────────────────────────────────────────────────────────
# KaTeX CDN
# ──────────────────────────────────────────────────────────

KATEX_CDN = """
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css">
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js">
</script>
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/contrib/auto-render.min.js"
    onload="renderMathInElement(document.body, {
        delimiters: [
            {left: '\\\\[', right: '\\\\]', display: true},
            {left: '$$', right: '$$', display: true},
            {left: '\\\\(', right: '\\\\)', display: false},
            {left: '$', right: '$', display: false}
        ],
        throwOnError: false,
        trust: true
    });">
</script>
"""

# ──────────────────────────────────────────────────────────
# Markdown 转 HTML（含代码高亮）
# ──────────────────────────────────────────────────────────


class CodeHighlighter:
    """Pygments 代码高亮处理器，兼容 markdown 库的 fenced_code 扩展。"""

    @staticmethod
    def highlight_code(source: str, language: str = "") -> str:
        """高亮一段代码并返回 HTML。"""
        if not source.strip():
            return ""

        try:
            if language:
                lexer = get_lexer_by_name(language, stripall=True)
            else:
                lexer = guess_lexer(source)
        except Exception:
            lexer = TextLexer()

        try:
            formatter = HtmlFormatter(
                style="github-dark",
                noclasses=True,
                wrapcode=True,
                lineseparator="",
            )
            result = highlight(source, lexer, formatter)
            # 移除多余的 pre 嵌套
            return result
        except Exception:
            return f"<pre><code>{html_mod.escape(source)}</code></pre>"

    @staticmethod
    def get_pygments_css() -> str:
        return HtmlFormatter(style="github-dark", noclasses=False).get_style_defs(".codehilite")


def _code_block_formatter(source: str, language: str, class_name: str = "") -> str:
    """markdown 库的 fenced_code 格式器回调。"""
    highlighted = CodeHighlighter.highlight_code(source, language)
    return f'<div class="codehilite">{highlighted}</div>'


# ──────────────────────────────────────────────────────────
# Layer 2: LaTeX 代码块检测 — 将 ``` 中的 LaTeX 转为 $$...$$
# ──────────────────────────────────────────────────────────

# 匹配常见 LaTeX 命令的模式：用于判断代码块内容是否为 LaTeX
_LATEX_CMD_RE = re.compile(
    r'\\(?:'
    r'frac|sum|int|iint|iiint|oint|prod|coprod|lim|sup|inf|log|ln|exp|sin|cos|tan'
    r'|partial|nabla|sqrt'
    r'|alpha|beta|gamma|delta|epsilon|zeta|eta|theta|iota|kappa|lambda|mu|nu|xi|pi|rho'
    r'|sigma|tau|upsilon|phi|chi|psi|omega'
    r'|Gamma|Delta|Theta|Lambda|Xi|Pi|Sigma|Phi|Psi|Omega'
    r'|infty|to|rightarrow|leftarrow|Rightarrow|Leftarrow|Leftrightarrow|mapsto'
    r'|neq|leq|geq|approx|equiv|times|div|pm|mp|cdot'
    r'|subset|supset|subseteq|supseteq|in|notin|cap|cup|emptyset'
    r'|mathbb|mathcal|mathrm|mathbf|mathit|text|boxed'
    r'|choose|binom|hat|vec|dot|ddot|bar|tilde|widehat|widetilde'
    r'|langle|rangle|lbrace|rbrace'
    r'|begin|end|left|right|quad|qquad|hbar|hslash|ell|wp|Re|Im'
    r'|aleph|nabla|Box|diamond|triangle'
    r'|dots|cdots|vdots|ddots'
    r')'
)

def _is_latex_line(line: str) -> bool:
    """判断一行文本是否包含 LaTeX 命令。"""
    # 跳过空行、纯标点行
    stripped = line.strip()
    if not stripped or len(stripped) < 3:
        return False
    # 已经包含 $ 定界符 → 已处理
    if "$" in stripped:
        return False
    # 检查 LaTeX 命令密度
    cmds = _LATEX_CMD_RE.findall(stripped)
    if not cmds:
        return False
    # 计算密度：每 10 个字符至少 1 个 LaTeX 命令算 dense
    cmd_count = len(cmds)
    non_blank_len = len(stripped.replace(" ", ""))
    if non_blank_len == 0:
        return False
    density = cmd_count / (non_blank_len / 10)
    return density >= 0.5


def _wrap_latex_codeblocks(html: str) -> str:
    """在已渲染的 HTML 中检测代码块，若内容为 LaTeX 则替换为 $$...$$。

    markdown 将 ``` 代码块渲染为：
      <div class="codehilite"><pre><span></span><code>...</code></pre></div>
    这个函数检测其中内容是否为 LaTeX，如果是则替换为展示式数学。
    """
    # 匹配 <code> 到 </code> 的全部内容（允许包含 <span> 等标签）
    # 注意：pygments 可能会在代码内部加 <span> 标签着色
    def _replace_code_block(m):
        full_code_html = m.group(1)
        # 提取纯文本（去掉所有 HTML 标签）
        plain_text = re.sub(r'<[^>]+>', '', full_code_html)
        # 解码 HTML 实体
        plain_text = html_mod.unescape(plain_text)

        # 按行判断是否为 LaTeX
        lines = plain_text.split("\n")
        latex_lines = []
        for line in lines:
            if _is_latex_line(line):
                latex_lines.append(line)

        # 如果超过 40% 的行是 LaTeX，整个代码块视为 LaTeX
        if len(lines) > 0 and len(latex_lines) / len(lines) >= 0.4:
            # 将整个代码块内容作为展示式数学
            latex_content = "\n".join(lines).strip()
            return f"\n$${latex_content}$$\n"

        # 不是 LaTeX → 保持原样
        return m.group(0)

    # 匹配 <code>...</code> 标签对
    result = re.sub(
        r'<code[^>]*>(.*?)</code>',
        _replace_code_block,
        html,
        flags=re.DOTALL,
    )
    return result


# ──────────────────────────────────────────────────────────
# Layer 3: 裸 LaTeX 命令包裹 — 给无定界符的 LaTeX 命令加 $...$
# ──────────────────────────────────────────────────────────

# 匹配裸 LaTeX 命令的模式（不在代码块内、不在 $...$ 内）
# 捕获 \command{...} 及其可能的 _{...} 和 ^{...} 后缀
_BARE_LATEX_BLOCK_RE = re.compile(
    r'(?<!\$)'                     # 前面不是 $
    r'(?:'
    r'\\frac\s*\{[^}]*\}\s*\{[^}]*\}'      # \frac{num}{den}
    r'|\\sum\s*(_\{[^}]*\})?\s*(\^\{[^}]*\})?'  # \sum_{}^{}
    r'|\\int\s*(_\{[^}]*\})?\s*(\^\{[^}]*\})?'  # \int_{}^{}
    r'|\\prod\s*(_\{[^}]*\})?\s*(\^\{[^}]*\})?' # \prod_{}^{}
    r'|\\lim\s*(_\{[^}]*\})?\s*(\^\{[^}]*\})?'  # \lim_{}^{}
    r'|\\iint\s*(_\{[^}]*\})?\s*(\^\{[^}]*\})?'
    r'|\\iiint\s*(_\{[^}]*\})?\s*(\^\{[^}]*\})?'
    r'|\\oint\s*(_\{[^}]*\})?\s*(\^\{[^}]*\})?'
    r'|\\binom\s*\{[^}]*\}\s*\{[^}]*\}'    # \binom{}{}
    r'|\\text\s*\{[^}]*\}'                  # \text{}
    r'|\\mathbb\{[^}]*\}'                    # \mathbb{}
    r'|\\mathcal\{[^}]*\}'                   # \mathcal{}
    r'|\\mathrm\{[^}]*\}'                    # \mathrm{}
    r'|\\mathbf\{[^}]*\}'                    # \mathbf{}
    r'|\\boxed\{[^}]*\}'                     # \boxed{}
    r')'
)


# ──────────────────────────────────────────────────────────
# Layer 0: 双重反斜杠归一化 — 兼容模型的转义输出
# ──────────────────────────────────────────────────────────

# 模型经常输出 \\(...\\)、\\frac 这类"转义后的反斜杠"。
# 归一化为单反斜杠：\\( → \(、\\frac → \frac 等。
# 注意：矩阵/对齐环境中的行分隔符（\\ 后跟空格/换行）不会被误伤。
_DOUBLED_BACKSLASH_RE = re.compile(r'\\\\([A-Za-z()\[\]{}])')


def _normalize_doubled_backslashes(text: str) -> str:
    """将双重反斜杠命令/定界符还原为单反斜杠（幂等，可安全用于任意输入）。"""
    return _DOUBLED_BACKSLASH_RE.sub(r'\\\1', text)


def _wrap_bare_latex(text: str) -> str:
    r"""在纯文本中检测裸 LaTeX 命令，自动用 $...$ 包裹。

    步骤：
      1. 保护代码块
      2. 将 \[...\] 转为 $$...$$（避免 markdown 吃掉反斜杠）
      3. 将 \(...\) 转为 $...$
      4. 在剩余文本中匹配裸 \frac{}{}, \sum_{}^{} 等命令，逐个用 $ 包裹
      5. 恢复代码块
    """
    if not text:
        return text

    # ── 1. 保护代码块 ──
    code_blocks = []

    def _save_code(m):
        code_blocks.append(m.group(0))
        return f"\x00CODEBLOCK{len(code_blocks)-1}\x00"

    protected = re.sub(r'```.*?```', _save_code, text, flags=re.DOTALL)

    # ── 2. \[...\] → $$...$$（多行） ──
    # 必须在 markdown 处理之前完成，否则 \ 被 markdown 转义
    # 匹配 \[ 到 \] 之间的所有内容（跨行）
    # 同时：把所有反斜杠翻倍，以抵消 markdown 的转义消耗
    protected = re.sub(
        r'\\\[(.*?)\\\]',
        lambda m: f"$${m.group(1).replace(chr(92), chr(92)*2)}$$",
        protected,
        flags=re.DOTALL,
    )

    # ── 3. \(...\) → $...$（行内） ──
    protected = re.sub(
        r'\\\((.*?)\\\)',
        lambda m: f"${m.group(1).replace(chr(92), chr(92)*2)}$",
        protected,
        flags=re.DOTALL,
    )

    # ── 4. 裸 LaTeX 命令包裹 ──
    # 跳过已有数学定界符的区域（$...$, $$...$$, 以及代码块）
    lines = protected.split("\n")
    new_lines = []
    in_display_math = False  # 跟踪 $$...$$ 状态
    for line in lines:
        # 跟踪 $$ 显示数学块状态
        stripped = line.strip()
        if stripped.startswith("$$"):
            if in_display_math:
                in_display_math = False
            elif stripped.count("$$") >= 2:
                # 单行 $$...$$：本身已闭合，不改变块状态
                pass
            else:
                in_display_math = True
            new_lines.append(line)
            continue
        # 跳过 $$...$$ 内部的行
        if in_display_math:
            new_lines.append(line)
            continue
        # 跳过已含 $ 的行（已有行内数学）
        if "$" in line:
            new_lines.append(line)
            continue
        # 跳过代码块占位符
        if "\x00CODEBLOCK" in line:
            new_lines.append(line)
            continue

        # 其余行：查找裸 LaTeX 命令，逐个包裹
        result_line = line
        while True:
            match = _BARE_LATEX_BLOCK_RE.search(result_line)
            if not match:
                break
            matched_text = match.group(0)
            start, end = match.span()
            result_line = result_line[:start] + "$" + matched_text + "$" + result_line[end:]

        new_lines.append(result_line)

    protected = "\n".join(new_lines)

    # ── 5. 恢复代码块 ──
    def _restore_code(m):
        idx = int(m.group(1))
        return code_blocks[idx] if idx < len(code_blocks) else m.group(0)

    protected = re.sub(r"\x00CODEBLOCK(\d+)\x00", _restore_code, protected)

    return protected


# ──────────────────────────────────────────────────────────
# Layer 3.5: 压平 $$...$$ 块内的新行
# ──────────────────────────────────────────────────────────

def _flatten_display_math(text: str) -> str:
    """将 $$...$$ 块内所有换行去掉，变成一个连续行。

    markdown 的 nl2br 扩展会把换行转 <br/>，这会切断 $$...$$ 对，
    KaTeX auto-render 无法跨 DOM 节点匹配定界符。

    同时保护 $$...$$ 内的以下序列不被 markdown 转义吞噬：
      - \\\\ → \x00DBLBS\x00  (双反斜杠，矩阵换行)
      - \\{ → \x00LCB\x00     (花括号，如 left\\{)
      - \\} → \x00RCB\x00     (花括号，如 right\\})
    """
    result = []
    in_math = False
    buffer = ""  # 累积 $$...$$ 块内容
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("$$"):
            if in_math:
                # 遇到关闭 $$，刷出 buffer
                buf = _protect_markdown_escapes(buffer)
                result.append(buf + " " + stripped)
                buffer = ""
                in_math = False
            elif stripped.count("$$") >= 2:
                # 单行 $$...$$：本身已闭合，直接通过（不吞后续内容）
                result.append(_protect_markdown_escapes(stripped))
            else:
                # 遇到开启 $$（多行块的起始行）
                in_math = True
                buffer = stripped
            continue
        if in_math:
            # 累积内容，用空格连接
            buffer = buffer + " " + stripped
            continue
        result.append(line)

    # 如果 buffer 未关闭（不匹配），追加回去
    if buffer:
        result.append(_protect_markdown_escapes(buffer))

    return "\n".join(result)


def _protect_markdown_escapes(s: str) -> str:
    """保护 $$...$$ 内部可能被 markdown 吃掉的转义序列。"""
    s = s.replace("\\\\", "\x00DBLBS\x00")  # 双反斜杠
    s = s.replace("\\{", "\x00LCB\x00")      # \{ 
    s = s.replace("\\}", "\x00RCB\x00")      # \}
    return s


def _restore_markdown_escapes(s: str) -> str:
    """恢复 _protect_markdown_escapes 保护的序列。"""
    s = s.replace("\x00DBLBS\x00", "\\\\")
    s = s.replace("\x00LCB\x00", "\\{")
    s = s.replace("\x00RCB\x00", "\\}")
    return s


# ──────────────────────────────────────────────────────────
# Layer 3.6: 保护数学定界符内部的 _ 和 *（防 markdown 转成 <em>/<strong>）
# ──────────────────────────────────────────────────────────

_MATH_SPAN_RE = re.compile(r'\$\$(.*?)\$\$|\$(.*?)\$', re.DOTALL)


def _protect_math_tokens(text: str) -> str:
    """把 $...$ / $$...$$ 内部的 _ 和 * 换成占位符。

    数学公式里的下划线（如 \\mathcal{P}_{\\zeta}、M_{\\rm Pl}）会被
    markdown 误判为斜体标记，转成 <em> 后 KaTeX 无法配对定界符。
    """
    out = []
    pos = 0
    for m in _MATH_SPAN_RE.finditer(text):
        out.append(text[pos : m.start()])
        inner = m.group(1) if m.group(1) is not None else m.group(2)
        inner = inner.replace("_", "\x00MATHUS\x00").replace("*", "\x00MATHSTAR\x00")
        delim = "$$" if m.group(1) is not None else "$"
        out.append(delim + inner + delim)
        pos = m.end()
    out.append(text[pos:])
    return "".join(out)


def _restore_math_tokens(s: str) -> str:
    """恢复 _protect_math_tokens 替换的占位符。"""
    return s.replace("\x00MATHUS\x00", "_").replace("\x00MATHSTAR\x00", "*")


def _escape_pricing_dollars(html: str) -> str:
    """在 HTML 输出中转义非数学 $<digit>（如 $50），
    插入零宽空格防止 KaTeX auto-render 误匹配为行内数学定界符。

    零宽空格 '\u200B' 在浏览器中不可见，但能打断 KaTeX 的 $ 定界符匹配。
    """
    # 数学记号：反斜杠命令、上下标、花括号——出现其一即视为公式
    math_mark = re.compile(r'[\\^_{}]')

    def escape_prices_in_line(line: str) -> str:
        """转义价格型 $数字；若与后续 $ 成对且中间含数学记号，则按公式放行。"""
        out = []
        pos = 0
        for m in re.finditer(r'(?<!\\)\$(\d+)', line):
            rest = line[m.end():]
            close = re.search(r'(?<!\\)\$', rest)
            # $10^{500}$、$5^\circ$、$2 \div 1.03$ → 公式，不转义
            if close and math_mark.search(rest[: close.start()]):
                out.append(line[pos : m.end()])
            else:
                # 价格/未配对 $50 → 插入零宽空格，防止 KaTeX 误配
                out.append(line[pos : m.start()])
                out.append("$\u200b" + m.group(1))
            pos = m.end()
        out.append(line[pos:])
        return "".join(out)

    # 只在非 <pre>/<code> 区域做替换
    parts = re.split(r'(<pre[^>]*>.*?</pre>)', html, flags=re.DOTALL)
    for i in range(0, len(parts), 2):
        parts[i] = "\n".join(escape_prices_in_line(l) for l in parts[i].split("\n"))
    return "".join(parts)


# ──────────────────────────────────────────────────────────
# Markdown → HTML（三层管道）
# ──────────────────────────────────────────────────────────

def markdown_to_html(text: str) -> str:
    """将 Markdown 文本转换为 HTML（含三层数学渲染管道）。

    管道：
      1. Layer 3 — 裸 LaTeX 命令包裹（给无定界符的命令加 $...$）
      2. Layer 3.5 — 压平 $$...$$ 块内新行（防 nl2br 破坏）
      3. markdown 标准转换（代码高亮、表格等）
      4. Layer 2 — 代码块 LaTeX 检测（将 ``` 中的 LaTeX 转为 $$...$$）
      (Layer 1 — 数学字体基线在 CSS 中全局生效)
    """
    # Layer 0: 双重反斜杠归一化（兼容模型输出的 \\(...\\)、\\frac 等）
    text = _normalize_doubled_backslashes(text)

    # Layer 3: 裸命令包裹
    processed = _wrap_bare_latex(text)

    # Layer 3.5: 压平 $$...$$ 块内新行 + 保护 \\\\
    processed = _flatten_display_math(processed)

    # Layer 3.6: 保护数学定界符内部的 _ 和 *（防止 markdown 转成 <em>/<strong>）
    processed = _protect_math_tokens(processed)

    # 标准 markdown 转换
    extensions = [
        "fenced_code",
        "codehilite",
        "tables",
        "footnotes",
        "toc",
        "nl2br",
        "sane_lists",
    ]
    extension_configs = {
        "codehilite": {
            "css_class": "codehilite",
            "linenums": False,
            "guess_lang": True,
            "use_pygments": True,
            "noclasses": False,
            "pygments_style": "github-dark",
        },
        "toc": {"permalink": False},
    }

    result = markdown.markdown(
        processed,
        extensions=extensions,
        extension_configs=extension_configs,
    )

    # 恢复 $$...$$ 中被保护的 markdown 转义序列
    result = _restore_markdown_escapes(result)

    # 恢复数学定界符内部被保护的 _ 和 *
    result = _restore_math_tokens(result)

    # Layer 3.4: 在 HTML 中转义非数学 $<digit>（$50 → $ + 零宽空格 + 50）
    result = _escape_pricing_dollars(result)

    # Layer 2: 代码块 LaTeX 检测
    result = _wrap_latex_codeblocks(result)

    return result


# ──────────────────────────────────────────────────────────
# 构建完整聊天 HTML
# ──────────────────────────────────────────────────────────

# 工具调用图标映射
TOOL_ICONS = {
    "read_file": "📖",
    "write_file": "📝",
    "list_files": "📂",
    "get_file_info": "ℹ️",
}


def _escape_system_message(text: str) -> str:
    """转义系统消息中的特殊字符，保留 HTML 标签。"""
    return html_mod.escape(text)


def format_chat_html(
    messages: list,
    page_title: str = "DeepSeek 对话",
    include_katex: bool = True,
) -> str:
    """将消息列表格式化为完整 HTML 页面。

    Args:
        messages: [{"role": str, "content": str}, ...]
                  其中 role 可为 "user", "assistant", "system", "tool"
        page_title: HTML 页面标题
        include_katex: 是否包含 KaTeX CDN（需要联网）

    Returns:
        完整 HTML 字符串，可直接加载到 HtmlFrame
    """
    chat_items = []

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        timestamp = msg.get("timestamp", "")

        if not content:
            continue

        # 格式化时间
        time_str = ""
        if timestamp:
            time_str = timestamp[-8:] if len(timestamp) >= 8 else timestamp

        if role == "system":
            # 工具调用日志（灰色斜体）
            safe = _escape_system_message(content)
            chat_items.append(
                f'<div class="message system">'
                f'<div class="message-body">{safe}</div>'
                f"</div>"
            )
            continue

        # 用户 / AI 角色
        if role == "user":
            avatar = "👤"
            role_name = "我"
        else:
            avatar = "🤖"
            role_name = "AI"

        # 将内容转为 HTML
        body_html = markdown_to_html(content)

        chat_items.append(
            f'<div class="message {role}">'
            f'<div class="message-header">'
            f'<div class="message-avatar">{avatar}</div>'
            f'<span class="message-role">{role_name}</span>'
            f'<span class="message-time">{time_str}</span>'
            f"</div>"
            f'<div class="message-body">{body_html}</div>'
            f"</div>"
        )

    # 如果没有消息，显示占位
    if not chat_items:
        chat_items.append(
            '<div class="message" style="text-align:center;color:#999;padding:40px;">'
            "开始一段新对话吧 💬</div>"
        )

    katex_html = KATEX_CDN if include_katex else ""

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{html_mod.escape(page_title)}</title>
    <style>{CHAT_CSS}</style>
    {katex_html}
</head>
<body>
    <div class="chat-container">
        {"".join(chat_items)}
    </div>
</body>
</html>"""
    return html


# ──────────────────────────────────────────────────────────
# 快速测试
# ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import tkinter as tk
    from tkinterweb import HtmlFrame

    test_messages = [
        {
            "role": "user",
            "content": "请解释质能方程",
            "timestamp": "2026-06-24 16:00:00",
        },
        {
            "role": "assistant",
            "content": (
                "**质能方程** 是爱因斯坦狭义相对论的核心结论：\n\n"
                "$$E = mc^2$$\n\n"
                "其中：\n"
                "- $E$ 表示能量\n"
                "- $m$ 表示质量\n"
                "- $c$ 表示光速（约为 $3 \\times 10^8$ m/s）\n\n"
                "### 推导要点\n\n"
                "根据相对论，物体的总能量为：\n"
                "$$E = \\frac{m_0 c^2}{\\sqrt{1 - v^2/c^2}}$$\n\n"
                "当 $v \\ll c$ 时，展开得到：\n"
                "$$E \\approx m_0 c^2 + \\frac{1}{2}m_0 v^2 + \\cdots$$\n\n"
                "其中第一项 $m_0 c^2$ 就是**静能**，第二项是经典动能。\n\n"
                "### 代码示例\n\n"
                "```python\ndef energy(mass, c=3e8):\n"
                '    """计算质能方程"""\n'
                "    return mass * c ** 2\n\n"
                'print(f"1kg 物质蕴含能量: {energy(1):.2e} J")\n'
                "```"
            ),
            "timestamp": "2026-06-24 16:00:05",
        },
        {
            "role": "user",
            "content": "写一个解热传导方程的代码",
            "timestamp": "2026-06-24 16:01:00",
        },
        {
            "role": "assistant",
            "content": (
                "## 一维热传导方程\n\n"
                "方程形式：\n"
                "$$\\frac{\\partial u}{\\partial t} = \\alpha^2 \\frac{\\partial^2 u}{\\partial x^2}$$\n\n"
                "可以使用 **有限差分法** 数值求解：\n\n"
                "```python\nimport numpy as np\nimport matplotlib.pyplot as plt\n\n"
                "# 参数设置\nL = 1.0           # 杆长\nT = 0.5           # 总时间\n"
                "Nx = 100          # 空间网格数\nNt = 10000        # 时间步数\n"
                "alpha = 0.01      # 热扩散系数\n\n"
                "dx = L / (Nx - 1)\ndt = T / Nt\n"
                "r = alpha * dt / dx**2\n\n"
                "# 初始条件: 中间高温\nu = np.sin(np.pi * np.linspace(0, L, Nx))\n\n"
                "for n in range(Nt):\n"
                "    u_new = u.copy()\n"
                "    u_new[1:-1] = u[1:-1] + r * (u[2:] - 2*u[1:-1] + u[:-2])\n"
                "    u = u_new\n\n"
                "plt.plot(np.linspace(0, L, Nx), u)\n"
                'plt.title("热传导数值解")\n'
                "plt.show()\n```\n\n"
                "> **提示**: 稳定性条件要求 $r = \\alpha \\Delta t / \\Delta x^2 \\leq 0.5$"
            ),
            "timestamp": "2026-06-24 16:01:10",
        },
    ]

    html = format_chat_html(test_messages)

    root = tk.Tk()
    root.title("HTML 渲染测试")
    root.geometry("800x600")

    frame = HtmlFrame(root)
    frame.load_html(html)
    frame.pack(fill="both", expand=True)

    root.mainloop()
