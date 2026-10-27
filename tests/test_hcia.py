"""HCIA 认证支持测试。

背景：用户提问「我想考 HCIA 证书」时，希望能拿到**具体的学习方案、时间计划
与专业针对性建议**，而不是一段泛泛的考证说明。

本测试覆盖三层：
1. 知识库：HCIA 条款可检索，且如实标注"外部厂商认证、非校内制度"；
2. 方案生成：模板存在，能出分阶段计划 + 资源 + 专业建议；
3. 对话链路：意图识别为 learning、主题识别为 HCIA、输出含四阶段与依据条款。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.knowledge import get_searcher  # noqa: E402
from core.learning_plans import generate_learning_plan  # noqa: E402
from core.rule_engine import detect_intent, detect_learning_topic, rule_engine  # noqa: E402


class TestKnowledgeClause:
    """知识库中的 HCIA 条款。"""

    def test_clause_is_searchable(self):
        """搜 HCIA 能命中该条款。"""
        hits = get_searcher().search("HCIA 华为认证 备考", top_k=5)
        titles = [h.clause.title for h in hits]
        assert any("HCIA" in t for t in titles), f"未命中 HCIA 条款：{titles}"

    def test_clause_is_honest_about_source(self):
        """必须如实标明是外部厂商认证、非校内制度（不能伪装成校规）。"""
        hits = get_searcher().search("HCIA", top_k=5)
        clause = next(h.clause for h in hits if "HCIA" in h.clause.title)
        assert clause.category == "考证"
        assert "非校内制度" in clause.doc_number or "非校内制度" in clause.source
        assert "华为" in clause.source

    def test_body_covers_directions(self):
        """正文给出方向选择建议（大数据专业最关心 HCIA-Big Data）。"""
        hits = get_searcher().search("HCIA", top_k=5)
        clause = next(h.clause for h in hits if "HCIA" in h.clause.title)
        assert "HCIA-Big Data" in clause.body
        assert "HCIA-Datacom" in clause.body


class TestPlanGeneration:
    """结构化方案生成。"""

    def test_plan_has_four_phases(self):
        """8 周方案拆成 4 个阶段，每阶段都有行动项与每周投入。"""
        plan = generate_learning_plan("HCIA 认证", "大数据", "2025级")
        assert plan.topic == "HCIA 认证"
        assert len(plan.phases) == 4
        for ph in plan.phases:
            assert ph.title and ph.duration and ph.actions and ph.weekly_hours

    def test_plan_has_resources_and_tips(self):
        """资源含官方渠道，建议提到有效期与"实验优先"。"""
        plan = generate_learning_plan("HCIA 认证", "大数据", "2025级")
        names = " ".join(r["name"] for r in plan.resources)
        assert "华为人才在线" in names
        tips = " ".join(plan.tips)
        assert "实验" in tips
        assert "3 年" in tips  # 证书有效期提醒

    def test_major_advice_is_specific(self):
        """大数据专业应给出 HCIA-Big Data 的具体建议。"""
        plan = generate_learning_plan("HCIA 认证", "大数据", "2025级")
        assert "HCIA-Big Data" in plan.major_advice
        assert "CDA" in plan.major_advice

    def test_without_major_still_works(self):
        """未提供专业时仍能出通用方案（向后兼容）。"""
        plan = generate_learning_plan("HCIA 认证")
        assert plan.phases
        assert plan.major_name == ""

    def test_doc_ref_points_to_clause(self):
        """方案末尾能给出依据条款，便于核验。"""
        plan = generate_learning_plan("HCIA 认证", "大数据", "2025级")
        assert any("HCIA" in ref for ref in plan.doc_refs)


class TestConversation:
    """对话链路：一句话 → 意图 → 主题 → 结构化回答。"""

    @pytest.mark.parametrize("text", [
        "我想考HCIA证书",
        "HCIA-Big Data 怎么准备",
        "华为认证难考吗",
        "我想考HCIP",
    ])
    def test_intent_is_learning(self, text):
        """厂商认证类提问应归到学习方案意图。"""
        intent, _ = detect_intent(text)
        assert intent == "learning"

    @pytest.mark.parametrize("text,expected", [
        ("我想考HCIA证书", "HCIA 认证"),
        ("HCIA-Big Data 怎么备考", "HCIA 认证"),
        ("华为认证 HCIA 难吗", "HCIA 认证"),
        ("四六级怎么准备", "四六级"),
        ("我要考研", "考研"),
    ])
    def test_topic_detection(self, text, expected):
        """能认出具体主题；认不出时返回空串由上层兜底。"""
        assert detect_learning_topic(text) == expected

    def test_unrelated_queries_not_swallowed(self):
        """选课、奖助学金等不能被误判成 HCIA。"""
        for text in ["下学期能选哪些课", "我GPA3.6能申请哪些奖助学金", "帮我排一份每周学习计划"]:
            assert detect_learning_topic(text) == ""
            assert detect_intent(text)[0] not in ("learning",)

    def test_reply_is_structured(self):
        """回答必须包含阶段、时间投入、资源、专业建议与依据条款。"""
        reply = rule_engine.handle("我想考HCIA证书，给我个学习计划")
        assert "学习方案 · HCIA 认证" in reply
        assert "第一阶段" in reply and "第四阶段" in reply
        assert "每周投入" in reply
        assert "资源" in reply
        assert "针对" in reply          # 专业针对性建议
        assert "依据条款" in reply
        assert "以官方当期公布为准" in reply   # 合规提示
