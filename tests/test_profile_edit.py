"""档案编辑能力测试。

背景：用户在界面上改「困难认定」改不动——因为档案编辑表单只提供
学校/姓名/学号/年级/专业五个字段，「困难认定」「政审」「GPA」「排名」
全是只读展示，根本没有编辑入口。

本测试固化修复后的行为：影响奖助学金资格判定的字段都可编辑并持久化，
且改动会真实影响资格初筛结果。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import db, eligibility  # noqa: E402
from core.schemas import StudentProfile  # noqa: E402


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    """把 db 模块指向临时数据库并初始化。"""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    return db


def _profile(**kwargs) -> StudentProfile:
    """构造一份测试档案，可用关键字覆盖任意字段。"""
    base = dict(
        student_id="2025016",
        name="测试同学",
        grade="2024级",
        department="计算机科学与技术",
        gpa=3.6,
        credit_earned=68.0,
        credit_required=165.0,
        class_rank_ratio=0.08,
        family_difficulty=True,
        political_review=True,
    )
    base.update(kwargs)
    return StudentProfile(**base)


class TestEditableFields:
    """影响资格判定的字段必须可改。"""

    def test_toggle_family_difficulty(self, temp_db):
        """困难认定可在「已认定 ↔ 未认定」之间切换（核心 bug）。"""
        temp_db.save_profile(_profile(family_difficulty=True))
        assert temp_db.load_profile("current").family_difficulty is True

        temp_db.save_profile(_profile(family_difficulty=False))
        assert temp_db.load_profile("current").family_difficulty is False

        temp_db.save_profile(_profile(family_difficulty=True))
        assert temp_db.load_profile("current").family_difficulty is True

    def test_toggle_political_review(self, temp_db):
        """政审结论同样可改。"""
        temp_db.save_profile(_profile(political_review=False))
        assert temp_db.load_profile("current").political_review is False

    def test_edit_gpa_and_credits(self, temp_db):
        """GPA 与学分可改，且保留浮点精度。"""
        temp_db.save_profile(_profile(gpa=3.25, credit_earned=95.0, credit_required=170.0))
        loaded = temp_db.load_profile("current")
        assert loaded.gpa == pytest.approx(3.25)
        assert loaded.credit_earned == pytest.approx(95.0)
        assert loaded.credit_required == pytest.approx(170.0)

    def test_rank_zero_means_unrated(self, temp_db):
        """排名为 0 表示未核定，写入 None 而非 0.0。

        界面把排名以百分比呈现，0 视为「未核定」；若写成 0.0，
        资格判定会误当成「专业第 0 名」而全部通过。
        """
        temp_db.save_profile(_profile(class_rank_ratio=None))
        loaded = temp_db.load_profile("current")
        assert loaded.class_rank_ratio is None

    def test_rank_percent_rounding(self, temp_db):
        """界面输入 12% → 存 0.12。"""
        temp_db.save_profile(_profile(class_rank_ratio=round(12 / 100.0, 4)))
        assert temp_db.load_profile("current").class_rank_ratio == pytest.approx(0.12)


class TestEligibilityReacts:
    """改动必须真实影响资格初筛结果，而不只是存下来。"""

    def test_difficulty_off_loses_need_based_items(self):
        """取消困难认定后，依赖困难认定的项目不再符合。"""
        on = eligibility.evaluate(_profile(family_difficulty=True))
        off = eligibility.evaluate(_profile(family_difficulty=False))

        on_ok = {i.name for i in on if i.eligible}
        off_ok = {i.name for i in off if i.eligible}

        assert "国家励志奖学金" in on_ok
        assert "国家励志奖学金" not in off_ok
        assert "国家助学金" not in off_ok
        assert len(off_ok) < len(on_ok)

    def test_political_review_off_blocks_most(self):
        """政审不合格时，要求政审的奖项全部排除，助学金类仍可申请。"""
        items = eligibility.evaluate(_profile(political_review=False))
        ok = {i.name for i in items if i.eligible}
        # 要求政审的项目必须被排除
        assert "国家奖学金" not in ok
        assert "国家励志奖学金" not in ok
        assert "校级一等奖学金" not in ok
        # 助学金 / 勤工助学不要求政审，仍应保留
        assert "国家助学金" in ok
        assert "勤工助学" in ok

    def test_gpa_gate(self):
        """GPA 低于门槛时，对应项目被排除且给出依据。"""
        items = eligibility.evaluate(_profile(gpa=2.9))
        item = next(i for i in items if i.name == "国家奖学金")
        assert item.eligible is False
        assert any("学业成绩" in r for r in item.reasons)


class TestFormRenders:
    """界面表单必须真的提供这些输入控件（否则又是只读）。"""

    def test_form_has_edit_controls(self):
        """编辑表单包含困难认定复选框与 GPA/排名输入框。"""
        from streamlit.testing.v1 import AppTest

        app_path = str(Path(__file__).resolve().parent.parent / "app.py")
        at = AppTest.from_file(app_path, default_timeout=180)
        at.run()
        btn = [b for b in at.button if b.key == "profile_edit_btn"]
        assert btn, "找不到「编辑档案」按钮"
        btn[0].click().run()

        labels = [cb.label for cb in at.checkbox]
        assert "家庭经济困难认定" in labels
        assert "政审合格" in labels

        num_labels = [n.label for n in at.number_input]
        assert "GPA" in num_labels
        assert any("专业排名" in lb for lb in num_labels)
        assert "已修学分" in num_labels
