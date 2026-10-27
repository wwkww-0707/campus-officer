"""种子数据：虚构课程库与学生档案。

⚠️ 本文件所有数据均为**教学演示用虚构样例**：
- 学号、姓名、教师姓名均为虚构，不对应任何真实个人；
- 课程名为通用教学科目名称，仅用于演示选课与冲突逻辑；
- 奖助学金规则指向公开真实文件（教育部相关文件），金额为示例说明值。

首次运行时会自动建库并灌入种子数据；重复运行不会重复插入。
"""

from __future__ import annotations

import json

from backend.db import get_conn, init_db, save_profile
from core.schemas import StudentProfile, Timeslot


def _slot(weekday: int, start: int, end: int, weeks: str = "1-16周", room: str = "") -> dict:
    """构造时段字典。"""
    return {
        "weekday": weekday,
        "start_period": start,
        "end_period": end,
        "weeks": weeks,
        "room": room,
    }


# 课程种子数据
# 课表刻意设计成：CS201 与 CS305 时段重叠、CS210 与 MA201 时段重叠，
# 用于演示确定性冲突检测。
SEED_COURSES: list[dict] = [
    {
        "code": "CS201",
        "name": "数据结构与算法",
        "credits": 4.0,
        "teacher": "示例教师甲",
        "department": "计算机科学与技术",
        "category": "必修",
        "capacity": 120,
        "enrolled": 96,
        "slots": [_slot(2, 1, 2, "1-16周", "教三楼A201"), _slot(4, 3, 4, "1-16周", "教三楼A201")],
        "prerequisites": [],
        "difficulty": 4,
        "rating": 4.3,
        "tags": ["核心课", "考研", "算法"],
        "hours_per_week": 6.0,
    },
    {
        "code": "CS210",
        "name": "计算机网络",
        "credits": 3.0,
        "teacher": "示例教师乙",
        "department": "计算机科学与技术",
        "category": "必修",
        "capacity": 100,
        "enrolled": 88,
        "slots": [_slot(1, 3, 4, "1-16周", "实验楼B305"), _slot(3, 5, 6, "1-16周", "实验楼B305")],
        "prerequisites": [],
        "difficulty": 3,
        "rating": 4.0,
        "tags": ["核心课", "考研", "计算机"],
        "hours_per_week": 5.0,
    },
    {
        "code": "CS305",
        "name": "人工智能导论",
        "credits": 3.0,
        "teacher": "示例教师丙",
        "department": "计算机科学与技术",
        "category": "选修",
        "capacity": 60,
        "enrolled": 58,
        "slots": [_slot(2, 1, 2, "1-16周", "教三楼A305")],  # 与 CS201 周二 1-2 节冲突
        "prerequisites": ["CS201"],
        "difficulty": 3,
        "rating": 4.5,
        "tags": ["AI", "热度高", "考研"],
        "hours_per_week": 4.0,
    },
    {
        "code": "CS320",
        "name": "数据库系统原理",
        "credits": 3.0,
        "teacher": "示例教师丁",
        "department": "计算机科学与技术",
        "category": "选修",
        "capacity": 80,
        "enrolled": 61,
        "slots": [_slot(3, 1, 2, "1-16周", "实验楼C402"), _slot(5, 3, 4, "1-16周", "实验楼C402")],
        "prerequisites": [],
        "difficulty": 3,
        "rating": 4.2,
        "tags": ["后端", "考研", "工程"],
        "hours_per_week": 5.0,
    },
    {
        "code": "CS340",
        "name": "机器学习基础",
        "credits": 3.0,
        "teacher": "示例教师丙",
        "department": "计算机科学与技术",
        "category": "选修",
        "capacity": 50,
        "enrolled": 50,  # 已满员，用于演示名额校验
        "slots": [_slot(4, 1, 2, "1-16周", "实验楼B201")],
        "prerequisites": ["CS201", "MA201"],
        "difficulty": 4,
        "rating": 4.4,
        "tags": ["AI", "满员", "热门"],
        "hours_per_week": 5.0,
    },
    {
        "code": "CS360",
        "name": "软件工程实践",
        "credits": 2.0,
        "teacher": "示例教师戊",
        "department": "计算机科学与技术",
        "category": "选修",
        "capacity": 45,
        "enrolled": 30,
        "slots": [_slot(5, 7, 8, "1-16周", "实验楼D101")],
        "prerequisites": ["CS210"],
        "difficulty": 2,
        "rating": 3.8,
        "tags": ["团队", "项目", "工程"],
        "hours_per_week": 3.0,
    },
    {
        "code": "MA201",
        "name": "高等数学（下）",
        "credits": 5.0,
        "teacher": "示例教师己",
        "department": "公共基础",
        "category": "必修",
        "capacity": 200,
        "enrolled": 175,
        "slots": [_slot(1, 1, 2, "1-16周", "教一楼101"), _slot(3, 3, 4, "1-16周", "教一楼101")],
        "prerequisites": [],
        "difficulty": 5,
        "rating": 3.6,
        "tags": ["公共基础", "难"],
        "hours_per_week": 8.0,
    },
    {
        "code": "MA210",
        "name": "线性代数",
        "credits": 3.0,
        "teacher": "示例教师庚",
        "department": "公共基础",
        "category": "必修",
        "capacity": 180,
        "enrolled": 160,
        "slots": [_slot(4, 5, 6, "1-16周", "教一楼203")],
        "prerequisites": [],
        "difficulty": 4,
        "rating": 3.9,
        "tags": ["公共基础", "数学"],
        "hours_per_week": 5.0,
    },
    {
        "code": "EN101",
        "name": "大学英语（学术写作）",
        "credits": 2.0,
        "teacher": "示例教师辛",
        "department": "公共基础",
        "category": "必修",
        "capacity": 60,
        "enrolled": 55,
        "slots": [_slot(2, 3, 4, "1-16周", "文科楼205")],
        "prerequisites": [],
        "difficulty": 2,
        "rating": 3.5,
        "tags": ["外语", "写作"],
        "hours_per_week": 3.0,
    },
    {
        "code": "HU210",
        "name": "中国近现代史纲要",
        "credits": 2.0,
        "teacher": "示例教师壬",
        "department": "通识教育",
        "category": "通识",
        "capacity": 120,
        "enrolled": 100,
        "slots": [_slot(1, 5, 6, "1-16周", "文科楼108")],
        "prerequisites": [],
        "difficulty": 1,
        "rating": 3.3,
        "tags": ["通识", "思政"],
        "hours_per_week": 3.0,
    },
    {
        "code": "CS380",
        "name": "信息安全导论",
        "credits": 2.0,
        "teacher": "示例教师癸",
        "department": "计算机科学与技术",
        "category": "选修",
        "capacity": 40,
        "enrolled": 22,
        "slots": [_slot(5, 1, 2, "1-16周", "实验楼B102")],
        "prerequisites": [],
        "difficulty": 3,
        "rating": 4.1,
        "tags": ["安全", "科普"],
        "hours_per_week": 3.0,
    },
    {
        "code": "PE100",
        "name": "体育（篮球）",
        "credits": 1.0,
        "teacher": "示例教师子",
        "department": "体育部",
        "category": "通识",
        "capacity": 60,
        "enrolled": 40,
        "slots": [_slot(4, 9, 10, "1-16周", "体育馆2号场")],
        "prerequisites": [],
        "difficulty": 1,
        "rating": 4.0,
        "tags": ["体育", "通识"],
        "hours_per_week": 2.0,
    },
]


