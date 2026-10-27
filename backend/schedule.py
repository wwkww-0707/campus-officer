"""确定性课表冲突算法。

这是本项目**最关键的一段自研代码**，也是"LLM 规划与规则裁决分离"创新点的落点。

设计要点：
1. 冲突判定完全由区间求交完成，**不依赖大模型**，结论可复现、可在答辩时逐步讲解；
2. 采用闭区间模型 [start_period, end_period]，两门课在同一天的节次区间有交集即冲突；
3. 输出结构化冲突对（哪两门课、重叠在哪几个时段），便于 Agent 生成可解释反馈。
"""

from __future__ import annotations

from itertools import combinations
from typing import Iterable, Sequence

from core.schemas import ConflictPair, Course, Timeslot


def slots_overlap(a: Timeslot, b: Timeslot) -> bool:
    """两个时段是否重叠。同一天且节次闭区间有交集即为重叠。"""
    if a.weekday != b.weekday:
        return False
    return a.start_period <= b.end_period and b.start_period <= a.end_period


def _overlap_slots(a: Timeslot, b: Timeslot) -> Timeslot:
    """返回两时段的实际重叠片段。"""
    start = max(a.start_period, b.start_period)
    end = min(a.end_period, b.end_period)
    return Timeslot(
        weekday=a.weekday,
        start_period=start,
        end_period=end,
        weeks=a.weeks if a.weeks == b.weeks else f"{a.weeks}/{b.weeks}",
        room=a.room,
    )


def find_conflict_pair(a: Course, b: Course) -> ConflictPair | None:
    """检查两门课是否冲突；有冲突则返回冲突对，否则返回 None。"""
    overlaps: list[Timeslot] = []
    for sa in a.slots:
        for sb in b.slots:
            if slots_overlap(sa, sb):
                overlaps.append(_overlap_slots(sa, sb))
    if not overlaps:
        return None
    return ConflictPair(
        course_a=f"{a.code} {a.name}",
        course_b=f"{b.code} {b.name}",
        slots=[s.describe() for s in overlaps],
    )


def detect_conflicts(courses: Sequence[Course]) -> list[ConflictPair]:
    """在课程集合中两两检测冲突，返回全部冲突对。

    使用 itertools.combinations 保证每对课程只比较一次，
    课程数量为 n 时复杂度为 O(n^2 * slots^2)，对教学规模完全够用。
    """
    conflicts: list[ConflictPair] = []
    for ca, cb in combinations(courses, 2):
        pair = find_conflict_pair(ca, cb)
        if pair is not None:
            conflicts.append(pair)
    return conflicts


def has_conflict(courses: Sequence[Course]) -> bool:
    """课程集合中是否存在任意冲突。"""
    return bool(detect_conflicts(courses))


def filter_by_conflict(candidate: Course, existing: Iterable[Course]) -> bool:
    """判断候选课程加入已有课程后是否会产生冲突。"""
    return any(find_conflict_pair(candidate, other) is not None for other in existing)


def merge_slot_lists(course_groups: Sequence[Sequence[Course]]) -> list[Timeslot]:
    """把多门课的时段合并为一个列表（用于总学分/时间占用统计）。"""
    merged: list[Timeslot] = []
    for group in course_groups:
        for course in group:
            merged.extend(course.slots)
    return merged


def weekly_contact_hours(courses: Sequence[Course]) -> float:
    """估算每周总课时（每节按 1 小时计），用于学习时间预算。"""
    total = 0
    for course in courses:
        for slot in course.slots:
            total += slot.end_period - slot.start_period + 1
    return float(total)
