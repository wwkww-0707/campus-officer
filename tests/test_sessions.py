"""会话管理测试。

覆盖用户反馈的问题：新建会话后切换，新建的会话从列表中消失。

原实现用「有消息 or 是当前会话」过滤列表，空会话一切走就不显示，
用户会以为"新建的会话丢了"。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.memory import SessionStore  # noqa: E402


@pytest.fixture()
def store() -> SessionStore:
    """独立会话库（conftest 已把路径指向临时目录）。"""
    s = SessionStore()
    for x in s.list_sessions():
        s.delete_session(x.id)
    return s


class TestSessionLifecycle:
    """会话生命周期。"""

    def test_create_session(self, store):
        """新建会话后可在列表中查到。"""
        sid = store.create_session("新会话")
        sessions = store.list_sessions()
        assert any(s.id == sid for s in sessions)

    def test_empty_session_is_listed(self, store):
        """**空会话也必须出现在列表里**（本 bug 的核心回归点）。"""
        sid = store.create_session("新会话")
        listed = [s for s in store.list_sessions() if s.id == sid]
        assert listed, "空会话未出现在列表中"
        assert listed[0].message_count == 0

    def test_multiple_empty_sessions_all_listed(self, store):
        """多个空会话都应保留，不会互相顶掉。"""
        ids = [store.create_session("新会话") for _ in range(3)]
        listed = {s.id for s in store.list_sessions()}
        assert all(i in listed for i in ids), "有新建的空会话丢失"

    def test_rename_session(self, store):
        """标题可更新（发出首条消息后自动命名依赖此能力）。"""
        sid = store.create_session("新会话")
        store.rename_session(sid, "我 GPA 3.6 能申请哪些奖助学金")
        renamed = [s for s in store.list_sessions() if s.id == sid][0]
        assert renamed.title.startswith("我 GPA 3.6")

    def test_delete_session(self, store):
        """删除后不在列表中出现。"""
        sid = store.create_session("待删除")
        store.delete_session(sid)
        assert not any(s.id == sid for s in store.list_sessions())

    def test_messages_persisted_per_session(self, store):
        """消息按会话隔离，互不串台。"""
        a = store.create_session("会话A")
        b = store.create_session("会话B")
        store.add_message(a, "user", "A 的问题")
        store.add_message(b, "user", "B 的问题")

        assert len(store.get_messages(a)) == 1
        assert len(store.get_messages(b)) == 1
        assert store.get_messages(a)[0]["content"] == "A 的问题"
        assert store.get_messages(b)[0]["content"] == "B 的问题"

    def test_delete_cascades_messages(self, store):
        """删除会话时其消息一并清除。"""
        sid = store.create_session("会话")
        store.add_message(sid, "user", "内容")
        store.delete_session(sid)
        assert store.get_messages(sid) == []


class TestSessionListedInUI:
    """界面层面的会话列表。"""

    def test_empty_session_visible_in_sidebar(self):
        """界面上，空会话切走后仍在列表中显示。"""
        pytest.importorskip("streamlit")
        from streamlit.testing.v1 import AppTest

        store = SessionStore()
        for x in store.list_sessions():
            store.delete_session(x.id)
        a = store.create_session("会话A")
        store.add_message(a, "user", "问题")
        b = store.create_session("新会话")  # 空会话

        at = AppTest.from_file(
            str(Path(__file__).resolve().parent.parent / "app.py"),
            default_timeout=180,
        )
        at.run()
        assert not at.exception

        # 两侧会话按钮都应渲染
        labels = [btn.label for btn in at.button if btn.label.startswith(("●", "○"))]
        assert len(labels) >= 2, f"会话列表未全部显示：{labels}"
        # 空会话显示为「新会话」
        assert any("新会话" in lb for lb in labels)