def seed_courses() -> int:
    """灌入课程种子数据，返回新增条数。"""
    init_db()
    inserted = 0
    with get_conn() as conn:
        for c in SEED_COURSES:
            exists = conn.execute(
                "SELECT 1 FROM courses WHERE code = ?", (c["code"],)
            ).fetchone()
            if exists:
                continue
            conn.execute(
                """
                INSERT INTO courses (code, name, credits, teacher, department, category,
                                     capacity, enrolled, slots_json, prerequisites_json,
                                     difficulty, rating, tags_json, hours_per_week)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    c["code"], c["name"], c["credits"], c["teacher"], c["department"],
                    c["category"], c["capacity"], c["enrolled"],
                    json.dumps(c["slots"], ensure_ascii=False),
                    json.dumps(c["prerequisites"], ensure_ascii=False),
                    c["difficulty"], c["rating"],
                    json.dumps(c["tags"], ensure_ascii=False),
                    c["hours_per_week"],
                ),
            )
            inserted += 1
    return inserted


def seed_profile() -> None:
    """灌入默认学生档案（虚构样例）。

    仅在**当前无任何档案**时灌入。否则用户改过的学号/专业/年级
    会在每次重启后端时被重置回默认值。
    """
    from backend.db import get_current_student_id

    if get_current_student_id():
        return  # 已有档案，保留用户修改

    profile = StudentProfile(
        student_id="20260001",
        name="示例同学",
        school="杉达学院",
        grade="2024级",
        department="计算机科学与技术",
        gpa=3.6,
        credit_earned=68.0,
        credit_required=165.0,
        class_rank_ratio=0.08,
        family_difficulty=True,
        political_review=True,
        has_dormitory=True,
        avg_weekly_free_hours=22.0,
        interests=["人工智能", "系统"],
        enrolled_course_ids=[],
        notes="演示用虚构档案，所有字段均为样例数据",
    )
    save_profile(profile)


def seed_all() -> dict[str, int]:
    """执行全部种子初始化。"""
    n = seed_courses()
    seed_profile()
    return {"courses_inserted": n}
