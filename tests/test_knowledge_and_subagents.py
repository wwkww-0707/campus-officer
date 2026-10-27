"""知识库检索与子代理求解测试。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.knowledge import get_searcher  # noqa: E402
from core.schemas import Course, Timeslot  # noqa: E402
from core.subagents import (  # noqa: E402
    BudgetRequest,
    SolveRequest,
    course_combination_agent,
    study_budget_agent,
)


class TestPolicySearch:
    """校规检索。"""

    def test_knowledge_base_loaded(self):
        """知识库加载到条款。"""
        searcher = get_searcher()
        assert len(searcher.docs) > 0

    def test_finds_real_policy(self):
        """能检索到公开真实文件。"""
        hits = get_searcher().search("国家奖学金 申请条件 金额", top_k=3)
        assert hits
        assert any("财教" in h.clause.doc_number for h in hits)

    def test_expired_clause_downweighted(self):
        """失效条款被降权，排在现行条款之后。"""
        hits = get_searcher().search("校级奖学金 覆盖率 金额", top_k=4)
        assert hits
        expired_idx = [i for i, h in enumerate(hits) if h.clause.is_expired]
        valid_idx = [i for i, h in enumerate(hits) if not h.clause.is_expired]
        if expired_idx and valid_idx:
            assert min(expired_idx) > min(valid_idx)

    def test_expired_clause_has_note(self):
        """失效条款带降权说明。"""
        hits = get_searcher().search("旧版 奖学金 覆盖率", top_k=5)
        expired = [h for h in hits if h.clause.is_expired]
        if expired:
            assert "失效" in expired[0].note

    def test_sample_clause_flagged(self):
        """校内示例条款被标记。"""
        hits = get_searcher().search("选课 学分上限 退课", top_k=5)
        sample = [h for h in hits if h.clause.is_sample]
        if sample:
            assert "演示样例" in sample[0].note

    def test_unknown_keyword_returns_empty(self):
        """无关关键词检索不到。"""
        hits = get_searcher().search("量子纠缠拓扑绝缘体", top_k=3)
        assert hits == []

    def test_every_clause_has_doc_number(self):
        """每条条款都有文号，保证可核验。"""
        for clause in get_searcher().list_docs():
            assert clause.doc_number

    def test_deadlines_extracted(self):
        """能抽取到时间节点。"""
        items = get_searcher().deadlines("助学金")
        assert items
        assert all("text" in i for i in items)

    def test_clause_body_not_truncated(self):
        """条款正文完整保留（含 Markdown 表格），不因首行标题被截断。"""
        searcher = get_searcher()
        kaoyan = [d for d in searcher.list_docs() if d.title.startswith("考研")]
        assert kaoyan, "应存在考研类条款"
        body = kaoyan[0].body
        # 正文应包含时间线表格（| 分隔符）与行动方案，而不是只剩首行标题
        assert "|" in body, f"正文被截断，未保留表格：{body[:80]!r}"
        assert len(body) > 100, f"正文过短，疑似被截断：{body[:80]!r}"


def _slot(weekday: int, start: int, end: int) -> Timeslot:
    """构造时段。"""
    return Timeslot(weekday=weekday, start_period=start, end_period=end)


class TestCourseCombinationAgent:
    """课程组合求解子代理。"""

    def test_finds_conflict_free_combo(self):
        """求出的组合内部无冲突。"""
        pool = [
            Course(id=1, code="A", name="甲", credits=3, rating=4.0,
                   slots=[_slot(2, 1, 2)], prerequisites=[]),
            Course(id=2, code="B", name="乙", credits=3, rating=4.5,
                   slots=[_slot(2, 1, 2)], prerequisites=[]),
            Course(id=3, code="C", name="丙", credits=3, rating=4.2,
                   slots=[_slot(4, 1, 2)], prerequisites=[]),
        ]
        result = course_combination_agent.solve(
            pool, [], SolveRequest(target_count=2, max_credits=10)
        )
        assert len(result.courses) == 2
        # 组合内部不应冲突
        codes = {c.code for c in result.courses}
        assert not ({"A", "B"} <= codes)

    def test_respects_credit_cap(self):
        """总学分不超过上限。"""
        pool = [
            Course(id=i, code=f"C{i}", name=f"课{i}", credits=4, rating=4.0,
                   slots=[_slot(i % 7 + 1, 1, 2)], prerequisites=[])
            for i in range(1, 6)
        ]
        result = course_combination_agent.solve(
            pool, [], SolveRequest(target_count=4, max_credits=10)
        )
        assert result.total_credits <= 10

    def test_excludes_full_courses(self):
        """满员课程被排除并记录原因。"""
        pool = [
            Course(id=1, code="F", name="满员课", credits=3, rating=5.0,
                   capacity=10, enrolled=10, slots=[_slot(2, 1, 2)], prerequisites=[]),
            Course(id=2, code="O", name="正常课", credits=3, rating=3.0,
                   capacity=10, enrolled=1, slots=[_slot(4, 1, 2)], prerequisites=[]),
        ]
        result = course_combination_agent.solve(
            pool, [], SolveRequest(target_count=2, max_credits=10)
        )
        assert all(c.code != "F" for c in result.courses)
        assert any(r["code"] == "F" and "名额已满" in r["reason"] for r in result.rejected)

    def test_respects_prerequisites(self):
        """先修条件不满足的课程不会被选中。"""
        pool = [
            Course(id=1, code="ML", name="机器学习", credits=3, rating=5.0,
                   slots=[_slot(2, 1, 2)], prerequisites=["CS201"]),
            Course(id=2, code="CS201", name="数据结构", credits=4, rating=4.0,
                   slots=[_slot(4, 1, 2)], prerequisites=[]),
        ]
        result = course_combination_agent.solve(
            pool, [], SolveRequest(target_count=1, max_credits=10)
        )
        assert result.courses[0].code == "CS201"

    def test_avoids_requested_weekday(self):
        """避开指定星期的课程不入选。"""
        pool = [
            Course(id=1, code="W", name="周三课", credits=3, rating=5.0,
                   slots=[_slot(3, 1, 2)], prerequisites=[]),
            Course(id=2, code="T", name="周四课", credits=3, rating=4.0,
                   slots=[_slot(4, 1, 2)], prerequisites=[]),
        ]
        result = course_combination_agent.solve(
            pool, [], SolveRequest(target_count=1, max_credits=10, avoid_weekdays=[3])
        )
        assert result.courses[0].code == "T"

    def test_no_solution_reports_reason(self):
        """无解时给出可解释的原因。"""
        pool = [
            Course(id=1, code="X", name="唯一课", credits=10, rating=4.0,
                   slots=[_slot(2, 1, 2)], prerequisites=[])
        ]
        result = course_combination_agent.solve(
            pool, [], SolveRequest(target_count=2, max_credits=5)
        )
        assert result.courses == []
        assert "未能找到" in result.message


class TestStudyBudgetAgent:
    """学习时间预算子代理。"""

    def test_allocation_sums_to_free_hours(self):
        """四象限分配之和等于可用时间。"""
        courses = [
            Course(id=1, code="A", name="甲", credits=3, hours_per_week=4,
                   slots=[_slot(2, 1, 2)], prerequisites=[]),
        ]
        plan = study_budget_agent.plan(courses, BudgetRequest(free_hours_per_week=20))
        assert sum(plan.allocation.values()) == pytest.approx(plan.free_hours, abs=0.2)

    def test_weekly_schedule_listed(self):
        """周课表被列出。"""
        courses = [
            Course(id=1, code="A", name="甲", credits=3, hours_per_week=4,
                   slots=[_slot(2, 1, 2)], prerequisites=[]),
        ]
        plan = study_budget_agent.plan(courses, BudgetRequest())
        assert plan.weekly_schedule
        assert plan.milestones
        assert plan.risks

    def test_low_free_time_triggers_risk(self):
        """可用时间过少时给出风险提示。"""
        courses = [
            Course(id=1, code="A", name="甲", credits=3, hours_per_week=20,
                   slots=[_slot(2, 1, 2)], prerequisites=[]),
        ]
        plan = study_budget_agent.plan(courses, BudgetRequest(free_hours_per_week=5))
        assert any("时间不足" in r or "课时" in r for r in plan.risks)
