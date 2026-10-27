"""资格判定与产出物安全测试。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.eligibility import evaluate  # noqa: E402
from core.artifacts import ArtifactWriter, sanitize_filename  # noqa: E402
from core.schemas import StudentProfile  # noqa: E402


@pytest.fixture()
def profile() -> StudentProfile:
    """默认演示档案。"""
    return StudentProfile(
        student_id="20260001",
        name="示例同学",
        grade="2024级",
        department="计算机科学与技术",
        gpa=3.6,
        credit_earned=68.0,
        credit_required=165.0,
        class_rank_ratio=0.08,
        family_difficulty=True,
        political_review=True,
    )


class TestEligibility:
    """资格判定。"""

    def test_high_performer_gets_scholarships(self, profile):
        """GPA 与排名达标者应获得国家奖学金与励志奖学金。"""
        items = {i.scholarship_id: i for i in evaluate(profile)}
        assert items["national_scholarship"].eligible is True
        assert items["national_encouragement"].eligible is True

    def test_reasons_are_cited(self, profile):
        """每条判定都必须给出依据。"""
        items = {i.scholarship_id: i for i in evaluate(profile)}
        assert items["national_scholarship"].reasons
        assert items["national_scholarship"].document_ref == "财教〔2024〕38号"

    def test_low_gpa_excluded(self, profile):
        """GPA 不达标应被排除。"""
        profile.gpa = 3.0
        items = {i.scholarship_id: i for i in evaluate(profile)}
        assert items["school_first"].eligible is False
        assert any("低于要求" in r for r in items["school_first"].reasons)

    def test_rank_not_provided_is_not_judged(self, profile):
        """排名未提供时不应误判为不符合，而应说明需院系核定。"""
        profile.class_rank_ratio = None
        profile.gpa = 3.0  # 排除校级一等，使其依赖排名项
        items = {i.scholarship_id: i for i in evaluate(profile)}
        national = items["national_scholarship"]
        assert any("排名由院系核定" in r for r in national.reasons)

    def test_every_item_has_disclaimer(self, profile):
        """每条结论都要带合规提示。"""
        for item in evaluate(profile):
            assert "官方最新文件为准" in item.disclaimer

    def test_eligible_item_lists_materials(self, profile):
        """符合条件时给出待交材料清单。"""
        items = {i.scholarship_id: i for i in evaluate(profile)}
        assert items["national_scholarship"].missing_materials


class TestArtifactSafety:
    """产出物写入安全。"""

    def test_sanitize_removes_path_separators(self):
        """文件名中的路径分隔符被清洗。"""
        assert "/" not in sanitize_filename("../../etc/passwd")
        assert ".." not in sanitize_filename("../../etc/passwd")

    def test_sanitize_handles_reserved_names(self):
        """Windows 保留设备名被转义。"""
        assert sanitize_filename("CON").upper() != "CON"

    def test_empty_name_gets_default(self):
        """空文件名走默认值。"""
        assert sanitize_filename("") == "output"

    def test_write_inside_output_dir(self, tmp_path):
        """正常写入落在输出目录内。"""
        writer = ArtifactWriter(output_dir=tmp_path)
        path = writer.write_text("测试.md", "内容")
        assert path.parent == tmp_path.resolve()

    def test_path_traversal_rejected(self, tmp_path):
        """路径穿越被拒绝。"""
        writer = ArtifactWriter(output_dir=tmp_path)
        with pytest.raises(PermissionError):
            writer.write_text("../evil.md", "恶意内容")

    def test_absolute_path_rejected(self, tmp_path):
        """绝对路径被拒绝。"""
        writer = ArtifactWriter(output_dir=tmp_path)
        with pytest.raises(PermissionError):
            writer.write_text("C:\\Windows\\evil.md", "恶意内容")
