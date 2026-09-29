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
│     ├─ /api/chat       → SSE 流式聊天（generate() 生成器，按 slot 定位会话）│
│     ├─ /api/history    → 历史记录 CRUD                                 │
│     ├─ /api/reset      → 重置该窗口的会话                              │
│     ├─ /api/restore    → 恢复历史会话                                   │
│     ├─ /api/new-window → 同进程内再开一个独立对话窗口                    │
│     └─ /               → 返回内联 HTML 页面 (CHAT_HTML)                │
│     │                                                             │
│     ▼                                                             │
│  core/chat.py → DeepSeek API (流式)                                │
└──────────────────────────────────────────────────────────────────┘

关键设计决策：
  - Flask 在后台线程运行（daemon=True），不阻塞 pywebview 主循环
  - 前端通过 fetch() + ReadableStream 消费 SSE，逐 token 渲染
  - 会话按「槽（slot）」隔离：一个窗口一个槽，各自一份上下文/锁/历史文件，
    因此多个对话可同时进行；页面 URL 的 ?slot= 决定本窗口是哪个槽
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
import re
import time
import logging
import tkinter as tk  # 仅用于获取屏幕尺寸以居中窗口
import webbrowser

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
from config import (
    APP_TITLE, HISTORY_DIR, PROJECT_ROOT, WINDOW_WIDTH, WINDOW_HEIGHT,
    SOURCE, MODEL, SOURCES, ENABLE_SEARCH, MAX_WINDOWS,
)


logger = logging.getLogger(__name__)

# ── 图片输入（识图模型）限制 ──
IMAGE_MAX_BYTES = 5 * 1024 * 1024  # 单张图片解码后大小上限（保守值：智谱限制 5MB，官方内联单图可到 32MB）
IMAGE_MAX_COUNT = 4                # 每条消息最多图片数
_IMAGE_DATA_URL_RE = re.compile(
    r"^data:image/(png|jpe?g|webp|gif);base64,([A-Za-z0-9+/=]+)$"
)

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

