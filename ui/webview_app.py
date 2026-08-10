"""
pywebview + Flask 聊天界面（当前主 UI）

┌──────────────────────────────────────────────────────────────────┐
│  用户操作                                                           │
│     ↓                                                             │
│  HTML/CSS/JS  (内联在 CHAT_HTML 中)                                 │
│     │  fetch('/api/chat') SSE 流式请求                              │
│     ▼                                                             │
│  Flask 路由层（本文件下半部分）                                        │
│     │                                                             │
│     ├─ /api/chat    → SSE 流式聊天（generate() 生成器）              │
│     ├─ /api/history → 历史记录 CRUD                                 │
│     ├─ /api/reset   → 重置会话                                      │
│     ├─ /api/restore → 恢复历史会话                                   │
│     └─ /            → 返回内联 HTML 页面 (CHAT_HTML)                │
│     │                                                             │
│     ▼                                                             │
│  core/chat.py → DeepSeek API (流式)                                │
└──────────────────────────────────────────────────────────────────┘

关键设计决策：
  - Flask 在后台线程运行（daemon=True），不阻塞 pywebview 主循环
  - 前端通过 fetch() + ReadableStream 消费 SSE，逐 token 渲染
  - 整个 UI 是一个超大内联 HTML 字符串（CHAT_HTML），零外部文件
  - KaTeX 通过 CDN 加载，无需本地安装 LaTeX

维护提示：
  - 新 API 路由 → 在 Flask app 上添加 @app.route
  - 修改前端界面 → 编辑 CHAT_HTML 中的 HTML/CSS/JS
  - 修改后端逻辑 → 编辑 core/chat.py（本文件只做胶水层对接）
  - 内联 HTML 中的 $TITLE 占位符会在 launch() 时被替换
"""

import threading
import json
import os
import time
import logging
import tkinter as tk  # 仅用于获取屏幕尺寸以居中窗口

from flask import Flask, Response, request, jsonify

import webview

from core.chat import ChatSession
from core.tools import TOOLS, execute_tool, SAVE_DIR
from core.history import (
    save_conversation_html, overwrite_conversation_html,
    list_conversations, parse_conversation,
)
from core.html_renderer import markdown_to_html
from core.prompts import build_system_prompt
from config import APP_TITLE, HISTORY_DIR, PROJECT_ROOT, WINDOW_WIDTH, WINDOW_HEIGHT


logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────
# HTML 页面（内嵌）
# ──────────────────────────────────────────────────────────

CHAT_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>$TITLE</title>
<link rel="stylesheet" href="/static/katex/katex.min.css">
<script defer src="/static/katex/katex.min.js"></script>
<script defer src="/static/katex/contrib/auto-render.min.js"
    onload="renderMathInElement(document.body, MATH_OPTIONS)">
