"""降级链与工具调用一致性测试。

重点验证《期末考查要求》的一条硬性指标：
**"用自然语言完成至少一个有工具参与的真实任务（能看见工具被调用）"**
在任何降级层级下都成立。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.graph import build_graph  # noqa: E402
from core.llm import get_provider  # noqa: E402
from core.rule_engine import rule_engine  # noqa: E402
from tools.registry import get_local_tools  # noqa: E402


class TestToolRegistry:
    """工具注册表。"""

    def test_has_eight_local_tools(self):
        """8 个本地工具齐备。"""
        tools = get_local_tools()
        assert len(tools) == 8
        names = {t.name for t in tools}
        assert {
            "list_courses",
            "get_my_enrollments",
            "get_my_profile",
            "check_schedule_conflict",
            "enroll_course",
            "search_policy",
            "check_eligibility",
            "write_artifact",
        } <= names

    def test_tools_have_descriptions(self):
        """每个工具都有描述（模型靠它决定何时调用）。"""
        for t in get_local_tools():
            assert t.description and len(t.description) > 10


class TestMcpToolMounting:
    """MCP 工具是否真正挂载进智能体（而非仅在文档/清单里声明）。"""

    def test_mcp_tools_are_sync_callable(self):
        """MCP 工具被桥接为同步可调用的 LangChain 工具。"""
        from core.mcp import get_mcp_tools

        tools = get_mcp_tools()
        names = {t.name for t in tools}
        assert {"get_deadlines", "list_policy_docs"} <= names

    def test_build_graph_defaults_include_mcp_tools(self):
        """默认 build_graph() 挂载 8 本地 + 2 MCP 工具。"""
        from core.graph import build_graph

        graph = build_graph()
        names = {t.name for t in graph.tools}
        assert {"get_deadlines", "list_policy_docs"} <= names

    def test_rule_engine_deadline_uses_mcp_tool(self):
        """规则引擎的截止查询路径真实调用 MCP 工具 get_deadlines。"""
        rule_engine.tool_events = []
        rule_engine.handle("奖学金什么时候截止申请？")
        invoked = {e["tool"] for e in rule_engine.tool_events}
        assert "get_deadlines" in invoked


class TestRuleEngineToolCalls:
    """规则降级引擎的工具调用。"""

    @pytest.mark.parametrize(
        "query,expected_tool",
        [
            ("我GPA3.6能申请哪些奖助学金？", "check_eligibility"),
            ("我已选了哪些课？", "get_my_enrollments"),            ("下学期能选哪些课？", "list_courses"),
            ("国家奖学金的条件是什么？", "search_policy"),
        ],
    )
    def test_tool_is_invoked(self, query: str, expected_tool: str):
        """降级路径下确实调用了真实工具。"""
        rule_engine.tool_events = []
        rule_engine.handle(query)
        invoked = {e["tool"] for e in rule_engine.tool_events}
        assert expected_tool in invoked, f"未调用 {expected_tool}，实际调用了 {invoked}"

    def test_events_have_summary(self):
        """工具事件带有结果摘要。"""
        rule_engine.tool_events = []
        rule_engine.handle("我GPA3.6能申请哪些奖助学金？")
        assert rule_engine.tool_events
        assert rule_engine.tool_events[0]["summary"]

    def test_same_tools_as_model_path(self):
        """降级路径与模型路径使用同一套工具对象（本地工具 + MCP 工具）。"""
        from core.mcp import get_mcp_tools

        expected = {t.name for t in get_local_tools() + get_mcp_tools()}
        assert set(rule_engine.tools) == expected


class TestGraphEventStream:
    """状态图事件流。"""

    def test_rule_path_emits_tool_events(self):
        """降级路径产生 tool_start / tool_end 事件。"""
        graph = build_graph(tools=get_local_tools())
        events = list(graph.run("我GPA3.6能申请哪些奖助学金？"))
        types = [e.get("type") for e in events]
        assert "tool_start" in types, "没有 tool_start 事件"
        assert "tool_end" in types, "没有 tool_end 事件"
        assert "done" in types

    def test_done_event_contains_reply(self):
        """done 事件带完整回复文本。"""
        graph = build_graph(tools=get_local_tools())
        events = list(graph.run("我GPA3.6能申请哪些奖助学金？"))
        done = [e for e in events if e.get("type") == "done"]
        assert done
        assert "助学金" in done[0]["content"]

    def test_level_event_emitted(self):
        """始终上报当前接入层级（界面指示灯用）。"""
        graph = build_graph(tools=get_local_tools())
        events = list(graph.run("我已选了哪些课？"))
        levels = [e for e in events if e.get("type") == "level"]
        assert levels
        assert levels[0].get("display")


class TestDegradationChain:
    """降级链。"""

    def test_provider_reports_status(self):
        """提供方能报告当前状态。"""
        status = get_provider().status()
        assert status.display
        assert status.level in {"bailian", "ollama", "mock"}

    def test_mock_level_has_no_real_model(self):
        """规则降级层标记为非真实模型。"""
        provider = get_provider()
        if provider.level == "mock":
            assert provider.has_real_model is False
            assert provider.llm is None


class TestIntentDisambiguation:
    """意图消歧。

    "奖学金" 同时属于资格判定与政策检索两类意图，
    这里固化区分规则，防止后续修改引入回归。
    """

    @pytest.mark.parametrize(
        "query,expected",
        [
            # 问"我能不能拿" → 资格判定
            ("我GPA3.6能申请哪些奖助学金？要什么材料？", "eligibility"),
            ("我能申请国家助学金吗？", "eligibility"),
            ("我的成绩能拿国家奖学金吗？", "eligibility"),
            # 问"条文本身" → 政策检索
            ("国家奖学金的条件是什么？", "policy"),
            ("国家励志奖学金需要哪些材料？", "policy"),
            ("校级奖学金的评选时间是什么？", "policy"),
            # 问截止时间 → 截止查询
            ("什么时候截止申请？", "deadline"),
            # 问可选课程 → 课程目录
            ("下学期能选哪些课？避开周三下午，挑3门", "courses"),
            # 要「选组合」而非「提交选课」→ 课程推荐
            ("帮我选3门不撞的课", "courses"),
            ("帮我选3门不撞的课避开周三", "courses"),
            ("帮我选两门课", "courses"),
            # 问我的已选 → 已选查询
            ("我已选了哪些课？", "enrollments"),
            # 要文档 → 产出物
            ("帮我把选课方案整理成一份清单文件", "artifact"),
            ("帮我排一份每周学习计划", "artifact"),
            # 要提交选课（点名具体课程）→ 选课
            ("把《数据结构与算法》选上", "enroll"),
            ("帮我选数据结构这门课", "enroll"),
        ],
    )
    def test_intent_routing(self, query: str, expected: str):
        """意图路由正确。"""
        from core.rule_engine import detect_intent

        intent, confidence = detect_intent(query)
        assert intent == expected, f"「{query}」被识别为 {intent}，期望 {expected}"
        assert 0 < confidence <= 1