/* 图片输入（识图模型） */
#img-btn { background: #fff; border: 1px solid #d0d0d0; border-radius: 8px; font-size: 18px; padding: 8px 12px; cursor: pointer; line-height: 1; }
#img-btn:hover:not(:disabled) { border-color: #1a73e8; background: #e8f4fd; }
#img-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.img-previews { display: none; flex-wrap: wrap; gap: 8px; max-width: 800px; width: 100%; margin: 0 auto; padding: 8px 16px 0; }
.img-previews.has-imgs { display: flex; }
.img-thumb { position: relative; width: 64px; height: 64px; border-radius: 8px; border: 1px solid #e0e0e0; overflow: hidden; background: #fafafa; }
.img-thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
.img-thumb .img-remove { position: absolute; top: 2px; right: 2px; width: 18px; height: 18px; line-height: 16px; text-align: center; background: rgba(0,0,0,0.55); color: #fff; border-radius: 50%; cursor: pointer; font-size: 11px; }
.img-thumb .img-remove:hover { background: #d32f2f; }

/* 顶部来源/模型切换条（一键换源） */
.top-bar { position: fixed; top: 8px; left: 50%; transform: translateX(-50%); display: flex; gap: 6px; align-items: center; z-index: 99; background: #fff; border: 1px solid #e0e0e0; border-radius: 8px; padding: 4px 10px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); }
.top-bar label { font-size: 12px; color: #666; white-space: nowrap; }
.top-bar select { border: 1px solid #d0d0d0; border-radius: 6px; padding: 4px 6px; font-size: 13px; background: #fff; color: #1a1a2e; outline: none; max-width: 170px; }
.top-bar select:focus { border-color: #1a73e8; }

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
#win-btn { position: fixed; right: 168px; top: 8px; background: #fff; color: #666; border: 1px solid #d0d0d0; border-radius: 6px; padding: 5px 12px; font-size: 13px; cursor: pointer; z-index: 100; }
#win-btn:hover { color: #1a73e8; border-color: #1a73e8; background: #e8f4fd; }
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
<button id="win-btn" onclick="newWindow()" title="再开一个独立对话窗口（同一进程，可并行生成）">新窗口</button>

<div class="top-bar" id="top-bar">
  <label for="source-select">来源</label>
  <select id="source-select" onchange="onSourceChange()"></select>
  <label for="model-select">模型</label>
  <select id="model-select" onchange="onModelChange()"></select>
</div>

<div class="history-panel" id="history-panel">
  <h3>历史记录</h3>
  <div id="history-list"></div>
</div>

<div class="chat-area" id="chat-area"></div>

<div class="img-previews" id="img-previews"></div>

<div class="input-area">
  <button id="img-btn" onclick="document.getElementById('img-input').click()" title="添加图片">🖼️</button>
  <input type="file" id="img-input" multiple accept="image/png,image/jpeg,image/webp,image/gif" style="display:none" onchange="onImgFilesPicked(this)">
  <textarea id="input-box" rows="1" placeholder="输入消息（Shift / Ctrl / Alt + Enter 换行）" onkeydown="onKeyDown(event)"></textarea>
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
let pendingImgs = [];          // 待发送的图片 [{dataUrl, name}]
const IMG_MAX_COUNT = 4;
const IMG_MAX_BYTES = 5 * 1024 * 1024;  // 5MB（与后端一致）

// ── 会话槽（slot）：本窗口是哪一路对话 ──
// 主窗口 URL 带 ?slot=main，点「新窗口」后每个窗口一个 ?slot=sN。
// 同进程内各窗口的 JS 上下文天然隔离，所以并行的全部差异都在后端；
// 前端只需让每个 /api/* 请求带上自己的 slot —— 统一在 fetch 上注入，
// 比改动十几处 fetch 调用点更不容易漏。
const SLOT = new URLSearchParams(location.search).get('slot') || 'main';
const rawFetch = window.fetch.bind(window);
window.fetch = function (input, init) {
    let url = typeof input === 'string' ? input : (input && input.url) || '';
    if (url.indexOf('/api/') === 0) {
        const method = ((init && init.method) || 'GET').toUpperCase();
        if (method === 'GET' || method === 'HEAD' || method === 'DELETE') {
            url += (url.indexOf('?') === -1 ? '?' : '&') + 'slot=' + encodeURIComponent(SLOT);
        } else {
            let body = {};
            try { body = JSON.parse((init && init.body) || '{}'); } catch (err) { body = {}; }
            body.slot = SLOT;
            const headers = Object.assign({}, init && init.headers,
                                          { 'Content-Type': 'application/json' });
            init = Object.assign({}, init, { body: JSON.stringify(body), headers: headers });
        }
    }
    return rawFetch(url, init);
};


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

// ── 一键换源（来源 / 模型下拉框，运行中即时切换） ──
let configInfo = null;

function populateSourceUI(info) {
    configInfo = info;
    const srcSel = document.getElementById('source-select');
    srcSel.innerHTML = '';
    (info.sources || []).forEach(s => {
        const opt = document.createElement('option');
        opt.value = s.id;
        opt.textContent = s.label;
        if (s.id === info.source) opt.selected = true;
        srcSel.appendChild(opt);
    });
    populateModelOptions(info.source, info.model);
}

function populateModelOptions(srcId, currentModel) {
    if (!configInfo) return;
    const modelSel = document.getElementById('model-select');
    const models = configInfo.models[srcId] || [];
    const prev = modelSel.value || currentModel;
    modelSel.innerHTML = '';
    models.forEach(m => {
        const opt = document.createElement('option');
        opt.value = m;
        opt.textContent = m;
        modelSel.appendChild(opt);
    });
    if (prev && models.indexOf(prev) !== -1) modelSel.value = prev;
}

function loadConfig() {
    fetch('/api/source').then(r => r.json()).then(info => {
        if (!info.error) { populateSourceUI(info); updateImgUi(); }
    }).catch(() => {});
}

function onSourceChange() {
    if (isLoading) return;
    const src = document.getElementById('source-select').value;
    populateModelOptions(src);
    applySource(src, document.getElementById('model-select').value);
}

function onModelChange() {
    if (isLoading) return;
    applySource(document.getElementById('source-select').value, document.getElementById('model-select').value);
}

function applySource(source, model) {
    fetch('/api/source', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source: source, model: model })
    }).then(r => r.json()).then(d => {
        if (d.error) {
            showError(d.error);
            loadConfig();  // 失败时还原下拉框
        } else {
            configInfo.source = d.source;
            configInfo.model = d.model;
            updateImgUi();
            // 记住本窗口这次的选择，下次启动按槽恢复（后端 _slot_ui_state 读取）
            fetch('/api/ui-state', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ source: d.source, model: d.model })
            }).catch(() => {});
        }
    }).catch(() => {});
}

// ── 图片输入（仅识图模型） ──
function currentModelSupportsVision() {
    if (!configInfo) return false;
    // visionModels 是按来源分组的字典：{ 来源id: [模型名, ...] }
    const vs = (configInfo.visionModels || {})[configInfo.source] || [];
    return vs.indexOf(configInfo.model) !== -1;
}

function updateImgUi() {
    const btn = document.getElementById('img-btn');
    if (!btn) return;
    const support = currentModelSupportsVision();
    // 按钮常驻：不支持识图时置灰，提示如何启用
    btn.disabled = !support;
    btn.title = support
        ? '添加图片（也可直接在输入框粘贴截图）'
        : '当前模型不支持图片。切换到支持识图的模型（官方 deepseek-flash / GO deepseek-v4.1-flash / 智谱 glm-5.3-flash）后可用';
    // 切到不支持识图的模型时，清空待发图片，避免发送时报错
    if (!support && pendingImgs.length) {
        pendingImgs = [];
        renderImgPreviews();
    }
}

function onImgFilesPicked(input) {
    for (const f of input.files) addImgFile(f);
    input.value = '';
}

function addImgFile(file) {
    if (!file.type.startsWith('image/')) { alert('仅支持图片文件（PNG/JPG/WebP/GIF）'); return; }
    if (pendingImgs.length >= IMG_MAX_COUNT) { alert('每条消息最多 ' + IMG_MAX_COUNT + ' 张图片'); return; }
    const reader = new FileReader();
    reader.onload = () => {
        const dataUrl = reader.result;
        // dataURL 长度 × 0.75 ≈ 原始字节数（base64 编码）
        if (dataUrl.length * 0.75 > IMG_MAX_BYTES) { alert('图片过大（最大 5MB）: ' + file.name); return; }
        pendingImgs.push({ dataUrl: dataUrl, name: file.name });
        renderImgPreviews();
    };
    reader.readAsDataURL(file);
}

function renderImgPreviews() {
    const wrap = document.getElementById('img-previews');
    if (!wrap) return;
    wrap.innerHTML = '';
    pendingImgs.forEach((img, i) => {
        const div = document.createElement('div');
        div.className = 'img-thumb';
        div.innerHTML = '<img src="' + img.dataUrl + '"><span class="img-remove" title="移除">✕</span>';
        div.querySelector('.img-remove').onclick = () => { pendingImgs.splice(i, 1); renderImgPreviews(); };
        wrap.appendChild(div);
    });
    wrap.classList.toggle('has-imgs', pendingImgs.length > 0);
}

loadConfig();

// ── 发送消息 ──
function sendMessage() {
    const text = inputBox.value.trim();
    const hasImgs = pendingImgs.length > 0;
    if ((!text && !hasImgs) || isLoading) return;
    if (hasImgs && !currentModelSupportsVision()) {
        showError('当前模型不支持图片输入，请切换到支持识图的模型（如官方 deepseek-flash、opencode GO 的 deepseek-v4.1-flash）后再发送图片。');
        return;
    }
    inputBox.value = '';
    inputBox.style.height = 'auto';
    isLoading = true;
    sendBtn.disabled = true;
    stopBtn.style.display = 'inline-block';
    reasoningText = '';
    cancelled = false;
    currentController = new AbortController();
    ensureAudio();  // 用户手势内创建/恢复音频上下文，保证后续能出声

    // 显示用户消息（含完整日期时间 + 图片缩略图）
    const now = new Date().toLocaleString();
    let fullContent = '';
    const imgs = pendingImgs.slice();
    pendingImgs = [];
    renderImgPreviews();
    addUserMessage(text, imgs, now);

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
        body: JSON.stringify({ message: text, images: imgs.map(i => i.dataUrl) }),
        signal: currentController.signal
    }).then(response => {
        if (!response.ok) {
            return response.json().then(d => { throw new Error(d.error || '请求失败'); });
        }
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

// ── 添加用户消息（含图片缩略图）──
function addUserMessage(text, imgs, time) {
    const div = document.createElement('div');
    div.className = 'msg user';
    let imgsHtml = '';
    if (imgs && imgs.length) {
        imgsHtml = '<div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:6px;">' +
            imgs.map(i => '<img src="' + i.dataUrl + '" style="max-width:160px;max-height:160px;border-radius:8px;">').join('') +
            '</div>';
    }
    div.innerHTML = '<div class="msg-header"><div class="msg-avatar">👤</div><span class="msg-role">我</span><span class="msg-time">' + (time || '') + '</span></div><div class="msg-body">' + imgsHtml + (text ? markedToHtml(text) : '') + '</div>';
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
    // 输入法组合中（如拼音候选确认的 Enter）不触发发送
    if (e.isComposing || e.keyCode === 229) return;
    if (e.key === 'Enter') {
        if (e.shiftKey || e.ctrlKey || e.altKey) {
            // 组合键 = 换行：显式插入（部分内嵌环境不触发 textarea 默认行为，
            // preventDefault + 手动插入保证各环境行为一致、不重复换行）
            e.preventDefault();
            const s = inputBox.selectionStart;
            const epos = inputBox.selectionEnd;
            inputBox.value = inputBox.value.slice(0, s) + '\n' + inputBox.value.slice(epos);
            inputBox.selectionStart = inputBox.selectionEnd = s + 1;
        } else {
            e.preventDefault();
            sendMessage();
        }
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

// 再开一个独立对话窗口：同进程、同 Flask，各自一份上下文，可以同时各跑一条回答。
// 只有用户点击才调用（新窗口加载的是同一个页面，自动调用会无限开下去）。
function newWindow() {
    fetch('/api/new-window', { method: 'POST' })
        .then(r => r.json())
        .then(d => { if (d.error) showError(d.error); })
        .catch(() => showError('新建窗口失败'));
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

// ── 外链统一走系统默认浏览器 ──
// 点击聊天区（实时回复/历史记录）中的 http(s) 链接时，
// 阻止窗口内导航，交给后端 webbrowser.open 用默认浏览器打开。
// 事件委托挂在 chatArea 上：innerHTML 替换（新对话/加载历史）不影响监听。
chatArea.addEventListener('click', (e) => {
    const a = e.target.closest && e.target.closest('a[href]');
    if (!a) return;
    const href = a.href || '';
    if (!/^https?:\/\//i.test(href)) return;  // 锚点等留在页内
    e.preventDefault();
    fetch('/api/open-external', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: href })
    }).catch(() => {});
});

// 粘贴图片（截图等）：仅在当前模型支持识图时接受
inputBox.addEventListener('paste', (e) => {
    if (!currentModelSupportsVision()) return;
    const items = (e.clipboardData || {}).items || [];
    for (const item of items) {
        if (item.type && item.type.startsWith('image/')) {
            const f = item.getAsFile();
            if (f) addImgFile(f);
        }
    }
});

// ── 页面缩放（Ctrl+滚轮 / Ctrl+= / Ctrl+- / Ctrl+0，接近浏览器网页缩放）──
// 用 CSS zoom 整页等比缩放（WebView2/Chromium 支持，布局随字号一起缩放）。
// 比例双通道持久化：后端 ui_state.json 跨端口稳定（localStorage 按端口隔离，
// 端口漂移会丢），localStorage 仅作即时回退。无记录时默认 100%。
const ZOOM_MIN = 0.5, ZOOM_MAX = 2.0, ZOOM_STEP = 0.1;
let pageZoom = 1.0;
let zoomTipTimer = null;

function clampZoom(z) {
    return Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Math.round(z * 10) / 10));
}

function persistPageZoom() {
    try { localStorage.setItem('pageZoom', String(pageZoom)); } catch (err) {}
    fetch('/api/ui-state', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pageZoom: pageZoom })
    }).catch(() => {});
}

