"""SQLite 数据访问层。

采用标准库 sqlite3，无需 ORM，依赖最少、启动最快。
所有数据均为**教学演示用虚构样例**，不含任何真实个人信息。
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from core.schemas import Course, Enrollment, StudentProfile, Timeslot

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "campus.db"


def _row_to_course(row: sqlite3.Row) -> Course:
    """数据库行 -> Course 模型。"""
    slots = [Timeslot(**s) for s in json.loads(row["slots_json"])]
    return Course(
        id=row["id"],
        code=row["code"],
        name=row["name"],
        credits=row["credits"],
        teacher=row["teacher"],
        department=row["department"],
        category=row["category"],
        capacity=row["capacity"],
        enrolled=row["enrolled"],
        slots=slots,
        prerequisites=json.loads(row["prerequisites_json"]),
        difficulty=row["difficulty"],
        rating=row["rating"],
        tags=json.loads(row["tags_json"]),
        hours_per_week=row["hours_per_week"],
    )


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """获取数据库连接（自动提交/回滚）。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """建表（幂等）。"""
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS courses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                credits REAL NOT NULL,
                teacher TEXT DEFAULT '',
                department TEXT DEFAULT '',
                category TEXT DEFAULT '选修',
                capacity INTEGER DEFAULT 30,
                enrolled INTEGER DEFAULT 0,
                slots_json TEXT DEFAULT '[]',
                prerequisites_json TEXT DEFAULT '[]',
                difficulty INTEGER DEFAULT 3,
                rating REAL DEFAULT 3.0,
                tags_json TEXT DEFAULT '[]',
                hours_per_week REAL DEFAULT 2.0
            );

            CREATE TABLE IF NOT EXISTS enrollments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id TEXT NOT NULL,
                course_id INTEGER NOT NULL,
                created_at TEXT DEFAULT '',
                UNIQUE(student_id, course_id)
            );

            CREATE TABLE IF NOT EXISTS profiles (
                student_id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL
            );

            -- 系统级键值配置（当前学生指针等）
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS announcements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                category TEXT DEFAULT '其他',
                content TEXT DEFAULT '',
                source TEXT DEFAULT '',
                event_date TEXT DEFAULT '',
                url TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );
            """
        )
        # 旧库迁移：补充历史版本缺失的列（已存在则忽略）
        for col, ddl in (
            ("event_date", "ALTER TABLE announcements ADD COLUMN event_date TEXT DEFAULT ''"),
            ("url", "ALTER TABLE announcements ADD COLUMN url TEXT DEFAULT ''"),
        ):
            try:
                conn.execute(ddl)
            except sqlite3.OperationalError:
                pass  # 列已存在


# ---------------------------------------------------------------------------
# 公告（学校新政策 / 活动，手动录入或官网同步）
# ---------------------------------------------------------------------------

def add_announcement(
    title: str,
    category: str = "其他",
    content: str = "",
    source: str = "",
    event_date: str = "",
    url: str = "",
    created_at: str | None = None,
) -> int:
    """新增一条公告，返回 id。

    Args:
        event_date: 活动类公告的日期（YYYY-MM-DD）。
        url: 原文链接，界面据此渲染「查看原文」跳转。
    """
    from datetime import datetime

    created_at = created_at or datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO announcements "
            "(title, category, content, source, event_date, url, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (title, category, content, source, event_date, url, created_at),
        )
        return int(cur.lastrowid)


def list_announcements() -> list[dict[str, Any]]:
    """列出全部公告。

    排序规则（「日程」页签按此展示，保证有效信息在前）：
    1. 有日期的活动在前（event_date 倒序）；
    2. 同日期条件下按类别优先级：通知 > 讲座 > 活动 > 新闻/其他；
    3. 最后按 id 倒序（新录入的在前）。
    """
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, title, category, content, source, event_date, url, created_at
            FROM announcements
            ORDER BY
                event_date DESC,
                CASE category
                    WHEN '通知' THEN 0
                    WHEN '讲座' THEN 1
                    WHEN '活动' THEN 2
                    WHEN '政策' THEN 3
                    ELSE 9
                END,
                id DESC
            """
        ).fetchall()
    return [dict(r) for r in rows]


def delete_announcement(ann_id: int) -> bool:
    """删除一条公告，返回是否删除成功。"""
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM announcements WHERE id = ?", (ann_id,))
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# 课程
# ---------------------------------------------------------------------------

def list_courses(
    department: str | None = None,
    category: str | None = None,
    min_credits: float | None = None,
    max_credits: float | None = None,
    keyword: str | None = None,
) -> list[Course]:
    """按条件查询课程目录。"""
    sql = "SELECT * FROM courses WHERE 1=1"
    params: list[Any] = []
    if department:
        sql += " AND department = ?"
        params.append(department)
    if category:
        sql += " AND category = ?"
        params.append(category)
    if min_credits is not None:
        sql += " AND credits >= ?"
        params.append(min_credits)
    if max_credits is not None:
        sql += " AND credits <= ?"
        params.append(max_credits)
    if keyword:
        sql += " AND (name LIKE ? OR code LIKE ? OR teacher LIKE ? OR tags_json LIKE ?)"
        like = f"%{keyword}%"
        params.extend([like, like, like, like])
    sql += " ORDER BY code"

    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_course(r) for r in rows]


def get_course(course_id: int) -> Course | None:
    """按 id 查询课程。"""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM courses WHERE id = ?", (course_id,)).fetchone()
    return _row_to_course(row) if row else None


