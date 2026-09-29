"""多会话槽（slot）隔离测试：不同窗口互不干扰、同一窗口不并发、取消/换源/落盘按槽定向。"""

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# 测试环境不要求真实密钥，但 ChatSession 构造 OpenAI 客户端时必须有值
os.environ.setdefault("DEEPSEEK_API_KEY", "sk-test-dummy")
os.environ.setdefault("OPENCODE_GO_API_KEY", "sk-go-dummy")
os.environ.setdefault("ZBIGMODEL_API_KEY", "zbig-test-dummy.dummy")
os.environ.setdefault("BAILIAN_TOKEN_PLAN_API_KEY", "sk-sp-test-dummy")
os.environ.setdefault("STEPFUN_API_KEY", "test-dummy-stepfun-key")
os.environ.setdefault("XIAOMI_MIMO_API_KEY", "sk-test-dummy-mimo")

import ui.webview_app as wa


def fake_stream(self, user_input):
    """ask_stream 替身：记录所属会话收到了什么，并产出一段固定文本。

    真实实现会先调用 _outgoing_messages() 打网络请求，这里只保留"往自己的
    messages 里追加用户消息"这一副作用，用来验证上下文是否串槽。
    """
    seen = getattr(self, "seen_inputs", None)
    if seen is None:
        seen = self.seen_inputs = []
    seen.append(user_input)
    self.messages.append({"role": "user", "content": user_input})
    yield "回声：" + str(user_input)


STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_tmp_slots_state.json")


def sse_events(payload):
    """解 SSE 响应体为 [(type, 文本), ...]。

    服务端 json.dumps 会把中文转义成 \\uXXXX，所以必须解码后再比对原文。
    """
    events = []
    for line in payload.splitlines():
        if not line.startswith("data: ") or line.strip() == "data: [DONE]":
            continue
        event = json.loads(line[6:])
        events.append((event.get("type"), event.get("text") or event.get("html") or ""))
    return events



class SlotTestBase(unittest.TestCase):
    def setUp(self):
        wa.SLOTS.clear()
        wa._slot_seq = 0
        wa._CASCADE[:] = [0, 0]
        for path in (STATE_FILE,):
            if os.path.exists(path):
                os.remove(path)
        patches = [
            mock.patch.object(wa.ChatSession, "ask_stream", fake_stream),
            mock.patch.object(wa, "_save_slot", lambda slot: None),   # 测试不写历史目录
            mock.patch.object(wa, "UI_STATE_FILE", STATE_FILE),
        ]
        for p in patches:
            p.start()
        self.addCleanup(mock.patch.stopall)
        self.addCleanup(lambda: os.path.exists(STATE_FILE) and os.remove(STATE_FILE))

    def client(self):
        return wa.app.test_client()

    def user_texts(self, slot_id):
        session = wa.get_slot(slot_id).session
        return [m.get("content") for m in session.messages if m.get("role") == "user"]