function applyPageZoom(z, showTip) {
    pageZoom = clampZoom(z);
    document.body.style.zoom = pageZoom === 1 ? '' : String(pageZoom);
    persistPageZoom();
    if (showTip) showZoomTip();
}

function showZoomTip() {
    let tip = document.getElementById('zoom-tip');
    if (!tip) {
        tip = document.createElement('div');
        tip.id = 'zoom-tip';
        tip.style.cssText = 'position:fixed;top:52px;left:50%;transform:translateX(-50%);z-index:200;'
            + 'background:rgba(26,26,46,0.75);color:#fff;padding:4px 14px;border-radius:14px;'
            + 'font-size:13px;pointer-events:none;opacity:0;transition:opacity .25s;';
        document.body.appendChild(tip);
    }
    tip.textContent = Math.round(pageZoom * 100) + '%';
    tip.style.opacity = '1';
    clearTimeout(zoomTipTimer);
    zoomTipTimer = setTimeout(() => { tip.style.opacity = '0'; }, 900);
}

window.addEventListener('wheel', (e) => {
    if (!e.ctrlKey) return;
    e.preventDefault();
    applyPageZoom(pageZoom + (e.deltaY < 0 ? ZOOM_STEP : -ZOOM_STEP), true);
}, { passive: false });

