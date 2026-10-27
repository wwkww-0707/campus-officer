"""子代理：课程组合求解 与 学习时间预算。

这两个"子代理"是本项目的加分项，它们的共同特点是**承担确定性计算任务**，
把"算"从模型手里拿走，让大模型只负责理解意图与解释结论。

### 子代理 1：课程组合求解器（CourseCombinationAgent）
用**回溯搜索**在多重约束下求课程组合：
- 约束 1：先修课程必须已在已选列表中（或在本次组合内）；
- 约束 2：组合内部两两不冲突（闭区间时段求交）；
- 约束 3：与已选课程不冲突；
- 约束 4：总学分不超过上限；
- 约束 5：排除满员课程；
- 约束 6（软约束）：尽量满足用户的"避开某天""偏好某方向"。

评分函数：学分利用率优先，其次课程评分（rating）之和，最后平均难度贴近目标。

### 子代理 2：学习时间预算器（StudyBudgetAgent）
基于选定课程的每周课表，算出每周可用时间，并按
"课程学习 / 竞赛训练 / 考研复习 / 休息机动"四象限分配，产出可执行的周计划。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from backend.schedule import detect_conflicts, weekly_contact_hours
from core.schemas import Course, Timeslot


# ---------------------------------------------------------------------------
# 子代理 1：课程组合求解
# ---------------------------------------------------------------------------

@dataclass
class SolveRequest:
    """求解请求。"""

    student_id: str = "current"
    target_count: int = 3
    max_credits: float = 20.0
    min_credits: float = 0.0
    avoid_weekdays: list[int] = field(default_factory=list)
    prefer_keywords: list[str] = field(default_factory=list)
    department: str = ""
    category: str = ""
    include_full: bool = False


@dataclass
class SolveResult:
    """求解结果。"""

    courses: list[Course]
    total_credits: float
    rejected: list[dict[str, str]]
    searched: int
    constraints: dict[str, Any]
    message: str


class CourseCombinationAgent:
    """课程组合求解子代理。"""

    name = "course_combination_agent"
    description = "在先修、无冲突、学分上限等约束下，用回溯搜索求解课程组合"

    def solve(
        self,
        candidates: Sequence[Course],
        enrolled: Sequence[Course],
        req: SolveRequest,
    ) -> SolveResult:
        """执行回溯搜索。

        搜索策略：按"评分/学分比"降序排列候选（贪心排序 + 回溯），
        依次尝试加入，冲突则回退到上一个分支。这在教学规模（数十门课）下
        既快又能得到高质量解。
        """
        enrolled_codes = {c.code for c in enrolled}
        enrolled_ids = {c.code for c in enrolled}

        # 过滤：满员、已选、学分区间、避开星期
        pool: list[Course] = []
        rejected: list[dict[str, str]] = []

        for c in candidates:
            if c.code in enrolled_ids:
                rejected.append({"code": c.code, "name": c.name, "reason": "已在已选列表中"})
                continue
            if c.full and not req.include_full:
                rejected.append(
                    {"code": c.code, "name": c.name, "reason": f"名额已满({c.enrolled}/{c.capacity})"}
                )
                continue
            if c.credits < req.min_credits:
                rejected.append(
                    {"code": c.code, "name": c.name, "reason": f"学分 {c.credits} 低于下限 {req.min_credits}"}
                )
                continue
            if req.avoid_weekdays and any(s.weekday in req.avoid_weekdays for s in c.slots):
                hit_days = sorted({s.weekday for s in c.slots if s.weekday in req.avoid_weekdays})
                day_text = "、".join(
                    f"周{'一二三四五六日'[d - 1]}" for d in hit_days
                )
                rejected.append(
                    {
                        "code": c.code,
                        "name": c.name,
                        "reason": f"与要求避开{day_text}冲突",
                    }
                )
                continue
            pool.append(c)

        # 贪心排序：先修依赖优先 + 评分/学分比高者优先
        def sort_key(c: Course) -> tuple:
            has_prereq = 1 if c.prerequisites else 0
            ratio = (c.rating / c.credits) if c.credits else 0
            return (-has_prereq, -ratio, -c.rating)

        pool.sort(key=sort_key)

        base_credits = sum(c.credits for c in enrolled)

        best: list[Course] = []
        best_score = float("-inf")
        visited = 0

        def score_of(combo: Sequence[Course]) -> float:
            """组合评分：学分利用率 + 课程质量 + 偏好匹配。"""
            if not combo:
                return float("-inf")
            total = sum(c.credits for c in combo)
            # 学分利用率（贴近上限但不超）
            utilization = total / max(req.max_credits, 1)
            quality = sum(c.rating for c in combo) / len(combo)
            preference = 0.0
            if req.prefer_keywords:
                hit = sum(
                    1
                    for c in combo
                    if any(k in c.name or k in c.tags for k in req.prefer_keywords)
                )
                preference = hit / len(combo)
            return utilization * 2.0 + quality + preference * 1.5

        def dfs(index: int, chosen: list[Course]) -> None:
            """回溯搜索。"""
            nonlocal best, best_score, visited
            visited += 1
            if visited > 20000:  # 安全阀
                return

            if len(chosen) == req.target_count:
                sc = score_of(chosen)
                if sc > best_score:
                    best_score = sc
                    best = list(chosen)
                return

            # 已达学分上限则不再加深
            if sum(c.credits for c in chosen) >= req.max_credits:
                return

            for i in range(index, len(pool)):
                candidate = pool[i]
                if sum(c.credits for c in chosen) + candidate.credits > req.max_credits:
                    continue
                # 约束：先修课程必须已在已选或本组合中
                if candidate.prerequisites:
                    have = set(enrolled_codes) | {c.code for c in chosen}
                    if not all(p in have for p in candidate.prerequisites):
                        continue
                # 约束：与已选及本组合均不冲突
                if detect_conflicts(list(enrolled) + chosen + [candidate]):
                    continue
                chosen.append(candidate)
                dfs(i + 1, chosen)
                chosen.pop()

        dfs(0, [])

        total = sum(c.credits for c in best)
        if not best:
            message = (
                f"在给定约束下未能找到满足 {req.target_count} 门课程的组合。"
                "可能原因：学分上限过低、避开的时间要求过严，或先修条件不满足。"
            )
        else:
            names = "、".join(f"《{c.name}》" for c in best)
            message = (
                f"找到无冲突课程组合（{len(best)} 门，合计 {total} 学分）：{names}。"
                "该组合已通过先修、时段冲突、学分上限与容量四重校验。"
            )

        return SolveResult(
            courses=list(best),
            total_credits=total,
            rejected=rejected,
            searched=visited,
            constraints={
                "目标门数": req.target_count,
                "学分上限": req.max_credits,
                "避开星期": [f"周{'一二三四五六日'[d - 1]}" for d in req.avoid_weekdays],
                "偏好方向": req.prefer_keywords,
                "已选学分": base_credits,
            },
            message=message,
        )


# ---------------------------------------------------------------------------
# 子代理 2：学习时间预算
# ---------------------------------------------------------------------------

@dataclass
class BudgetRequest:
    """预算请求。"""

    free_hours_per_week: float = 22.0
    competition_hours: float = 0.0
    exam_prep_hours: float = 0.0


@dataclass
class BudgetPlan:
    """预算计划。"""

    contact_hours: float
    free_hours: float
    allocation: dict[str, float]
    weekly_schedule: list[dict[str, Any]]
    milestones: list[str]
    risks: list[str]


class StudyBudgetAgent:
    """学习时间预算子代理。"""

    name = "study_budget_agent"
    description = "基于选定课程的周课表，计算每周可用时间并分配学习/竞赛/考研/休息四象限"

    def plan(
        self,
        courses: Sequence[Course],
        req: BudgetRequest,
    ) -> BudgetPlan:
        """计算时间预算并排布每周计划。"""
        contact = weekly_contact_hours(courses)
        # 课表占用的是"在校时间"，课外学习时间为用户给定的自由时间减去作业量
        homework = sum(c.hours_per_week for c in courses) * 0.35  # 作业量按课外学时 35% 估算
        free = max(0.0, req.free_hours_per_week - homework)

        # 分配比例：优先满足显式需求，其余给课程学习与休息
        comp = min(req.competition_hours, free * 0.5)
        exam = min(req.exam_prep_hours, free * 0.5)
        rest = free * 0.2
        study = max(0.0, free - comp - exam - rest)

        allocation = {
            "课程复习与作业": round(study, 1),
            "竞赛/项目训练": round(comp, 1),
            "考研复习": round(exam, 1),
            "休息与机动": round(rest, 1),
        }

        # 排布：优先安排高难度课程的复习在其次日
        weekly: list[dict[str, Any]] = []
        sorted_courses = sorted(courses, key=lambda c: -c.difficulty)
        for course in sorted_courses:
            for slot in course.slots:
                day_name = "一二三四五六日"[slot.weekday - 1]
                weekly.append(
                    {
                        "day": f"周{day_name}",
                        "period": f"{slot.start_period}-{slot.end_period}节",
                        "course": f"{course.code}《{course.name}》",
                        "focus": (
                            "重点：课后习题 + 概念梳理"
                            if course.difficulty >= 4
                            else "常规：课堂笔记整理"
                        ),
                    }
                )
        weekly.sort(key=lambda x: (["一", "二", "三", "四", "五", "六", "日"].index(x["day"][1]), x["period"]))

        milestones = [
            "第 1-4 周：完成各课程第一章与基础习题，建立知识框架",
            "第 5-8 周：进入核心章节，配合往年真题/期末题库做阶段性检测",
            "第 9-12 周：专题强化与错题复盘，压缩低价值重复劳动",
            "第 13-16 周：模拟演练与查漏补缺，按考试顺序安排冲刺",
        ]

        risks: list[str] = []
        if free < 6:
            risks.append("每周可用课外时间不足 6 小时，建议减少 1 门课程或压缩竞赛投入")
        if contact > 24:
            risks.append("每周课表占用超过 24 课时，注意留出消化时间，避免学习效率下降")
        if sum(c.difficulty for c in courses) / max(len(courses), 1) >= 4.0:
            risks.append("所选课程平均难度较高，建议明确优先级，避免多线并进导致全线失守")
        if not risks:
            risks.append("当前时间安排较为均衡，按周计划执行并每周复盘一次即可")

        return BudgetPlan(
            contact_hours=contact,
            free_hours=round(free, 1),
            allocation=allocation,
            weekly_schedule=weekly,
            milestones=milestones,
            risks=risks,
        )


# 单例
course_combination_agent = CourseCombinationAgent()
study_budget_agent = StudyBudgetAgent()