class SlotIsolationTest(SlotTestBase):

    def test_each_slot_has_its_own_context(self):
        c = self.client()
        r1 = sse_events(c.post("/api/chat", json={"message": "甲的问题", "slot": "main"})
                        .get_data(as_text=True))
        r2 = sse_events(c.post("/api/chat", json={"message": "乙的问题", "slot": "s2"})
                        .get_data(as_text=True))
        self.assertTrue(any("甲的问题" in text for _, text in r1), r1)
        self.assertTrue(any("乙的问题" in text for _, text in r2), r2)

        main, s2 = wa.get_slot("main"), wa.get_slot("s2")
        self.assertIsNot(main.session, s2.session)
        self.assertEqual(getattr(main.session, "seen_inputs", []), ["甲的问题"])
        self.assertEqual(getattr(s2.session, "seen_inputs", []), ["乙的问题"])
        self.assertEqual(self.user_texts("main"), ["甲的问题"])
        self.assertEqual(self.user_texts("s2"), ["乙的问题"])

    def test_missing_slot_falls_back_to_default(self):
        resp = self.client().post("/api/chat", json={"message": "不带槽"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.user_texts(wa.DEFAULT_SLOT), ["不带槽"])

    def test_same_slot_is_single_flight(self):
        main = wa.get_slot("main")
        main.busy = True
        resp = self.client().post("/api/chat", json={"message": "x", "slot": "main"})
        self.assertEqual(resp.status_code, 409)
        self.assertIn("该窗口", resp.get_json()["error"])
        # 别的窗口照常并行
        other = self.client().post("/api/chat", json={"message": "y", "slot": "s7"})
        self.assertEqual(other.status_code, 200)

    def test_busy_is_released_when_stream_ends(self):
        self.client().post("/api/chat", json={"message": "x", "slot": "main"})
        self.assertFalse(wa.get_slot("main").busy)

    def test_cancel_only_targets_its_slot(self):
        main, s2 = wa.get_slot("main"), wa.get_slot("s2")
        resp = self.client().post("/api/cancel", json={"slot": "s2"})
        self.assertEqual(resp.get_json()["slot"], "s2")
        self.assertTrue(s2.session._cancel_event.is_set())
        self.assertFalse(main.session._cancel_event.is_set())

    def test_cancel_returns_while_a_stream_is_running(self):
        # 旧版整段流期持全局锁，取消请求会排队到回答结束；现在必须能立刻返回
        main = wa.get_slot("main")
        main.busy = True
        self.assertEqual(self.client().post("/api/cancel", json={"slot": "main"}).status_code, 200)

    def test_configure_is_per_slot(self):
        self.client().post("/api/source", json={
            "slot": "s2", "source": "zbigmodel", "model": "glm-5.3-flash"})
        self.assertEqual(wa.get_slot("s2").session.source, "zbigmodel")
        self.assertEqual(wa.get_slot("s2").session.model, "glm-5.3-flash")
        self.assertEqual(wa.get_slot("main").session.source, wa.SOURCE)
        self.assertNotEqual(wa.get_slot("main").session.model, "glm-5.3-flash")

    def test_reset_only_touches_its_slot(self):
        c = self.client()
        c.post("/api/chat", json={"message": "甲", "slot": "main"})
        c.post("/api/chat", json={"message": "乙", "slot": "s2"})
        old_main = wa.get_slot("main").session

        self.assertEqual(c.post("/api/reset", json={"slot": "main"}).status_code, 200)
        self.assertIsNot(wa.get_slot("main").session, old_main)
        self.assertEqual(self.user_texts("main"), [])
        self.assertEqual(self.user_texts("s2"), ["乙"])

    def test_reset_blocked_while_generating(self):
        main = wa.get_slot("main")
        main.busy = True
        resp = self.client().post("/api/reset", json={"slot": "main"})
        self.assertEqual(resp.status_code, 409)


class UiStatePerSlotTest(SlotTestBase):

    def test_source_is_stored_per_slot(self):
        self.client().post("/api/ui-state", json={
            "slot": "s2", "source": "zbigmodel", "model": "glm-5.3-flash"})
        with open(STATE_FILE, encoding="utf-8") as f:
            state = json.load(f)
        self.assertEqual(state["slots"]["s2"]["source"], "zbigmodel")
        self.assertNotIn("main", state["slots"])          # 没动别的槽
        self.assertNotIn("source", state)                  # 不再写扁平旧键
        self.assertNotIn("slot", state)                    # 请求里的定位字段不落盘

    def test_page_zoom_stays_global(self):
        self.client().post("/api/ui-state", json={"slot": "s2", "pageZoom": 1.25})
        with open(STATE_FILE, encoding="utf-8") as f:
            state = json.load(f)
        self.assertEqual(state["pageZoom"], 1.25)

    def test_new_slot_restores_its_own_recorded_source(self):
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"slots": {"s3": {"source": "zbigmodel", "model": "glm-5.3-flash"}}}, f)
        slot = wa.get_slot("s3")
        self.assertEqual((slot.session.source, slot.session.model),
                         ("zbigmodel", "glm-5.3-flash"))
        # 主槽没有记录 → 用 config 默认
        self.assertEqual(wa.get_slot("main").session.source, wa.SOURCE)

    def test_legacy_flat_state_maps_to_default_slot(self):
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"source": "zbigmodel", "model": "glm-5.3-flash", "pageZoom": 1.1}, f)
        self.assertEqual(wa.get_slot("main").session.model, "glm-5.3-flash")


class NewWindowTest(SlotTestBase):

    def test_new_window_opens_independent_slot_inheriting_config(self):
        c = self.client()
        c.post("/api/source", json={
            "slot": "main", "source": "zbigmodel", "model": "glm-5.3-flash"})

        opened = []

        def record(slot_id, x=None, y=None):
            opened.append((slot_id, x, y))

        with mock.patch.object(wa, "_open_slot_window", record), \
                mock.patch.object(wa.webview, "windows", [1]):
            data = c.post("/api/new-window", json={"slot": "main"}).get_json()

        self.assertTrue(data["ok"])
        self.assertEqual(data["slot"], "s2")            # 主槽是第 1 个，子槽从 s2 起
        self.assertEqual(len(opened), 1)
        self.assertEqual(opened[0][0], "s2")
        self.assertIsNotNone(opened[0][1])              # 级联位置由后端给定

        new = wa.get_slot("s2")
        self.assertIsNot(new.session, wa.get_slot("main").session)
        # 新窗口沿用发起方的来源/模型，但不共用上下文
        self.assertEqual((new.session.source, new.session.model),
                         ("zbigmodel", "glm-5.3-flash"))
        self.assertEqual(self.user_texts("s2"), [])

    def test_slot_ids_are_never_reused(self):
        def record(slot_id, x=None, y=None):
            return None

        ids = []
        with mock.patch.object(wa, "_open_slot_window", record), \
                mock.patch.object(wa.webview, "windows", [1]):
            for _ in range(3):
                ids.append(self.client().post("/api/new-window", json={}).get_json()["slot"])
        self.assertEqual(ids, ["s2", "s3", "s4"])

    def test_new_window_respects_max_windows(self):
        with mock.patch.object(wa, "_open_slot_window", lambda *a, **k: None), \
                mock.patch.object(wa.webview, "windows", list(range(wa.MAX_WINDOWS))):
            resp = self.client().post("/api/new-window", json={})
        self.assertEqual(resp.status_code, 409)
        self.assertIn(str(wa.MAX_WINDOWS), resp.get_json()["error"])

    def test_window_title_distinguishes_slots(self):
        self.assertEqual(wa._slot_title(wa.DEFAULT_SLOT), wa.APP_TITLE)
        self.assertIn("会话2", wa._slot_title("s2"))


class PageContractTest(SlotTestBase):

    def test_page_reads_slot_from_url(self):
        html = self.client().get("/").get_data(as_text=True)
        self.assertIn("new URLSearchParams(location.search).get('slot')", html)
        self.assertIn("/api/new-window", html)

    def test_history_listing_is_unaffected(self):
        self.assertEqual(self.client().get("/api/history").status_code, 200)


if __name__ == "__main__":
    unittest.main()