window.addEventListener('keydown', (e) => {
    if (!e.ctrlKey || e.shiftKey || e.altKey) return;
    if (e.key === '=' || e.key === '+') {
        e.preventDefault();
        applyPageZoom(pageZoom + ZOOM_STEP, true);
    } else if (e.key === '-') {
        e.preventDefault();
        applyPageZoom(pageZoom - ZOOM_STEP, true);
    } else if (e.key === '0') {
        e.preventDefault();
        applyPageZoom(1.0, true);
    }
});

// 启动恢复上次缩放：先用 localStorage 预应用（避免闪烁，不落盘），
// 随后以后端记录为准（后端无记录则沿用本地值/默认 100%）
pageZoom = clampZoom(parseFloat(localStorage.getItem('pageZoom')) || 1.0);
document.body.style.zoom = pageZoom === 1 ? '' : String(pageZoom);
fetch('/api/ui-state').then(r => r.json()).then(s => {
    const saved = s && s.pageZoom !== undefined ? parseFloat(s.pageZoom) : NaN;
    applyPageZoom(isNaN(saved) ? pageZoom : saved, false);
}).catch(() => {});
</script>
</body>
</html>
""".replace("$TITLE", APP_TITLE)

# ──────────────────────────────────────────────────────────
# Flask 应用
# ──────────────────────────────────────────────────────────

app = Flask(__name__, static_folder=os.path.join(PROJECT_ROOT, "static"))

# UI 状态持久化文件（如窗口缩放比例）。放用户本地、不入 Git。
UI_STATE_FILE = os.path.join(PROJECT_ROOT, "ui_state.json")

# ──────────────────────────────────────────────────────────
# 会话槽（slot）：一个窗口 = 一个 slot = 一份独立上下文
#
# 并行能力全靠这一层：每个 slot 有自己的 ChatSession（core 层本就是实例级
# 状态：messages / 取消标记 / client）、自己的锁、自己的历史文件名。
#
# 锁的用法是这套设计的关键：slot.lock 只在"改结构"的瞬间持有（建会话、
# 换源、占位生成权），绝不横跨整个流式期间。生成期间用 slot.busy 标记
# 挡住同槽的第二次请求，从而既保住"一个槽同时只有一条回答"的上下文完整性，
# 又让 /api/cancel 能立刻拿到锁（旧版全程持锁，取消请求会排队到回答结束）。
# ──────────────────────────────────────────────────────────

DEFAULT_SLOT = "main"
"""不带 slot 参数时的槽 id（主窗口；也是改造前的唯一会话）"""


class Slot:
    """一个独立对话槽。"""

    __slots__ = ("id", "lock", "session", "history_file", "window", "busy")

    def __init__(self, slot_id: str):
        self.id = slot_id
        self.lock = threading.Lock()
        self.session = None
        self.history_file = None  # 本会话对应的历史文件（同会话内覆盖，避免重复副本）
        self.window = None
        self.busy = False         # 该槽是否有一条回答正在生成


SLOTS = {}
slots_guard = threading.Lock()   # 只保护 SLOTS 字典结构，不横跨流期
_windows_guard = threading.Lock()  # 子槽编号分配
_slot_seq = 0

_APP_HOME = ""                   # launch() 写入，运行中新开窗口拼 URL 用
_SCREEN = (1920, 1080)           # launch() 写入，新窗口级联位置钳制用
_CASCADE = [0, 0]                # 运行中新开窗口的位置游标（launch 里对齐主窗口）


def _slot_list() -> list:
    """当前所有槽（快照，避免调用方遍历时改字典）。"""
    with slots_guard:
        return list(SLOTS.values())


def get_slot(slot_id: str = None) -> Slot:
    """取（或惰性建）一个会话槽，保证 session 已就绪。"""
    slot_id = slot_id or DEFAULT_SLOT
    with slots_guard:
        slot = SLOTS.get(slot_id)
        if slot is None:
            slot = SLOTS[slot_id] = Slot(slot_id)
    with slot.lock:
        if slot.session is None:
            _prepare_slot(slot)
    return slot


def _next_slot_id() -> str:
    """分配下一个子槽编号（s2、s3…）。

    编号只增不复用：关掉 s2 再开新窗口得到 s3，避免新窗口接上旧 s2 的上下文。
    """
    global _slot_seq
    with _windows_guard:
        _slot_seq += 1
        return "s%d" % (_slot_seq + 1)


def _cascade_pos() -> tuple:
    """运行中新开窗口的级联位置：每个往右下挪 36px，避免完全重叠看不出开了几个。"""
    step = 36
    with _windows_guard:
        _CASCADE[0] = min(_CASCADE[0] + step, max(0, _SCREEN[0] - 320))
        _CASCADE[1] = min(_CASCADE[1] + step, max(0, _SCREEN[1] - 200))
        return _CASCADE[0], _CASCADE[1]


def _slot_title(slot_id: str) -> str:
    if slot_id == DEFAULT_SLOT:
        return APP_TITLE
    return "%s · 会话%s" % (APP_TITLE, slot_id.lstrip("s") or "1")


def _load_ui_state() -> dict:
    """读取本地 UI 状态（损坏/缺失时返回空 dict，各项走默认值）。"""
    try:
        with open(UI_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _slot_ui_state(slot_id: str) -> dict:
    """该槽上次用的来源/模型。

    多窗口必须各记各的，否则第二个窗口启动时会被拉回第一个窗口的来源。
    老配置只有扁平 source/model（单会话时代），视作主槽的记录兼容读取。
    """
    state = _load_ui_state()
    per_slot = state.get("slots")
    if isinstance(per_slot, dict) and isinstance(per_slot.get(slot_id), dict):
        return per_slot[slot_id]
    if slot_id == DEFAULT_SLOT:
        return {k: state.get(k) for k in ("source", "model")}
    return {}


def _save_slot(slot: Slot):
    """将该槽的会话保存为 HTML 文件（同一会话覆盖同一文件）。"""
    if slot.session is None:
        return
    try:
        messages = slot.session.get_messages()
        if len(messages) < 2:  # 至少要有来回
            return
        # 渲染消息为简单 HTML（不含页面框架）
        chat_html = _render_messages_html(messages)
        if slot.history_file and os.path.isfile(slot.history_file):
            # 同一会话：覆盖原文件，避免历史目录堆积重复副本
            overwrite_conversation_html(slot.history_file, messages, chat_html)
        else:
            slot.history_file = save_conversation_html(messages, HISTORY_DIR, chat_html)
    except Exception:
        logger.exception("保存会话失败")


def _save_all_slots():
    """兜底保存（窗口关闭后调用）。"""
    for slot in _slot_list():
        _save_slot(slot)


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


def _session_tools() -> list:
    """按全局开关筛选工具列表。

    ENABLE_SEARCH=False 时移除联网工具，只保留本地文件工具；
    这样模型不会看到它无法使用的工具。
    """
    if ENABLE_SEARCH:
        return TOOLS
    return [
        t for t in TOOLS
        if t["function"]["name"] not in ("web_search", "fetch_webpage")
    ]


def _restore_target(state: dict):
    """从 UI 状态解析应恢复的 (source, model)。

    - source 无记录/已不存在 → None（保持默认来源）
    - model 无记录/已不在该来源模型清单 → 用该来源的 default_model
    - 之后 configure() 若因 Key 缺失失败，由调用方整体回退默认
    """
    src = state.get("source")
    if not src or src not in SOURCES:
        return None
    info = SOURCES[src]
    model = state.get("model")
    if model not in info["models"]:
        model = info["default_model"]
    return src, model


def _prepare_slot(slot: Slot):
    """为该槽建 ChatSession 并恢复它上次的来源/模型。调用方须持有 slot.lock。"""
    slot.history_file = None
    slot.session = ChatSession(
        system_prompt=build_system_prompt(enable_search=ENABLE_SEARCH),
        tools=_session_tools(),
        tool_executor=execute_tool,
    )

    # 恢复上次使用的来源/模型（记录缺失/失效/Key 缺失时静默回退默认）
    target = _restore_target(_slot_ui_state(slot.id))
    if target:
        try:
            slot.session.configure(source=target[0], model=target[1])
        except ValueError as e:
            logger.info("恢复上次来源/模型失败，使用默认: %s", e)


def _reset_slot(slot: Slot) -> bool:
    """该槽开始新对话：先落盘旧会话再重建。生成中不允许重置（返回 False）。"""
    with slot.lock:
        if slot.busy:
            return False
        _save_slot(slot)
        _prepare_slot(slot)
        return True


@app.route("/")
def index():
    return CHAT_HTML


@app.route("/api/source")
def api_source():
    """读取该窗口（slot）当前的来源/模型配置（页面加载时填充下拉框）。"""
    slot = get_slot(request.args.get("slot"))
    with slot.lock:
        source = slot.session.source
        model = slot.session.model
    sources = [
        {"id": sid, "label": info["label"]}
        for sid, info in SOURCES.items()
    ]
    models = {sid: info["models"] for sid, info in SOURCES.items()}
    vision_models = {sid: info.get("vision_models", []) for sid, info in SOURCES.items()}
    return jsonify({
        "source": source, "model": model,
        "sources": sources, "models": models,
        "visionModels": vision_models,
        "slot": slot.id,
    })


@app.route("/api/source", methods=["POST"])
def api_source_update():
    """运行中切换该窗口（slot）的来源/模型（一键换源，各窗口互不影响）。"""
    data = request.get_json(silent=True) or {}
    source = data.get("source")
    model = data.get("model")
    slot = get_slot(data.get("slot"))
    with slot.lock:
        if slot.busy:
            return jsonify({"error": "该窗口正在生成回答，请先停止或等待结束再换源"}), 409
        try:
            slot.session.configure(source=source, model=model)
            # 联网工具由本地 Function Calling 提供，与 API 来源无关，
            # 因此切源时提示词保持统一（只按 ENABLE_SEARCH 开关变化）
            slot.session.messages[0]["content"] = build_system_prompt(
                enable_search=ENABLE_SEARCH
            )
        except Exception as e:
            logger.warning("切换来源失败: %s", e)
            return jsonify({"error": str(e)}), 400
        session = slot.session
    return jsonify({
        "ok": True,
        "source": session.source,
        "model": session.model,
        "supports_vision": session.supports_vision,
        "slot": slot.id,
    })


@app.route("/api/chat", methods=["POST"])
def api_chat():
    """SSE 流式聊天接口（按 slot 定位会话；不同窗口可同时各跑一条）。

    请求体：{"message": str, "images": [dataURL, ...], "slot": str}
    images 为可选的 OpenAI vision 图片列表（data:image/...;base64,），
    仅当前模型支持识图时接受（见 ChatSession.supports_vision）。
    """
    data = request.get_json(silent=True)
    if not data or "message" not in data:
        return jsonify({"error": "缺少 message 字段"}), 400

    message = data["message"]
    images = data.get("images") or []
    slot = get_slot(data.get("slot"))

    with slot.lock:
        if slot.busy:
            # 一个窗口同时只允许一条回答（保证上下文不被并发写坏）；
            # 别的窗口不受影响，这就是"多个对话并行"的实现点。
            return jsonify({"error": "该窗口已有一条回答正在生成，请等待完成或先点「停止」"}), 409
        session = slot.session
        if images:
            # 图片校验：模型支持 / 数量 / 格式 / 大小
            if not session.supports_vision:
                return jsonify({"error": (
                    f"当前模型 {session.model} 不支持图片输入。"
                    f"请切换到支持识图的模型（如官方 deepseek-flash、opencode GO 的 deepseek-v4.1-flash）后再发送图片。"
                )}), 400
            if len(images) > IMAGE_MAX_COUNT:
                return jsonify({"error": f"每条消息最多 {IMAGE_MAX_COUNT} 张图片"}), 400
            for raw in images:
                m = _IMAGE_DATA_URL_RE.match(raw)
                if not m:
                    return jsonify({"error": "图片格式无效，仅支持 PNG/JPG/WebP/GIF 的 base64 data URL"}), 400
                if len(m.group(2)) > (IMAGE_MAX_BYTES * 4) // 3:
                    return jsonify({"error": f"单张图片不能超过 {IMAGE_MAX_BYTES // (1024 * 1024)}MB"}), 400
        slot.busy = True  # 占用生成权；流结束时在 generate() 的 finally 释放

    def build_content():
        """组 OpenAI vision 多模态 content（文本 + 图片）。"""
        if not images:
            return message
        # 部分网关要求 image 消息必须带 text part，空文本时补占位
        parts = [{"type": "text", "text": message if message else "（图片）"}]
        for img in images:
            parts.append({"type": "image_url", "image_url": {"url": img}})
        return parts

    def generate():
        # 注意：这里不持有 slot.lock —— 整段流式期间持锁会让 /api/cancel 和本槽
        # 后续请求排队到回答结束（旧版如此）。生成权由上面的 slot.busy 标记独占。
        try:
            full_content = ""
            reasoning_active = False
            for chunk in session.ask_stream(build_content()):
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
            _save_slot(slot)

        except Exception as e:
            logger.exception("聊天接口异常")
            yield f"data: {json.dumps({'type': 'error', 'text': str(e)})}\n\n"
        finally:
            # 客户端 abort / 异常 / 正常结束都会走到这里，生成权必须归还
            with slot.lock:
                slot.busy = False

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.route("/api/ui-state")
def api_ui_state_get():
    """读取本地 UI 状态（页面加载时恢复上次的窗口设置，如缩放比例）。"""
    return jsonify(_load_ui_state())


@app.route("/api/ui-state", methods=["POST"])
def api_ui_state_post():
    """保存 UI 状态（前端缩放/换源时即时上报，合入已有状态防止互相覆盖）。

    pageZoom 全局共享；source/model 按 slot 分键 —— 多窗口各自记自己的来源，
    否则下次启动会被别的窗口的选择带跑。
    """
    data = request.get_json(silent=True) or {}
    slot_id = data.get("slot") or DEFAULT_SLOT
    state = _load_ui_state()
    if "pageZoom" in data:
        try:
            z = float(data["pageZoom"])
            if 0.5 <= z <= 2.0:
                state["pageZoom"] = round(z, 2)
        except (TypeError, ValueError):
            pass  # 非法值忽略，保持原记录
    for key in ("source", "model"):
        if isinstance(data.get(key), str):
            slots = state.setdefault("slots", {})
            slots.setdefault(slot_id, {})[key] = data[key]
    # 其余字段原样合入（pageZoom / 按槽存取的键 / slot 之外不做校验）
    for k, v in data.items():
        if k in ("pageZoom", "slot", "source", "model") or not isinstance(k, str):
            continue
        state[k] = v
    try:
        with open(UI_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
    except OSError as e:
        logger.warning("保存 UI 状态失败: %s", e)
        return jsonify({"error": str(e)}), 500
    return jsonify({"ok": True, "state": state})


@app.route("/api/cancel", methods=["POST"])
def api_cancel():
    """取消该窗口正在进行的生成（用户点"停止"，只停自己那条流）。"""
    data = request.get_json(silent=True) or {}
    slot = get_slot(data.get("slot"))
    with slot.lock:
        session = slot.session
    if session is not None:
        session.cancel()
    return jsonify({"ok": True, "slot": slot.id})


@app.route("/api/new-window", methods=["POST"])
def api_new_window():
    """在同进程内再开一个独立对话窗口（新会话槽，上下文互不相干）。

    新槽的窗口标题带「会话N」以便在任务栏/Alt+Tab 区分；来源/模型跟随发起方，
    省得每个新窗口都要重新选一次。上限 MAX_WINDOWS 是因为 WebView2 对同一
    host 的并发连接数有限（每窗口一路 SSE 长连接）。

    只能由用户点击触发：新窗口加载的是同一个页面，若前端改成加载时自动调用
    本接口，会无限开下去。
    """
    data = request.get_json(silent=True) or {}
    template = get_slot(data.get("slot"))
    if len(webview.windows) >= MAX_WINDOWS:
        return jsonify({"error": f"最多同时打开 {MAX_WINDOWS} 个窗口"}), 409

    slot_id = _next_slot_id()
    slot = get_slot(slot_id)
    with template.lock:
        source, model = template.session.source, template.session.model
    with slot.lock:
        try:
            slot.session.configure(source=source, model=model)
        except Exception as e:
            logger.info("新窗口沿用 %s/%s 失败，保持默认: %s", source, model, e)

    try:
        x, y = _cascade_pos()
        _open_slot_window(slot_id, x=x, y=y)
    except Exception as e:
        logger.exception("新建窗口失败")
        return jsonify({"error": f"新建窗口失败: {e}"}), 500
    return jsonify({"ok": True, "slot": slot_id, "windows": len(webview.windows)})


@app.route("/api/open-external", methods=["POST"])
def api_open_external():
    """用系统默认浏览器打开外部链接。

    聊天区的外链（AI 回复/历史记录中的 URL）禁止在应用窗口内导航——
    WebView2 窗口一旦跳到外部页面（或加载失败出现错误页），整个 UI 就丢了。
    仅允许 http/https，防止 file:// 等协议被注入。
    """
    data = request.get_json(silent=True) or {}
    url = str(data.get("url") or "").strip()
    if not re.match(r"^https?://[^\s]+$", url, re.IGNORECASE):
        return jsonify({"error": "仅支持 http/https 链接"}), 400
    try:
        webbrowser.open(url)
        return jsonify({"ok": True})
    except Exception as e:
        logger.warning("打开外部链接失败: %s", e)
        return jsonify({"error": str(e)}), 500


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
    """该窗口开始新对话（先落盘旧会话，再生成新会话槽）。"""
    data = request.get_json(silent=True) or {}
    slot = get_slot(data.get("slot"))
    if not _reset_slot(slot):
        return jsonify({"error": "该窗口正在生成回答，请先停止或等待结束"}), 409
    return jsonify({"ok": True, "slot": slot.id})


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
    """在该窗口（slot）恢复历史会话上下文，之后可继续提问。"""
    data = request.get_json(silent=True)
    if not data or "messages" not in data:
        return jsonify({"error": "缺少 messages 字段"}), 400

    slot = get_slot(data.get("slot"))
    with slot.lock:
        if slot.busy:
            return jsonify({"error": "该窗口正在生成回答，请先停止或等待结束"}), 409
        # 保留 reasoning_content（DeepSeek 推理模型要求回传）
        hist = []
        for m in data["messages"]:
            if m.get("role") in ("user", "assistant"):
                entry = {"role": m["role"], "content": m.get("content", "")}
                if m.get("reasoning_content"):
                    entry["reasoning_content"] = m["reasoning_content"]
                hist.append(entry)
        slot.session.restore(hist)
        # 恢复后继续对话时，覆盖保存到同一个历史文件
        filename = data.get("filename") or ""
        restore_path = os.path.join(HISTORY_DIR, filename) if filename else ""
        slot.history_file = restore_path if restore_path and os.path.isfile(restore_path) else None
    return jsonify({"ok": True, "slot": slot.id})


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


def _open_slot_window(slot_id: str, x: int = None, y: int = None):
    """创建一个绑定到会话槽的窗口（主窗口与运行中新开的窗口共用）。

    从主线程调用时（launch）只登记窗口，真正建窗由 webview.start() 完成；
    从其它线程调用时（/api/new-window 的 Flask 请求线程）pywebview 会把窗口
    Invoke 到 GUI 线程立即创建 —— 本机实测约 15ms，且因为 URL 是 http 开头的
    本地地址，pywebview 不会再为它拉起自带的 Bottle server。
    """
    global _APP_HOME
    width = min(WINDOW_WIDTH, max(600, _SCREEN[0] - (x or 0)))
    height = min(WINDOW_HEIGHT, max(400, _SCREEN[1] - (y or 0)))
    window = webview.create_window(
        _slot_title(slot_id),
        f"{_APP_HOME}?slot={slot_id}",
        width=width,
        height=height,
        x=x,
        y=y,
        min_size=(600, 400),
        resizable=True,
        text_select=True,    # 允许用户在聊天区选中文本
    )
    if window is None:
        return None
    get_slot(slot_id).window = window

    # 兜底：窗口 URL 一旦离开应用页面（外链导航、错误页等），自动拉回。
    # 正常情况下外链已由前端拦截 + OPEN_EXTERNAL_LINKS_IN_BROWSER 双保险处理，
    # 此监听只防其他路径的"窗口跑飞"（如拖拽链接进窗口）。
    app_home = _APP_HOME

    def _bring_back_to_app():
        try:
            url = window.get_current_url() or ""
            if not url.startswith(app_home):
                logger.warning("窗口离开了应用页面（%s），自动拉回", url[:120])
                window.load_url(app_home)
        except Exception:
            pass

    window.events.loaded += _bring_back_to_app
    return window


def launch(port=5000):
    """启动 Flask 服务器 + pywebview 主窗口 —— 主入口。

    启动时序：
      1. get_slot('main')      → 初始化主会话槽（含 system prompt）
      2. run_server(daemon)     → Flask 在后台线程启动
      3. _wait_server_ready()   → 轮询等待 Flask 就绪
      4. _open_slot_window      → 创建主窗口，加载 http://127.0.0.1:port/?slot=main
      5. webview.start()        → 事件循环（阻塞，直到所有窗口关闭）
      6. _save_all_slots()      → 退出前兜底保存

    默认只开一个窗口。要多开时点界面上的「新窗口」按钮：同进程内再加一个会话槽
    （独立上下文 + 独立流，可与其它窗口同时生成），不用再 --port 起第二个进程。

    参数：
      port: Flask 监听端口（所有窗口共用这一个服务）。
    """
    global _APP_HOME, _SCREEN, _CASCADE

    # 1. 初始化主会话槽（含 system prompt、工具注册、上次来源/模型）
    get_slot(DEFAULT_SLOT)

    # 2. 启动 Flask 服务器（daemon 线程，主线程退出时自动结束）
    #    端口被占用时自动挑选空闲端口，避免启动失败
    port = _find_free_port(port)
    _APP_HOME = f"http://127.0.0.1:{port}/"
    server_thread = threading.Thread(target=run_server, args=(port,), daemon=True)
    server_thread.start()
    if not _wait_server_ready(port):
        logger.warning("Flask 服务器未能及时就绪，继续等待窗口加载...")

    # 3. 打开主窗口（居中）；位置游标对齐主窗口，后续新窗口从它往右下级联
    # target="_blank" 的链接（如 AI 回复中的外链）交给系统默认浏览器，不在窗口内开新页
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    root = tk.Tk()
    root.withdraw()  # 隐藏临时窗口
    screen_w = root.winfo_screenwidth()
    screen_h = root.winfo_screenheight()
    root.destroy()
    _SCREEN = (screen_w, screen_h)
    win_x = max(0, (screen_w - WINDOW_WIDTH) // 2)
    win_y = max(0, (screen_h - WINDOW_HEIGHT) // 2)
    _CASCADE = [win_x, win_y]
    _open_slot_window(DEFAULT_SLOT, x=win_x, y=win_y)

    # private_mode=False 表示使用用户默认浏览器数据（Cookie、缓存等）
    # icon 参数（Windows WinForms 后端）：自定义窗口/任务栏图标，仅支持 .ico
    icon_path = os.path.join(PROJECT_ROOT, "icon.ico")
    if not os.path.isfile(icon_path):
        icon_path = None  # 图标缺失时退回默认图标
    webview.start(private_mode=False, icon=icon_path)

    # 4. 所有窗口关闭后兜底保存（每轮回答结束时已按槽自动保存过）
    _save_all_slots()


if __name__ == "__main__":
    launch()
