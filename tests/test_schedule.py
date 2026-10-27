"""确定性冲突算法测试。

这些用例覆盖《期末考查要求》"能指认关键代码"所指向的核心算法。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.schedule import (  # noqa: E402
    detect_conflicts,
    find_conflict_pair,
    has_conflict,
    weekly_contact_hours,
)
from core.schemas import Course, Timeslot  # noqa: E402


def slot(weekday: int, start: int, end: int, room: str = "") -> Timeslot:
    """构造时段。"""
    return Timeslot(weekday=weekday, start_period=start, end_period=end, room=room)


def course(cid: int, code: str, slots: list[Timeslot], credits: float = 3.0) -> Course:
    """构造课程。"""
    return Course(id=cid, code=code, name=f"课程{code}", credits=credits, slots=slots)


class TestTimeslotOverlap:
    """时段重叠判定。"""

    def test_same_day_overlapping_periods(self):
        """同一天、节次区间相交 -> 重叠。"""
        a = slot(2, 1, 3)
        b = slot(2, 2, 4)
        assert a.overlaps(b) is True

    def test_same_day_adjacent_periods_overlap(self):
        """边界相接（1-2 与 2-3）算冲突，因为第 2 节是同一节课。"""
        a = slot(2, 1, 2)
        b = slot(2, 2, 3)
        assert a.overlaps(b) is True

    def test_same_day_disjoint_periods(self):
        """同一天、节次区间不相交 -> 不重叠。"""
        a = slot(2, 1, 2)
        b = slot(2, 3, 4)
        assert a.overlaps(b) is False

    def test_different_days_never_overlap(self):
        """不同天永不冲突，即使节次完全相同。"""
        a = slot(2, 1, 2)
        b = slot(3, 1, 2)
        assert a.overlaps(b) is False

    def test_containment(self):
        """一方完全包含另一方 -> 重叠。"""
        a = slot(2, 1, 6)
        b = slot(2, 3, 4)
        assert a.overlaps(b) is True


class TestConflictDetection:
    """冲突检测。"""

    def test_detect_single_conflict(self):
        """检出唯一冲突对。"""
        a = course(1, "A", [slot(2, 1, 2)])
        b = course(2, "B", [slot(2, 1, 2)])
        conflicts = detect_conflicts([a, b])
        assert len(conflicts) == 1
        assert conflicts[0].course_a.startswith("A")
        assert conflicts[0].course_b.startswith("B")

    def test_no_conflict(self):
        """无冲突时返回空列表。"""
        a = course(1, "A", [slot(2, 1, 2)])
        b = course(2, "B", [slot(4, 3, 4)])
        assert detect_conflicts([a, b]) == []
        assert has_conflict([a, b]) is False

    def test_multi_slot_detects_second_slot_conflict(self):
        """多时段课程要能检出非第一个时段的冲突。"""
        a = course(1, "A", [slot(2, 1, 2), slot(4, 3, 4)])
        b = course(2, "B", [slot(4, 3, 4)])
        conflicts = detect_conflicts([a, b])
        assert len(conflicts) == 1
        assert "周四" in conflicts[0].slots[0]

    def test_three_courses_multiple_conflicts(self):
        """三门课两两冲突时应检出两对。"""
        a = course(1, "A", [slot(2, 1, 2)])
        b = course(2, "B", [slot(2, 1, 2)])
        c = course(3, "C", [slot(2, 1, 2)])
        assert len(detect_conflicts([a, b, c])) == 3

    def test_conflict_message_includes_day_and_period(self):
        """冲突描述应包含星期与节次，便于直接展示给用户。"""
        a = course(1, "A", [slot(3, 5, 6)])
        b = course(2, "B", [slot(3, 5, 6)])
        pair = find_conflict_pair(a, b)
        assert pair is not None
        assert "周三" in pair.slots[0]
        assert "5-6" in pair.slots[0]


class TestWeeklyContactHours:
    """每周课时统计。"""

    def test_counts_all_slots(self):
        """两门课共 4 课时。"""
        a = course(1, "A", [slot(2, 1, 2)], credits=2)
        b = course(2, "B", [slot(4, 3, 4)], credits=2)
        assert weekly_contact_hours([a, b]) == 4.0

    def test_empty_list(self):
        """空课程列表课时为 0。"""
        assert weekly_contact_hours([]) == 0.0