def get_course_by_code(code: str) -> Course | None:
    """按课程代码查询。"""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM courses WHERE code = ?", (code,)).fetchone()
    return _row_to_course(row) if row else None


# ---------------------------------------------------------------------------
# 选课
# ---------------------------------------------------------------------------

def get_enrollments(student_id: str) -> list[Enrollment]:
    """查询学生已选课程。"""
    student_id = resolve_student_id(student_id)
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT e.id, e.student_id, e.course_id, e.created_at,
                   c.code, c.name, c.credits, c.slots_json
            FROM enrollments e JOIN courses c ON c.id = e.course_id
            WHERE e.student_id = ?
            ORDER BY e.id
            """,
            (student_id,),
        ).fetchall()
    return [
        Enrollment(
            id=r["id"],
            student_id=r["student_id"],
            course_id=r["course_id"],
            course_code=r["code"],
            course_name=r["name"],
            credits=r["credits"],
            slots=[Timeslot(**s) for s in json.loads(r["slots_json"])],
            created_at=r["created_at"],
        )
        for r in rows
    ]


def enroll(student_id: str, course_id: int) -> tuple[bool, str]:
    student_id = resolve_student_id(student_id)
    """提交选课。

    返回 (是否成功, 说明)。成功时课程已选人数 +1。
    """
    from datetime import datetime

    with get_conn() as conn:
        course = conn.execute("SELECT * FROM courses WHERE id = ?", (course_id,)).fetchone()
        if course is None:
            return False, f"课程 id={course_id} 不存在"
        if course["enrolled"] >= course["capacity"]:
            return False, f"《{course['name']}》名额已满（{course['enrolled']}/{course['capacity']}）"

        exists = conn.execute(
            "SELECT 1 FROM enrollments WHERE student_id = ? AND course_id = ?",
            (student_id, course_id),
        ).fetchone()
        if exists:
            return False, f"《{course['name']}》已在已选课程中，无需重复选课"

        conn.execute(
            "INSERT INTO enrollments (student_id, course_id, created_at) VALUES (?, ?, ?)",
            (student_id, course_id, datetime.now().isoformat(timespec="seconds")),
        )
        conn.execute(
            "UPDATE courses SET enrolled = enrolled + 1 WHERE id = ?", (course_id,)
        )
        return True, f"已成功选入《{course['name']}》（{course['code']}，{course['credits']} 学分）"


def unenroll(student_id: str, course_id: int) -> tuple[bool, str]:
    student_id = resolve_student_id(student_id)
    """退课。"""
    from datetime import datetime

    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT e.id, c.name FROM enrollments e
            JOIN courses c ON c.id = e.course_id
            WHERE e.student_id = ? AND e.course_id = ?
            """,
            (student_id, course_id),
        ).fetchone()
        if row is None:
            return False, "该课程不在你的已选列表中"
        conn.execute("DELETE FROM enrollments WHERE student_id = ? AND course_id = ?", (student_id, course_id))
        conn.execute("UPDATE courses SET enrolled = MAX(0, enrolled - 1) WHERE id = ?", (course_id,))
        return True, f"已退选《{row['name']}》"


# ---------------------------------------------------------------------------
# 学生档案
# ---------------------------------------------------------------------------

def save_profile(profile: StudentProfile) -> None:
    """保存学生档案，并把该学号设为**当前学生**。

    设为当前学生是关键：用户可能修改学号（如换成自己的学号），
    之后所有功能都应跟随新学号，而不是继续读旧的默认档案。
    """
    sid = (profile.student_id or "").strip()
    # 防御：拒绝把占位符当成真实学号写入主键
    if not sid or sid == "current":
        raise ValueError("保存档案时 student_id 必须填写真实学号（不能为空或 current）")
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO profiles (student_id, payload_json) VALUES (?, ?)",
            (sid, profile.model_dump_json()),
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('current_student_id', ?)",
            (sid,),
        )


def get_current_student_id() -> str:
    """返回当前学生学号。

    优先级：
    1. meta 中的指针（每次保存档案时更新）；
    2. 回退：优先选**非默认种子学号**（20260001）的档案，再按最近写入排序。
       理由：默认学号是系统灌入的种子，用户自己填的学号更可能是"当前学生"。
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'current_student_id'"
        ).fetchone()
        if row and row["value"]:
            # 校验指针指向的档案仍然存在
            exists = conn.execute(
                "SELECT 1 FROM profiles WHERE student_id = ?", (row["value"],)
            ).fetchone()
            if exists:
                return str(row["value"])
        # 回退：非默认学号优先，其次最近写入
        fallback = conn.execute(
            """
            SELECT student_id FROM profiles
            ORDER BY (student_id = '20260001') ASC, rowid DESC
            LIMIT 1
            """
        ).fetchone()
    return str(fallback["student_id"]) if fallback else ""


def resolve_student_id(student_id: str | None) -> str:
    """把 'current' / 空值解析为真实学号，其余原样返回。"""
    if not student_id or student_id == "current":
        return get_current_student_id()
    return student_id


def load_profile(student_id: str) -> StudentProfile | None:
    """读取学生档案（传 "current" 或空值表示当前学生）。"""
    student_id = resolve_student_id(student_id)
    if not student_id:
        return None
    with get_conn() as conn:
        row = conn.execute(
            "SELECT payload_json FROM profiles WHERE student_id = ?", (student_id,)
        ).fetchone()
    if not row:
        return None
    return StudentProfile.model_validate_json(row["payload_json"])
