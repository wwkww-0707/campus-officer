"""当前学生机制测试。

背景：用户改学号后，界面与工具仍读旧的默认学号，导致"改了专业但学习方案没变"。
本测试固化修复后的行为：保存档案即成为当前学生，所有读取跟随。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import db  # noqa: E402
from core.schemas import StudentProfile  # noqa: E402


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    """把 db 模块指向临时数据库并初始化。"""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    return db


def _profile(sid: str, dept: str = "计算机科学与技术", grade: str = "2024级") -> StudentProfile:
    """构造一份档案。"""
    return StudentProfile(
        student_id=sid,
        name="测试同学",
        grade=grade,
        department=dept,
    )


class TestCurrentStudent:
    """当前学生指针。"""

    def test_save_sets_current(self, temp_db):
        """保存档案后，该学号成为当前学生。"""
        temp_db.save_profile(_profile("2025016"))
        assert temp_db.get_current_student_id() == "2025016"

    def test_changing_sid_follows_new(self, temp_db):
        """改学号后，当前学生跟随新学号（核心 bug 修复）。"""
        temp_db.save_profile(_profile("20260001", "大数据"))
        assert temp_db.get_current_student_id() == "20260001"

        # 用户把学号改成自己的
        temp_db.save_profile(_profile("2025016", "计算机科学与技术"))
        assert temp_db.get_current_student_id() == "2025016"

        loaded = temp_db.load_profile("current")
        assert loaded is not None
        assert loaded.student_id == "2025016"
        assert loaded.department == "计算机科学与技术"

    def test_changing_only_department(self, temp_db):
        """只改专业时，同一学号的档案被更新。"""
        temp_db.save_profile(_profile("2025016", "计算机科学与技术"))
        temp_db.save_profile(_profile("2025016", "护理学"))
        loaded = temp_db.load_profile("current")
        assert loaded.department == "护理学"

    def test_resolve_student_id(self, temp_db):
        """'current' 与空值解析为真实学号。"""
        temp_db.save_profile(_profile("2025016"))
        assert temp_db.resolve_student_id("current") == "2025016"
        assert temp_db.resolve_student_id("") == "2025016"
        assert temp_db.resolve_student_id(None) == "2025016"
        # 明确学号原样返回
        assert temp_db.resolve_student_id("20260001") == "20260001"

    def test_reject_empty_sid(self, temp_db):
        """拒绝空学号或占位符，避免污染主键。"""
        with pytest.raises(ValueError):
            temp_db.save_profile(_profile(""))
        with pytest.raises(ValueError):
            temp_db.save_profile(_profile("current"))

    def test_fallback_prefers_non_default_sid(self, temp_db):
        """无指针时，优先选非默认种子学号的档案。"""
        # 清掉 meta，模拟旧数据
        with temp_db.get_conn() as conn:
            conn.execute("DELETE FROM meta")
        temp_db.save_profile(_profile("20260001", "大数据"))
        with temp_db.get_conn() as conn:
            conn.execute("DELETE FROM meta")  # 再次清掉指针
        temp_db.save_profile(_profile("2025016", "计算机科学与技术"))
        with temp_db.get_conn() as conn:
            conn.execute("DELETE FROM meta")

        # 回退应选非默认学号 2025016
        assert temp_db.get_current_student_id() == "2025016"

    def test_enrollments_follow_current(self, temp_db):
        """选课数据也跟随当前学生。"""
        temp_db.update_courses() if hasattr(temp_db, "update_courses") else None
        temp_db.save_profile(_profile("2025016"))
        # 未选课时为空列表，但不应抛错
        assert temp_db.get_enrollments("current") == []
