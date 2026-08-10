"""
deepseek_client.ui — 用户界面层

唯一界面方案：
  - webview_app.py — pywebview（Edge WebView2）+ Flask 实现
    支持流式 SSE 响应、深度思考展示、Function Calling、KaTeX 公式渲染。

界面层只做"胶水"对接：Flask 路由转发给 core/ 层，前端为内联 HTML/CSS/JS。
"""
