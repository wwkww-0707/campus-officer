"""公告同步与原文链接测试。

覆盖两个此前被用户报告的问题：
1. 官网同步失败（playwright 缺失 → 已在 requirements 声明，此处只测数据层契约）
2. 公告卡片必须能跳转官网原文 → 依赖 `url` 字段正确存取

数据层测试用临时数据库，不触碰生产 `data/campus.db`。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import db  # noqa: E402


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    """把 db 模块的数据库路径指向临时文件，并初始化表结构。"""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_campus.db")
    db.init_db()
    return db


class TestAnnouncementUrl:
    """公告 url 字段。"""

    def test_url_roundtrip(self, temp_db):
        """写入的 url 能原样读回（界面据此渲染跳转）。"""
        temp_db.add_announcement(
            title="关于2026年中秋节、国庆节放假安排的通知",
            category="通知",
            content="",
            source="杉达学院官网",
            url="https://news.sandau.edu.cn/64/48/c1218a91208/page.htm",
        )
        items = temp_db.list_announcements()
        assert items[0]["url"] == "https://news.sandau.edu.cn/64/48/c1218a91208/page.htm"

    def test_url_defaults_to_empty(self, temp_db):
        """手动录入不填链接时，url 为空字符串（界面不渲染链接）。"""
        temp_db.add_announcement(title="手动录入的公告")
        assert temp_db.list_announcements()[0]["url"] == ""

    def test_source_preserved(self, temp_db):
        """来源字段保留，用于卡片底部展示。"""
        temp_db.add_announcement(title="测试公告", source="杉达学院官网")
        assert temp_db.list_announcements()[0]["source"] == "杉达学院官网"


class TestAnnouncementOrdering:
    """公告排序：日程类（通知/讲座/活动）应排在新闻前。"""

    def test_category_priority(self, temp_db):
        """同一日期下，顺序为 通知 > 讲座 > 活动 > 新闻。"""
        for cat in ["新闻", "活动", "讲座", "通知"]:
            temp_db.add_announcement(title=f"{cat}条目", category=cat)

        cats = [a["category"] for a in temp_db.list_announcements()]
        assert cats == ["通知", "讲座", "活动", "新闻"]

    def test_dated_items_first(self, temp_db):
        """有活动日期的排在最前。"""
        temp_db.add_announcement(title="无日期通知", category="通知")
        temp_db.add_announcement(
            title="有日期活动", category="活动", event_date="2026-11-01"
        )
        items = temp_db.list_announcements()
        assert items[0]["title"] == "有日期活动"
