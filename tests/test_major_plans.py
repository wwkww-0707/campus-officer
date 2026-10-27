"""专业定制学习方案测试。

需求：学生档案已绑定专业，学习方案应据此定制，而不是给所有人同一套模板。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.learning_plans import generate_learning_plan, generate_major_plan  # noqa: E402
from core.major_profiles import grade_stage, list_major_names, match_major  # noqa: E402


class TestMajorMatching:
    """专业画像匹配。"""

    def test_exact_match(self):
        """完整专业名精确命中。"""
        assert match_major("计算机科学与技术").name == "计算机科学与技术"

    def test_keyword_match(self):
        """含关键词的变体也能命中（如「软件工程」归到计算机画像）。"""
        assert match_major("软件工程").name == "计算机科学与技术"
        assert match_major("人工智能").name == "计算机科学与技术"

    def test_bigdata_not_swallowed_by_cs(self):
        """「大数据」不能被计算机画像抢走——它有独立画像。

        历史 bug：'大数据' 曾写在计算机画像的关键词里，
        学生填「大数据」时方案全是计算机的课，看起来像"没同步专业"。
        """
        assert match_major("大数据").name == "大数据技术"
        assert match_major("大数据技术").name == "大数据技术"
        assert match_major("数据科学与大数据技术").name == "大数据技术"
        assert match_major("数据挖掘").name == "大数据技术"

    def test_other_majors(self):
        """其他专业各自命中对应画像。"""
        assert match_major("护理学").name == "护理学"
        assert match_major("国际经济与贸易").name == "国际经济与贸易"
        assert match_major("视觉传达设计").name == "视觉传达设计"
        assert match_major("金融学").name == "金融学"

    def test_unknown_falls_back(self):
        """未知专业回退到通用画像，不抛异常。"""
        assert match_major("宇宙学").name == "通用"
        assert match_major("").name == "通用"

    def test_builtin_majors_count(self):
        """内置专业画像数量充足（覆盖常见专业）。"""
        assert len(list_major_names()) >= 8


class TestGradeStage:
    """年级 → 学年阶段推断。"""

    @pytest.mark.parametrize(
        "grade,expected",
        [
            ("2024级", "大三"),   # 2024 入学，2026-10 处于第三学年
            ("2025级", "大二"),
            ("2023级", "大四"),
        ],
    )
    def test_stage_inference(self, grade: str, expected: str):
        """按入学年份与当前月份推断阶段。"""
        assert grade_stage(grade) == expected

    def test_invalid_grade(self):
        """无法解析时返回空串。"""
        assert grade_stage("") == ""
        assert grade_stage("未知") == ""


class TestMajorPlan:
    """专业学习方案。"""

    def test_cs_plan_is_specific(self):
        """计算机专业方案包含本专业核心课与能力，而非通用模板。"""
        plan = generate_major_plan("计算机科学与技术", "2024级")
        assert plan.major_name == "计算机科学与技术"
        assert plan.current_stage == "大三"
        assert any("数据结构" in c for c in plan.major_focus)
        assert any("操作系统" in c for c in plan.major_focus)
        # 阶段从当前学年排到毕业
        assert len(plan.phases) >= 2
        assert "大三" in plan.phases[0].title

    def test_nursing_plan_differs_from_cs(self):
        """不同专业方案内容不同（针对性有效）。"""
        cs = generate_major_plan("计算机科学与技术", "2024级")
        nur = generate_major_plan("护理学", "2024级")
        assert cs.major_focus != nur.major_focus
        assert any("护理" in c for c in nur.major_focus)
        assert not any("数据结构" in c for c in nur.major_focus)

    def test_plan_has_all_sections(self):
        """方案四要素齐备：阶段 / 时间投入 / 资源 / 要点。"""
        plan = generate_major_plan("计算机科学与技术", "2024级")
        assert plan.phases
        for ph in plan.phases:
            assert ph.title and ph.duration and ph.actions and ph.weekly_hours
        assert len(plan.resources) >= 4
        assert plan.tips

    def test_unknown_major_still_works(self):
        """未知专业也能出方案：标题保留学生填写的专业名，内容走通用画像。"""
        plan = generate_major_plan("宇宙学", "2024级")
        assert plan.phases
        assert plan.major_name == "宇宙学"
        assert plan.profile_name == "通用"

    def test_second_year_gets_three_stages(self):
        """大二学生覆盖大二/大三/大四三个阶段。"""
        plan = generate_major_plan("计算机科学与技术", "2025级")
        assert len(plan.phases) == 3
        assert "大二" in plan.phases[0].title
        assert "大四" in plan.phases[-1].title


class TestTopicPlanMajorBoost:
    """主题方案叠加专业建议。"""

    def test_kaoyan_has_cs_advice(self):
        """考研方案叠加计算机专业建议（408）。"""
        plan = generate_learning_plan("考研", "计算机科学与技术", "2024级")
        assert plan.major_name == "计算机科学与技术"
        assert "408" in plan.major_advice
        # 专业建议置顶
        assert plan.tips[0] == plan.major_advice

    def test_internship_has_major_advice(self):
        """实习方案带专业针对性建议。"""
        plan = generate_learning_plan("实习", "计算机科学与技术", "2024级")
        assert plan.major_advice
        assert "项目" in plan.major_advice or "刷题" in plan.major_advice

    def test_no_major_still_generates(self):
        """未提供专业时仍生成通用方案（向后兼容）。"""
        plan = generate_learning_plan("考研")
        assert plan.phases
        assert plan.major_name == ""

    def test_different_majors_get_different_advice(self):
        """不同专业的主题建议不同。"""
        cs = generate_learning_plan("考研", "计算机科学与技术", "2024级")
        nur = generate_learning_plan("考研", "护理学", "2024级")
        assert cs.major_advice != nur.major_advice


class TestBigDataProfile:
    """大数据专业画像（独立于计算机）。"""

    def test_plan_uses_bigdata_courses(self):
        """方案用的是数据方向的课，不是计算机的操作系统/组原。"""
        plan = generate_major_plan("大数据", "2025级")
        joined = " ".join(plan.major_focus)
        assert "Python 数据分析" in joined
        assert "Hadoop" in joined or "Spark" in joined
        # 不应出现计算机专属课
        assert "操作系统" not in joined

    def test_plan_certs_are_data_oriented(self):
        """推荐证书是数据分析/大数据方向。"""
        plan = generate_major_plan("大数据", "2025级")
        names = " ".join(r["name"] for r in plan.resources)
        assert "CDA" in names or "大数据认证" in names


class TestDisplayedMajorName:
    """方案标题应显示学生填写的专业名，而不是画像的规范名。"""

    def test_major_name_is_user_input(self):
        """填「大数据」→ 标题显示「大数据」，不显示「大数据技术」。"""
        plan = generate_major_plan("大数据", "2025级")
        assert plan.major_name == "大数据"
        assert plan.profile_name == "大数据技术"
        assert plan.topic.startswith("大数据 ")
        assert "大数据技术 专业学习方案" not in plan.topic

    def test_headline_uses_user_input(self):
        """概述里也应是用户填写的专业名。"""
        plan = generate_major_plan("大数据", "2025级")
        assert "「大数据」" in plan.headline

    def test_topic_plan_major_name_is_user_input(self):
        """升学/考证方案叠加专业时同样显示用户填写值。"""
        plan = generate_learning_plan("考研", "大数据", "2025级")
        assert plan.major_name == "大数据"
        assert plan.profile_name == "大数据技术"

    def test_empty_department_falls_back_to_profile_name(self):
        """专业为空时回退到画像名，不出现空标题。"""
        plan = generate_major_plan("", "2025级")
        assert plan.major_name  # 通用画像名
