"""扩展证书支持测试（软考 / CDA / 阿里云 ACP / 普通话 / 雅思托福）。

背景：HCIA 之外，学生还会问软考、CDA、阿里云 ACP、普通话、雅思托福。
要求与 HCIA 同等待遇——知识库有据可查、能出分阶段方案、对话能认出来。

本测试覆盖四层：
1. 知识库：5 个新条款都能被检索到，且素材类条款如实标注来源；
2. 方案生成：每个主题都有模板，出得了阶段 + 资源 + 依据条款；
3. 主题识别：口语化问法能认对主题，且不互相抢占；
4. 界面：TOPIC_GROUPS 里列出的主题必须都有模板（防止加了卡片没加模板）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.learning_plans import (  # noqa: E402
    TOPIC_GROUPS,
    generate_learning_plan,
    list_topics,
)
from core.rule_engine import detect_intent, detect_learning_topic, rule_engine  # noqa: E402

# 本轮新增的 5 个证书主题
NEW_TOPICS = ["软考", "CDA", "阿里云 ACP", "普通话", "雅思托福"]


class TestKnowledgeClauses:
    """5 个新条款都能被检索到，且来源标注诚实。"""

    @pytest.mark.parametrize("topic,keyword", [
        ("软考", "软考"),
        ("CDA", "CDA"),
        ("阿里云 ACP", "阿里云"),
        ("普通话", "普通话"),
        ("雅思托福", "雅思"),
    ])
    def test_clause_is_searchable(self, topic, keyword):
        """用证书名搜索，应命中对应的知识库条款。"""
        from core.knowledge import get_searcher

        hits = get_searcher().search(f"{keyword} 备考 报名", top_k=8)
        titles = [h.clause.title for h in hits]
        assert any(keyword in t for t in titles), f"{topic} 未命中条款：{titles}"

    @pytest.mark.parametrize("keyword", ["软考", "CDA", "阿里云", "普通话", "雅思"])
    def test_clause_marks_external_source(self, keyword):
        """厂商/考试机构认证不是校规，必须写明来源，不能伪装成校内制度。"""
        from core.knowledge import get_searcher

        hits = get_searcher().search(keyword, top_k=8)
        clause = next(h.clause for h in hits if keyword in h.clause.title)
        assert clause.category == "考证"
        assert clause.source, f"{keyword} 条款缺少 source 字段"
        # 条款里必须写明"以官方为准"一类的合规提示
        assert any(w in clause.body for w in ("官方", "当期", "最新"))


class TestPlanGeneration:
    """每个新主题都能出结构化方案。"""

    @pytest.mark.parametrize("topic", NEW_TOPICS)
    def test_plan_has_phases(self, topic):
        plan = generate_learning_plan(topic, "大数据", "2025级")
        assert plan.topic == topic
        assert len(plan.phases) >= 3, f"{topic} 阶段太少"
        for ph in plan.phases:
            assert ph.title and ph.duration and ph.actions and ph.weekly_hours

    @pytest.mark.parametrize("topic", NEW_TOPICS)
    def test_plan_has_resources_and_refs(self, topic):
        plan = generate_learning_plan(topic, "大数据", "2025级")
        assert plan.resources, f"{topic} 缺少资源"
        assert plan.doc_refs, f"{topic} 缺少依据条款"

    def test_soft_exam_mentions_subject_choice(self):
        """软考的关键决策是"报哪个科目"，方案必须正面回答。"""
        plan = generate_learning_plan("软考", "大数据", "2025级")
        text = " ".join(
            [plan.headline, plan.major_advice]
            + list(plan.tips)
            + [a for ph in plan.phases for a in ph.actions]
        )
        assert "数据库系统工程师" in text or "软件设计师" in text

    def test_cda_advice_is_data_oriented(self):
        """CDA 对大数据专业应给出"业务分析打底"的定位建议。"""
        plan = generate_learning_plan("CDA", "大数据", "2025级")
        assert "CDA" in plan.major_advice

    def test_ielts_mentions_validity(self):
        """雅思托福有效期 2 年，方案必须提醒别考太早。"""
        plan = generate_learning_plan("雅思托福", "大数据", "2025级")
        tips = " ".join(plan.tips)
        assert "2 年" in tips or "两年" in tips

    def test_putonghua_three_weeks(self):
        """普通话只有 3 阶段（短期可突击），不应硬凑成 4 阶段。"""
        plan = generate_learning_plan("普通话", "大数据", "2025级")
        assert len(plan.phases) == 3


class TestTopicDetection:
    """口语化问法要能认对主题。"""

    @pytest.mark.parametrize("text,expected", [
        ("我想考软考", "软考"),
        ("数据库系统工程师怎么准备", "软考"),
        ("我想考CDA数据分析师", "CDA"),
        ("阿里云ACP认证难吗", "阿里云 ACP"),
        ("普通话二甲怎么练", "普通话"),
        ("我想考雅思", "雅思托福"),
        ("托福和雅思哪个好", "雅思托福"),
    ])
    def test_detection(self, text, expected):
        assert detect_learning_topic(text) == expected

    @pytest.mark.parametrize("text", [
        "我想考软考",
        "我想考CDA数据分析师",
        "阿里云ACP怎么报名",
        "普通话水平测试什么时候报名",
        "我想考雅思",
    ])
    def test_intent_is_learning(self, text):
        intent, _ = detect_intent(text)
        assert intent == "learning"

    def test_ali_yun_acp_not_swallowed_by_hcia(self):
        """阿里云 ACP 不能被 HCIA 规则先吃掉（顺序敏感）。"""
        assert detect_learning_topic("阿里云 ACP 云计算认证怎么考") == "阿里云 ACP"

    def test_unrelated_still_safe(self):
        """教务类提问不能被误判成考证。"""
        for text in ["我的宿舍能换吗", "国家奖学金什么时候评", "图书馆能借几本书"]:
            assert detect_learning_topic(text) == ""


class TestUiGroupsConsistency:
    """界面卡片与模板必须一一对应（加卡片忘加模板是最容易犯的错）。"""

    def test_every_card_has_template(self):
        for topic in list_topics():
            plan = generate_learning_plan(topic, "", "")
            assert plan.phases, f"界面有「{topic}」卡片但没有方案模板"

    def test_group_count(self):
        """两组卡片，覆盖 11 个主题。"""
        assert len(TOPIC_GROUPS) == 2
        assert len(list_topics()) == 11
        for _, items in TOPIC_GROUPS:
            assert items, "分组不能为空"

    def test_new_topics_present_in_groups(self):
        listed = list_topics()
        for topic in NEW_TOPICS:
            assert topic in listed, f"{topic} 未出现在界面卡片清单里"


class TestConversation:
    """端到端：一句话 → 规则引擎 → 结构化回答。"""

    @pytest.mark.parametrize("text,topic", [
        ("我想考软考，给我个学习计划", "软考"),
        ("CDA数据分析师怎么备考", "CDA"),
        ("阿里云ACP怎么准备", "阿里云 ACP"),
        ("普通话二甲怎么练", "普通话"),
        ("我想考雅思，给我个方案", "雅思托福"),
    ])
    def test_reply_is_structured(self, text, topic):
        reply = rule_engine.handle(text)
        assert f"学习方案 · {topic}" in reply
        assert "第一阶段" in reply
        assert "每周投入" in reply
        assert "依据条款" in reply
        assert "以官方当期公布为准" in reply