</script>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
html, body { height: 100%; overflow: hidden; font-family: 'Cambria Math', 'STIX Two Text', -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans SC', 'Microsoft YaHei', sans-serif; background: #f5f5f5; color: #1a1a2e; font-size: 15px; line-height: 1.7; }
body { display: flex; flex-direction: column; align-items: center; }
.chat-area { flex: 1; min-height: 0; overflow-y: auto; overflow-x: hidden; padding: 16px; display: flex; flex-direction: column; gap: 12px; max-width: 800px; margin: 0 auto; width: 100%; }
.msg { animation: fadeIn 0.3s ease; }
@keyframes fadeIn { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
.msg-header { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; font-size: 13px; }
.msg-avatar { width: 28px; height: 28px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 14px; font-weight: 600; flex-shrink: 0; }
.msg-role { font-weight: 600; color: #666; }
.msg-time { color: #999; margin-left: auto; font-size: 11px; }
.msg-body { padding: 12px 16px; border-radius: 12px; }
.msg.user .msg-avatar { background: #e8f4fd; color: #1a73e8; }
.msg.user .msg-body { background: #e8f4fd; margin-left: 36px; border-radius: 12px 12px 4px 12px; }
.msg.assistant .msg-avatar { background: #e8f5e9; color: #0d652d; }
.msg.assistant .msg-body { background: #fff; margin-right: 36px; border-radius: 12px 12px 12px 4px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
.msg.system .msg-body { color: #888; font-size: 13px; font-style: italic; padding: 4px 12px; }
.msg-body p { margin-bottom: 8px; }
.msg-body p:last-child { margin-bottom: 0; }
.msg-body h1, .msg-body h2, .msg-body h3 { margin: 16px 0 8px; }
.msg-body h1 { font-size: 20px; border-bottom: 1px solid #eee; padding-bottom: 6px; }
.msg-body h2 { font-size: 18px; }
.msg-body h3 { font-size: 16px; }
.msg-body ul, .msg-body ol { padding-left: 24px; margin: 8px 0; }
.msg-body blockquote { border-left: 3px solid #1a73e8; padding: 8px 16px; margin: 8px 0; background: #f8f9fa; border-radius: 0 8px 8px 0; color: #555; }
.msg-body table { border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 14px; }
.msg-body th, .msg-body td { border: 1px solid #e0e0e0; padding: 8px 12px; text-align: left; }
.msg-body th { background: #f5f5f5; font-weight: 600; }
.msg-body code { font-family: 'Cascadia Code', 'Fira Code', 'Consolas', monospace; font-size: 13px; background: #f0f0f0; padding: 2px 6px; border-radius: 4px; color: #d63384; }
.msg-body pre { background: #0d1117; border-radius: 8px; padding: 16px; margin: 8px 0; overflow-x: auto; }
.msg-body pre code { background: transparent; padding: 0; color: #e6edf3; }
.katex-display { text-align: center; margin: 12px 0; overflow-x: auto; }
.katex { font-size: 1.1em; }
.streaming-cursor::after { content: '|'; animation: blink 0.8s infinite; color: #1a73e8; font-weight: bold; }
@keyframes blink { 50% { opacity: 0; } }
.tool-log { color: #888; font-size: 13px; font-style: italic; padding: 4px 0; }
.error-msg { color: #d32f2f; background: #ffebee; padding: 8px 12px; border-radius: 8px; margin: 8px 0; }

/* Reasoning (thinking) section */
.reasoning { font-size: 13px; margin: 4px 36px 8px; border-left: 3px solid #e0a800; }
.reasoning summary { cursor: pointer; color: #b8860b; font-weight: 600; padding: 4px 8px; border-radius: 4px; user-select: none; }
.reasoning summary:hover { background: #fff8e1; }
.reasoning-content { background: #fffbef; padding: 8px 12px; border-radius: 0 8px 8px 8px; color: #666; line-height: 1.6; max-height: 300px; overflow-y: auto; font-family: 'Cascadia Code', 'Fira Code', 'Consolas', monospace; white-space: pre-wrap; font-size: 12px; }

/* Input area */
.input-area { border-top: 1px solid #e0e0e0; background: #fff; padding: 12px 16px; display: flex; gap: 8px; align-items: flex-end; max-width: 800px; margin: 0 auto; width: 100%; flex-shrink: 0; }
#input-box { flex: 1; border: 1px solid #d0d0d0; border-radius: 8px; padding: 10px 14px; font-size: 15px; font-family: inherit; resize: none; outline: none; min-height: 42px; max-height: 150px; }
#input-box:focus { border-color: #1a73e8; }
#send-btn { background: #1a73e8; color: #fff; border: none; border-radius: 8px; padding: 10px 20px; font-size: 15px; cursor: pointer; white-space: nowrap; }
#send-btn:hover { background: #1557b0; }
#send-btn:disabled { background: #ccc; cursor: not-allowed; }

/* History */
.history-panel { position: fixed; left: 0; top: 0; bottom: 0; width: 200px; background: #fff; border-right: 1px solid #e0e0e0; padding: 12px; overflow-y: auto; display: none; }
.history-panel.open { display: block; }
.history-panel h3 { font-size: 14px; margin-bottom: 8px; color: #666; }
.history-item { padding: 6px 8px; border-radius: 4px; cursor: pointer; font-size: 13px; color: #333; margin-bottom: 2px; word-break: break-all; }
.history-item:hover { background: #f0f0f0; }
.history-item { display: flex; align-items: center; justify-content: space-between; gap: 6px; }
.history-title { flex: 1; min-width: 0; }
.history-del { color: #bbb; cursor: pointer; font-size: 12px; padding: 2px 4px; border-radius: 3px; flex-shrink: 0; }
.history-del:hover { color: #d32f2f; background: #ffebee; }
#menu-btn { position: fixed; left: 8px; top: 8px; background: none; border: none; font-size: 20px; cursor: pointer; z-index: 100; color: #666; }
#menu-btn:hover { color: #1a73e8; }
#new-btn { position: fixed; right: 8px; top: 8px; background: #1a73e8; color: #fff; border: none; border-radius: 6px; padding: 6px 14px; font-size: 13px; cursor: pointer; z-index: 100; }
#new-btn:hover { background: #1557b0; }
#summary-btn { position: fixed; right: 96px; top: 8px; background: #fff; color: #1a73e8; border: 1px solid #1a73e8; border-radius: 6px; padding: 5px 12px; font-size: 13px; cursor: pointer; z-index: 100; }
#summary-btn:hover { background: #e8f4fd; }
#stop-btn { background: #fff; color: #d32f2f; border: 1px solid #d32f2f; border-radius: 8px; padding: 10px 16px; font-size: 14px; cursor: pointer; white-space: nowrap; }
#stop-btn:hover { background: #ffebee; }

/* Loading */
.loading { text-align: center; color: #999; padding: 20px; }
.loading::after { content: '...'; animation: dots 1.5s infinite; }
@keyframes dots { 0%,20% { content: '.'; } 40% { content: '..'; } 60%,100% { content: '...'; } }
</style>
</head>
<body>

<button id="menu-btn" onclick="toggleHistory()">☰</button>
<button id="new-btn" onclick="newConversation()">新对话</button>
<button id="summary-btn" onclick="openSummary()">总结</button>

<div class="history-panel" id="history-panel">
  <h3>历史记录</h3>
  <div id="history-list"></div>
</div>

<div class="chat-area" id="chat-area"></div>

<div class="input-area">
  <textarea id="input-box" rows="1" placeholder="输入消息..." onkeydown="onKeyDown(event)"></textarea>
  <button id="stop-btn" onclick="stopStreaming()" style="display:none">停止</button>
  <button id="send-btn" onclick="sendMessage()">发送</button>
</div>

<script>
const chatArea = document.getElementById('chat-area');
const inputBox = document.getElementById('input-box');
const sendBtn = document.getElementById('send-btn');
const stopBtn = document.getElementById('stop-btn');
let isLoading = false;
let reasoningText = '';
let currentStreamId = 0;
let currentController = null;  // 当前 SSE 请求（用于"停止"中断）
let cancelled = false;         // 用户是否主动停止

// KaTeX 渲染配置（页面加载与手动渲染共用，确保 $...$ 也能渲染）
const MATH_OPTIONS = {
    delimiters: [
        {left: '$$', right: '$$', display: true},
        {left: '$', right: '$', display: false},
        {left: '\\(', right: '\\)', display: false},
        {left: '\\[', right: '\\]', display: true}
    ],
    throwOnError: false,
    trust: true
};
function renderMath(el) {
    if (!el || !window.renderMathInElement) return;
    try { renderMathInElement(el, MATH_OPTIONS); } catch(e) {}
}

// ── 回答完成通知（气泡音） ──
let audioCtx = null;
function ensureAudio() {
    if (!audioCtx) {
        try { audioCtx = new (window.AudioContext || window.webkitAudioContext)(); } catch(e) { return null; }
    }
    if (audioCtx.state === 'suspended') {
        audioCtx.resume();
    }
    return audioCtx;
}
function playBubbleSound() {
    const ctx = ensureAudio();
    if (!ctx) return;
    try {
        const now = ctx.currentTime;
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = 'sine';
        osc.frequency.setValueAtTime(720, now);
        osc.frequency.exponentialRampToValueAtTime(240, now + 0.12);
        gain.gain.setValueAtTime(0.0001, now);
        gain.gain.exponentialRampToValueAtTime(0.35, now + 0.015);
        gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.22);
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start(now);
        osc.stop(now + 0.25);
    } catch(e) {}
}

// ── 欢迎词 ──
function showWelcome() {
    chatArea.innerHTML = `
    <div class="msg assistant" style="animation:fadeIn 0.3s ease;">
      <div class="msg-header">
        <div class="msg-avatar" style="background:#e8f5e9;color:#0d652d;">🤖</div>
        <span class="msg-role">AI</span>
      </div>
      <div class="msg-body" style="background:#fff;border-radius:12px 12px 12px 4px;padding:20px 24px;box-shadow:0 1px 3px rgba(0,0,0,0.08);font-size:15px;line-height:1.8;">
        <h2 style="margin:0 0 8px;font-size:20px;">👋 欢迎使用 DeepSeek 本地问答</h2>
        <p style="margin:0 0 12px;color:#555;">
          基于 DeepSeek API 的桌面聊天工具，支持：
        </p>
        <ul style="margin:0 0 12px;padding-left:20px;color:#555;">
          <li><strong>智能对话</strong> — 上下文延续，流式输出，联网搜索</li>
          <li><strong>文件操作</strong> — AI 可以直接读写你本地的文件</li>
          <li><strong>深度思考</strong> — 支持推理模型的思考过程展示</li>
          <li><strong>公式渲染</strong> — KaTeX 引擎，与官网效果一致</li>
        </ul>
        <hr style="border:none;border-top:1px solid #eee;margin:12px 0;">
        <p style="margin:0;color:#999;font-size:13px;">在下方输入框开始对话吧。</p>
      </div>
    </div>`;
    forceScrollToBottom();
}

// 页面加载时显示欢迎词
showWelcome();

// ── 发送消息 ──
function sendMessage() {
    const text = inputBox.value.trim();
    if (!text || isLoading) return;
    inputBox.value = '';
    inputBox.style.height = 'auto';
    isLoading = true;
    sendBtn.disabled = true;
    stopBtn.style.display = 'inline-block';
    reasoningText = '';
    cancelled = false;
    currentController = new AbortController();
    ensureAudio();  // 用户手势内创建/恢复音频上下文，保证后续能出声

    // 显示用户消息（含完整日期时间）
    const now = new Date().toLocaleString();
    let fullContent = '';
    addMessage('user', text, now);

    // 显示加载占位
    const loadingDiv = document.createElement('div');
    loadingDiv.className = 'msg assistant';
    loadingDiv.id = 'streaming-msg';
    loadingDiv.innerHTML = '<div class="msg-header"><div class="msg-avatar">🤖</div><span class="msg-role">AI</span><span class="msg-time">' + now + '</span></div><details class="reasoning" id="reasoning-box" style="display:none"><summary>深度思考过程</summary><div class="reasoning-content" id="reasoning-content"></div></details><div class="msg-body"><div class="loading">思考中</div></div>';
    chatArea.appendChild(loadingDiv);
    forceScrollToBottom();

    // SSE 流式请求
    currentStreamId++;
    const streamId = currentStreamId;
    fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text }),
        signal: currentController.signal
    }).then(response => {
        const reader = response.body.getReader();
        const decoder = new TextDecoder();

        function processChunk({ done, value }) {
            if (done || streamId !== currentStreamId) {
                finishStream(fullContent, now);
                return;
            }
            const chunk = decoder.decode(value, { stream: true });
            const lines = chunk.split('\n');
            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    const data = line.slice(6);
                    if (data === '[DONE]') {
                        finishStream(fullContent, now);
                        return;
                    }
                    try {
                        const parsed = JSON.parse(data);
                        if (parsed.type === 'chunk') {
                            fullContent += parsed.text;
                            updateStreaming(fullContent, now);
                        } else if (parsed.type === 'reasoning') {
                            // 深度思考过程
                            reasoningText += parsed.text;
                            updateReasoning(reasoningText, now);
                        } else if (parsed.type === 'reasoning_end') {
                            // 思考结束，折叠
                            finalizeReasoning(now);
                        } else if (parsed.type === 'render') {
                             // 服务器端渲染的完整 HTML，替换流式内容
                             finishStreamHtml(parsed.html, now);
                         } else if (parsed.type === 'cancelled') {
                            cancelled = true;
                            finishStream(fullContent + '\n\n*（已停止生成）*', now);
                            return;
                         } else if (parsed.type === 'tool') {
                            fullContent += '\n\n*' + parsed.text + '*\n\n';
                            updateStreaming(fullContent, now);
                        } else if (parsed.type === 'error') {
                            showError(parsed.text);
                            return;
                        }
                    } catch(e) {}
                }
            }
            return reader.read().then(processChunk);
        }
        return reader.read().then(processChunk);
    }).catch(err => {
        if (cancelled) {
            finishStream(fullContent + '\n\n*（已停止生成）*', now);
        } else {
            showError('网络错误: ' + err.message);
        }
    });
}

function stopStreaming() {
    if (!isLoading) return;
    cancelled = true;
    if (currentController) currentController.abort();
    // 通知后端停止生成（节省 token）
    fetch('/api/cancel', { method: 'POST' }).catch(() => {});
}

function updateStreaming(content, time) {
    const el = document.getElementById('streaming-msg');
    if (!el) return;
    const html = markedToHtml(content);
    el.querySelector('.msg-body').innerHTML = '<div class="streaming-cursor">' + html + '</div>';
    scrollToBottom();
    // Re-render KaTeX for new content
    renderMath(el.querySelector('.msg-body'));
}

function finishStream(content, time) {
    isLoading = false;
    sendBtn.disabled = false;
    stopBtn.style.display = 'none';
    const el = document.getElementById('streaming-msg');
    if (!el) return;
    el.id = '';
    const html = markedToHtml(content);
    el.querySelector('.msg-body').innerHTML = html;
    renderMath(el.querySelector('.msg-body'));
    scrollToBottom();
    playBubbleSound();  // 回答完成通知
}

function finishStreamHtml(html, time) {
    isLoading = false;
    sendBtn.disabled = false;
    stopBtn.style.display = 'none';
    const el = document.getElementById('streaming-msg');
    if (!el) return;
    el.id = '';
    el.querySelector('.msg-body').innerHTML = html;
    renderMath(el.querySelector('.msg-body'));
    scrollToBottom();
    playBubbleSound();  // 回答完成通知
}

// ── 深度思考显示 ──
function updateReasoning(text, time) {
    const box = document.getElementById('reasoning-box');
    const content = document.getElementById('reasoning-content');
    if (!box || !content) return;
    // 第一次收到推理内容时显示
    if (box.style.display === 'none') {
        box.style.display = 'block';
        box.open = true;  // 默认展开
    }
    content.textContent = text;
}

function finalizeReasoning(time) {
    const box = document.getElementById('reasoning-box');
    if (!box) return;
    // 思考结束后默认折叠（干净）
    box.open = false;
}

function showError(msg) {
    isLoading = false;
    sendBtn.disabled = false;
    stopBtn.style.display = 'none';
    const el = document.getElementById('streaming-msg');
    if (el) el.remove();
    addMessage('assistant', '**错误**: ' + msg, new Date().toLocaleString());
}

// ── 添加历史消息 ──
function addMessage(role, content, time) {
    const div = document.createElement('div');
    div.className = 'msg ' + role;
    const emoji = role === 'user' ? '👤' : '🤖';
    const name = role === 'user' ? '我' : 'AI';
    div.innerHTML = '<div class="msg-header"><div class="msg-avatar">' + emoji + '</div><span class="msg-role">' + name + '</span><span class="msg-time">' + (time || '') + '</span></div><div class="msg-body">' + markedToHtml(content) + '</div>';
    chatArea.appendChild(div);
    renderMath(div);
    forceScrollToBottom();
}

// ── 添加预渲染消息（公式不被 markedToHtml 破坏）──
function addMessageHtml(role, html, time) {
    const div = document.createElement('div');
    div.className = 'msg ' + role;
    const emoji = role === 'user' ? '👤' : '🤖';
    const name = role === 'user' ? '我' : 'AI';
    div.innerHTML = '<div class="msg-header"><div class="msg-avatar">' + emoji + '</div><span class="msg-role">' + name + '</span><span class="msg-time">' + (time || '') + '</span></div><div class="msg-body">' + html + '</div>';
    chatArea.appendChild(div);
    renderMath(div);
    forceScrollToBottom();
}

// ── Markdown → HTML（流式显示用，块级渲染：标题/列表/引用/表格/代码块）──
function markedToHtml(text) {
    if (!text) return '';

    // 兼容模型输出的双重反斜杠（\\(...\\)、\\frac 等），归一化为单反斜杠
    text = text.replace(/\\\\([A-Za-z()\[\]{}])/g, '\\$1');

    // 1. 保护代码块
    const codeBlocks = [];
    text = text.replace(/```(\w*)\n([\s\S]*?)```/g, (m, lang, code) => {
        codeBlocks.push('<pre><code class="language-' + (lang || '') + '">' + code + '</code></pre>');
        return '\x00CODE' + (codeBlocks.length - 1) + '\x00';
    });

    // 2. 行内样式
    function inline(s) {
        if (s.indexOf('\x00CODE') !== -1) return s;
        // 保护数学片段，避免 _ * 被当作文本样式处理（与后端 markdown 处理一致）
        const mathSpans = [];
        s = s.replace(/\$\$[^$]*\$\$|\$[^$]*\$/g, (m) => {
            mathSpans.push(m);
            return '\x00MATH' + (mathSpans.length - 1) + '\x00';
        });
        s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
        s = s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
        s = s.replace(/__(.+?)__/g, '<strong>$1</strong>');
        s = s.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
        s = s.replace(/(^|[^_])_([^_\n]+)_/g, '$1<em>$2</em>');
        s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank">$1</a>');
        s = s.replace(/\x00MATH(\d+)\x00/g, (m, i) => mathSpans[parseInt(i, 10)] || '');
        return s;
    }

    // 3. 表格（| a | b | 分隔行 |---|）
    function renderTable(rows) {
        const body = rows
            .map(r => r.replace(/^\||\|$/g, '').split('|').map(c => c.trim()))
            .filter((row, i) => !(i === 1 && row.every(c => /^:?-{2,}:?$/.test(c))));
        if (body.length === 0) return '';
        let html = '<table><thead><tr>' + body[0].map(c => '<th>' + inline(c) + '</th>').join('') + '</tr></thead>';
        html += '<tbody>' + body.slice(1).map(r => '<tr>' + r.map(c => '<td>' + inline(c) + '</td>').join('') + '</tr>').join('') + '</tbody></table>';
        return html;
    }

    // 4. 块级逐行处理
    const out = [];
    let listTag = null;
    let quoteBuf = [];
    let tableRows = [];
    for (const raw of text.split('\n')) {
        const line = raw.trimEnd();

        if (line.indexOf('\x00CODE') !== -1) {
            const idx = parseInt(line.replace(/\D/g, ''), 10);
            out.push(codeBlocks[idx] || '');
            continue;
        }

        // 标题
        let m = line.match(/^(#{1,6})\s+(.*)$/);
        if (m) {
            const level = m[1].length;
            out.push('<h' + level + '>' + inline(m[2]) + '</h' + level + '>');
            continue;
        }

        // 引用
        if (line.startsWith('> ')) {
            quoteBuf.push(inline(line.slice(2)));
            continue;
        }
        if (quoteBuf.length) {
            out.push('<blockquote>' + quoteBuf.join('<br>') + '</blockquote>');
            quoteBuf = [];
        }

        // 表格行
        if (line.startsWith('|') && line.endsWith('|')) {
            tableRows.push(line);
            continue;
        }
        if (tableRows.length) {
            out.push(renderTable(tableRows));
            tableRows = [];
        }

        // 无序列表
        m = line.match(/^\s*[-*+]\s+(.*)$/);
        if (m) {
            if (listTag !== 'ul') {
                if (listTag) out.push('</' + listTag + '>');
                listTag = 'ul';
                out.push('<ul>');
            }
            out.push('<li>' + inline(m[1]) + '</li>');
            continue;
        }
        // 有序列表
        m = line.match(/^\s*\d+[.、]\s+(.*)$/);
        if (m) {
            if (listTag !== 'ol') {
                if (listTag) out.push('</' + listTag + '>');
                listTag = 'ol';
                out.push('<ol>');
            }
            out.push('<li>' + inline(m[1]) + '</li>');
            continue;
        }
        if (listTag) {
            out.push('</' + listTag + '>');
            listTag = null;
        }

        // 分隔线
        if (/^([-*_]\s*){3,}$/.test(line)) {
            out.push('<hr>');
            continue;
        }

        // 普通段落
        if (line.trim() === '') continue;
        out.push('<p>' + inline(line) + '</p>');
    }
    if (quoteBuf.length) out.push('<blockquote>' + quoteBuf.join('<br>') + '</blockquote>');
    if (tableRows.length) out.push(renderTable(tableRows));
    if (listTag) out.push('</' + listTag + '>');

    // 5. 兜底恢复占位符
    return out.join('\n').replace(/\x00CODE(\d+)\x00/g, (m, i) => codeBlocks[parseInt(i, 10)] || '');
}

function scrollToBottom() {
    // 如果用户已滚动到远离底部位置，不强制滚动
    const threshold = 60;
    const distance = chatArea.scrollHeight - chatArea.scrollTop - chatArea.clientHeight;
    if (distance > threshold) return;
    chatArea.scrollTop = chatArea.scrollHeight;
}

function forceScrollToBottom() {
    chatArea.scrollTop = chatArea.scrollHeight;
}

function onKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
    // Auto-resize
    inputBox.style.height = 'auto';
    inputBox.style.height = Math.min(inputBox.scrollHeight, 150) + 'px';
}

// ── 历史记录 ──
function toggleHistory() {
    const panel = document.getElementById('history-panel');
    panel.classList.toggle('open');
    loadHistoryList();
}

function loadHistoryList() {
    fetch('/api/history').then(r => r.json()).then(data => {
        const list = document.getElementById('history-list');
        list.innerHTML = '';
        if (data.error) {
            list.innerHTML = '<div style="color:#999;font-size:13px;">' + data.error + '</div>';
            return;
        }
        if (data.length === 0) {
            list.innerHTML = '<div style="color:#999;font-size:13px;">暂无历史记录</div>';
            return;
        }
        data.forEach(item => {
            const div = document.createElement('div');
            div.className = 'history-item';
            const timeSpan = item.time ? `<span style="font-size:11px;color:#999;display:block;">${item.time}</span>` : '';
            const titleSpan = `<span class="history-title">${timeSpan}${item.title || item.filename}</span>`;
            const delBtn = `<span class="history-del" title="删除">✕</span>`;
            div.innerHTML = titleSpan + delBtn;
            div.onclick = () => loadHistory(item.filename);
            div.querySelector('.history-del').onclick = (e) => {
                e.stopPropagation();
                deleteHistory(item.filename);
            };
            list.appendChild(div);
        });
    });
}

function deleteHistory(filename) {
    if (!confirm('确定删除这条历史记录？')) return;
    fetch('/api/history/' + encodeURIComponent(filename), { method: 'DELETE' })
        .then(r => r.json())
        .then(d => { if (!d.error) loadHistoryList(); })
        .catch(() => {});
}

function openSummary() {
    fetch('/api/open-summary', { method: 'POST' }).catch(() => {});
}

function loadHistory(filename) {
    if (isLoading) return;
    fetch('/api/history/' + encodeURIComponent(filename)).then(r => r.json()).then(data => {
        if (data.error) return;

        // 1. 恢复会话上下文到后端
        fetch('/api/restore', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ messages: data.messages, filename: data.filename })
        }).then(() => {
            // 2. 渲染消息（使用服务端预渲染的 HTML）
            chatArea.innerHTML = '';
            for (const msg of data.messages) {
                // 构建消息内容
                let bodyHtml = msg.html || '';
                // 如果有思考过程，添加可折叠面板
                if (msg.reasoning_content) {
                    const rcEscaped = msg.reasoning_content
                        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
                    bodyHtml = '<details class="reasoning" style="margin:4px 0 8px;font-size:13px;border-left:3px solid #e0a800;padding-left:8px"><summary style="cursor:pointer;color:#b8860b;font-weight:600">深度思考过程</summary><div style="background:#fffbef;padding:8px 12px;border-radius:0 8px 8px 8px;color:#666;line-height:1.6;font-family:monospace;font-size:12px;white-space:pre-wrap">' + rcEscaped + '</div></details>' + bodyHtml;
                }
                addMessageHtml(msg.role, bodyHtml, msg.timestamp ? msg.timestamp.slice(-8) : '');
            }
        });

        document.getElementById('history-panel').classList.remove('open');
    });
}

function newConversation() {
    if (isLoading) return;
    if (chatArea.children.length > 0 && !confirm('确定开始新对话？当前对话将自动保存。')) return;
    fetch('/api/reset', { method: 'POST' }).then(() => {
        showWelcome();
    });
}

// Auto-resize
inputBox.addEventListener('input', () => {
    inputBox.style.height = 'auto';
    inputBox.style.height = Math.min(inputBox.scrollHeight, 150) + 'px';
});
</script>
</body>
</html>
""".replace("$TITLE", APP_TITLE)

# ──────────────────────────────────────────────────────────
# Flask 应用
# ──────────────────────────────────────────────────────────

app = Flask(__name__, static_folder=os.path.join(PROJECT_ROOT, "static"))
session_lock = threading.Lock()
chat_session = None
current_history_file = None  # 当前会话对应的历史文件（同会话内覆盖保存，避免重复副本）


def _save_current_session():
    """将当前会话保存为 HTML 文件。"""
    global chat_session, current_history_file
    if chat_session is None:
        return
    try:
        messages = chat_session.get_messages()
        if len(messages) < 2:  # 至少要有来回
            return
        # 渲染消息为简单 HTML（不含页面框架）
        chat_html = _render_messages_html(messages)
        if current_history_file and os.path.isfile(current_history_file):
            # 同一会话：覆盖原文件，避免历史目录堆积重复副本
            overwrite_conversation_html(current_history_file, messages, chat_html)
        else:
            current_history_file = save_conversation_html(messages, HISTORY_DIR, chat_html)
    except Exception:
        logger.exception("保存会话失败")


def _render_messages_html(messages: list) -> str:
    """将消息列表渲染为聊天区 HTML（不含 <html>/<head>/<body>）。"""
    items = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        reasoning = msg.get("reasoning_content", "")
        if not content and not reasoning:
            continue
        label = "我" if role == "user" else "AI"
        ts = msg.get("timestamp", "")
        time_str = ts if len(ts) >= 16 else (ts[-8:] if len(ts) >= 8 else "")
        html_content = markdown_to_html(content) if content else ""

        # 思考过程（可折叠）
        reasoning_html = ""
        if reasoning:
            rc_escaped = reasoning.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            reasoning_html = f'<details class="reasoning" style="margin:4px 0 8px"><summary>深度思考过程</summary><div class="reasoning-content" style="background:#fffbef;padding:8px 12px;border-radius:0 8px 8px 8px;color:#666;line-height:1.6;font-family:monospace;font-size:12px;white-space:pre-wrap">{rc_escaped}</div></details>'

        items.append(
            f'<div class="msg {role}">'
            f'<div class="msg-header"><strong>{label}</strong>'
            f'<span style="float:right;color:#999;font-size:11px">{time_str}</span></div>'
            f'{reasoning_html}'
            f'<div class="msg-body">{html_content}</div>'
            f"</div>"
        )
    return "\n".join(items)


def _init_session():
    """初始化（或重置）ChatSession。"""
    global chat_session, current_history_file
    # 保存旧会话（如果有）
    _save_current_session()
    current_history_file = None

    chat_session = ChatSession(
        system_prompt=build_system_prompt(),
        tools=TOOLS,
        tool_executor=execute_tool,
    )


@app.route("/")
def index():
    return CHAT_HTML


@app.route("/api/chat", methods=["POST"])
def api_chat():
    """SSE 流式聊天接口。"""
    data = request.get_json(silent=True)
    if not data or "message" not in data:
        return jsonify({"error": "缺少 message 字段"}), 400

    message = data["message"]

    def generate():
        with session_lock:
            if chat_session is None:
                _init_session()

            try:
                full_content = ""
                reasoning_active = False
                for chunk in chat_session.ask_stream(message):
                    # 用户点击"停止"：不保存半截回复
                    if "\x00CANCEL\x00" in chunk:
                        yield f"data: {json.dumps({'type': 'cancelled'})}\n\n"
                        return
                    # 深度思考标记
                    if "\x00RSNG\x00" in chunk:
                        # 提取推理内容
                        parts = chunk.split("\x00RSNG\x00")
                        for part in parts:
                            if "\x00RSNG_END\x00" in part:
                                rc = part.replace("\x00RSNG_END\x00", "")
                                yield f"data: {json.dumps({'type': 'reasoning', 'text': rc})}\n\n"
                            elif part:
                                yield f"data: {json.dumps({'type': 'reasoning', 'text': part})}\n\n"
                        continue
                    if chunk.startswith("[工具") or chunk.startswith("\n\n[工具"):
                        yield f"data: {json.dumps({'type': 'tool', 'text': chunk.strip()})}\n\n"
                    else:
                        # 流式显示：兼容模型输出的双重反斜杠定界符 \\(...\\)、\\[...\\]
                        processed = chunk
                        processed = processed.replace(r"\\(", "$").replace(r"\\)", "$")
                        processed = processed.replace(r"\\[", "$$").replace(r"\\]", "$$")
                        processed = processed.replace(r"\(", "$").replace(r"\)", "$")
                        processed = processed.replace(r"\[", "$$").replace(r"\]", "$$")
                        full_content += chunk
                        yield f"data: {json.dumps({'type': 'chunk', 'text': processed})}\n\n"

                # 关闭推理区域
                yield f"data: {json.dumps({'type': 'reasoning_end'})}\n\n"

                # 完整渲染
                rendered = markdown_to_html(full_content)
                yield f"data: {json.dumps({'type': 'render', 'html': rendered})}\n\n"
                yield "data: [DONE]\n\n"
                # 每轮回答完成后自动保存（同一会话覆盖同一文件，不产生重复副本）
                _save_current_session()

            except Exception as e:
                logger.exception("聊天接口异常")
                yield f"data: {json.dumps({'type': 'error', 'text': str(e)})}\n\n"

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.route("/api/cancel", methods=["POST"])
def api_cancel():
    """取消当前生成（用户点击"停止"）。"""
    with session_lock:
        if chat_session is not None:
            chat_session.cancel()
    return jsonify({"ok": True})


@app.route("/api/open-summary", methods=["POST"])
def api_open_summary():
    """在资源管理器中打开总结目录。"""
    try:
        os.makedirs(SAVE_DIR, exist_ok=True)
        os.startfile(SAVE_DIR)  # type: ignore[attr-defined]
        return jsonify({"ok": True})
    except Exception as e:
        logger.exception("打开总结目录失败")
        return jsonify({"error": str(e)}), 500


@app.route("/api/reset", methods=["POST"])
def api_reset():
    """重置对话。"""
    with session_lock:
        _init_session()
    return jsonify({"ok": True})


@app.route("/api/history")
def api_history():
    """列出历史对话。

    返回格式（含完整日期 + 标题）：
      filename: 原始文件名
      title:    仅对话标题（不含时间戳）
      time:     时间戳前缀（YYYY-MM-DD HH:mm:SS）
    """
    try:
        files = list_conversations(HISTORY_DIR)
        result = []
        for fname, fpath in files:
            ext = os.path.splitext(fname)[1]
            name_no_ext = fname.replace(ext, "")
            # 文件名格式：YYYY-MM-DD_HH-mm-SS_标题
            parts = name_no_ext.split("_", 2)
            if len(parts) >= 3:
                date_part = parts[0]  # YYYY-MM-DD
                time_part = parts[1].replace("-", ":")  # HH:mm:SS
                title = parts[2]
                display_time = f"{date_part} {time_part}"
            elif len(parts) == 2:
                display_time = parts[0].replace("-", "/")
                title = parts[1]
            else:
                display_time = ""
                title = name_no_ext
            result.append({"filename": fname, "title": title, "time": display_time})
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/history/<filename>")
def api_history_load(filename):
    """加载历史对话。"""
    try:
        filepath = os.path.join(HISTORY_DIR, filename)
        messages = parse_conversation(filepath)

        # 服务端预渲染每条消息（保留 $$...$$ 不被 markedToHtml 的 \n→<br> 破坏）
        rendered = []
        for msg in messages:
            entry = {
                "role": msg["role"],
                "content": msg["content"],
                "html": markdown_to_html(msg["content"]),
                "timestamp": msg.get("timestamp", ""),
            }
            # 保留 reasoning_content（DeepSeek 推理模型要求回传）
            if msg.get("reasoning_content"):
                entry["reasoning_content"] = msg["reasoning_content"]
            rendered.append(entry)

        return jsonify({"messages": rendered, "filename": filename})
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/restore", methods=["POST"])
def api_restore():
    """恢复历史会话上下文。"""
    global current_history_file
    data = request.get_json(silent=True)
    if not data or "messages" not in data:
        return jsonify({"error": "缺少 messages 字段"}), 400

    with session_lock:
        if chat_session is None:
            _init_session()
        # 保留 reasoning_content（DeepSeek 推理模型要求回传）
        hist = []
        for m in data["messages"]:
            if m.get("role") in ("user", "assistant"):
                entry = {"role": m["role"], "content": m.get("content", "")}
                if m.get("reasoning_content"):
                    entry["reasoning_content"] = m["reasoning_content"]
                hist.append(entry)
        chat_session.restore(hist)
        # 恢复后继续对话时，覆盖保存到同一个历史文件
        filename = data.get("filename") or ""
        restore_path = os.path.join(HISTORY_DIR, filename) if filename else ""
        current_history_file = restore_path if restore_path and os.path.isfile(restore_path) else None
    return jsonify({"ok": True})


@app.route("/api/history/<filename>", methods=["DELETE"])
def api_history_delete(filename):
    """删除历史对话。"""
    try:
        filepath = os.path.join(HISTORY_DIR, filename)
        if os.path.exists(filepath):
            os.remove(filepath)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)})


# ──────────────────────────────────────────────────────────
# 启动
# ──────────────────────────────────────────────────────────

def run_server(port=5000):
    """在后台线程运行 Flask 开发服务器。

    注意：
      - debug=False 防止 Flask 自动重载（会启动新进程，与 pywebview 冲突）
      - use_reloader=False 同理
      - 生产环境建议换用 waitress/gunicorn，本地开发 Flask dev server 够用
    """
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)


def _find_free_port(preferred: int) -> int:
    """优先使用指定端口；被占用时自动挑选一个空闲端口。"""
    import socket
    for port in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1] if port == 0 else port
            except OSError:
                continue
    return 0


def _wait_server_ready(port: int, timeout: float = 15.0) -> bool:
    """轮询探测 Flask 是否已就绪（替代固定 sleep）。"""
    import socket
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def launch(port=5000):
    """启动 Flask 服务器 + pywebview 窗口 —— 主入口。

    启动时序：
      1. _init_session()      → 初始化 ChatSession（含 system prompt）
      2. run_server(daemon)    → Flask 在后台线程启动
      3. _wait_server_ready()  → 轮询等待 Flask 就绪
      4. webview.create_window → 创建桌面窗口，加载 http://127.0.0.1:port/
      5. webview.start()      → 进入事件循环（阻塞，直到窗口关闭）
      6. _save_current_session → 窗口关闭后自动保存对话

    参数：
      port: Flask 监听端口。如需多开实例，修改此值避免端口冲突。
    """
    # 1. 初始化会话（含 system prompt、工具注册）
    _init_session()

    # 2. 启动 Flask 服务器（daemon 线程，主线程退出时自动结束）
    #    端口被占用时自动挑选空闲端口，避免启动失败
    port = _find_free_port(port)
    server_thread = threading.Thread(target=run_server, args=(port,), daemon=True)
    server_thread.start()
    if not _wait_server_ready(port):
        logger.warning("Flask 服务器未能及时就绪，继续等待窗口加载...")

    # 3. 打开 pywebview 窗口
    # 计算居中坐标，使窗口出现在屏幕正中央
    root = tk.Tk()
    root.withdraw()  # 隐藏临时窗口
    screen_w = root.winfo_screenwidth()
    screen_h = root.winfo_screenheight()
    root.destroy()
    WINDOW_W, WINDOW_H = WINDOW_WIDTH, WINDOW_HEIGHT
    win_x = max(0, (screen_w - WINDOW_W) // 2)
    win_y = max(0, (screen_h - WINDOW_H) // 2)

    window = webview.create_window(
        APP_TITLE,
        f"http://127.0.0.1:{port}/",
        width=WINDOW_W,
        height=WINDOW_H,
        x=win_x,
        y=win_y,
        min_size=(600, 400),
        resizable=True,
        text_select=True,    # 允许用户在聊天区选中文本
    )
    # private_mode=False 表示使用用户默认浏览器数据（Cookie、缓存等）
    # icon 参数（Windows WinForms 后端）：自定义窗口/任务栏图标，仅支持 .ico
    icon_path = os.path.join(PROJECT_ROOT, "icon.ico")
    if not os.path.isfile(icon_path):
        icon_path = None  # 图标缺失时退回默认图标
    webview.start(private_mode=False, icon=icon_path)

    # 4. 窗口关闭后保存对话（兜底，_init_session / 回答完成时已自动保存）
    _save_current_session()


if __name__ == "__main__":
    launch()
