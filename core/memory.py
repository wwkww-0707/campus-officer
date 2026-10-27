"""会话持久化与长期记忆。

两个层次：
1. **会话记忆**（SQLite）：会话列表、消息记录，支持多轮对话与历史回看；
2. **长期记忆**（AGENTS.md + 学生档案）：跨会话记住学生画像与偏好。
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from core.config import get_settings

DEFAULT_PROFILE = """# AGENTS.md —— 长期记忆

> 本文件由智能体维护，用于**跨会话记住**用户的画像与偏好，
> 使第二次对话不必重复说明背景信息。

## 学生画像

- 学号：20260001（**演示用虚构样例数据**）
- 姓名：示例同学
- 年级专业：2024级 计算机科学与技术
- 学业情况：GPA 3.6，已修 68 / 需修 165 学分
- 家庭情况：已通过家庭经济困难认定（由学校认定，系统仅读取结论）
- 每周期可支配课外时间：约 22 小时
- 兴趣方向：人工智能、系统

## 交互偏好

- 回答偏好：简洁、结论先行、给可执行清单
- 产出偏好：需要 Markdown 表格形式的可留档文档
- 关注重点：选课冲突、学分上限、申报截止时间

## 已办事项记录

<!-- 由智能体在完成事务后追加，例如： -->
<!-- - 2026-10-26 完成选课组合求解，推荐 3 门课程共 9 学分 -->

## 合规声明

本文件所有内容均为**教学演示用虚构样例数据**，不含任何真实个人信息。
涉及制度、资助、认定事项的结论一律以学校官方最新文件为准。
"""


@dataclass
class Session:
    """一个会话。"""

    id: int
    title: str
    created_at: str
    updated_at: str
    message_count: int = 0


class SessionStore:
    """SQLite 会话存储。"""

    def __init__(self, db_path: Path | None = None) -> None:
        settings = get_settings()
        self.db_path = db_path or settings.session_db
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        """建表。"""
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tool_calls TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );
                """
            )

    # -- 会话 ---------------------------------------------------------------

    def create_session(self, title: str = "新会话") -> int:
        """新建会话。"""
        now = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO sessions (title, created_at, updated_at) VALUES (?, ?, ?)",
                (title, now, now),
            )
            return int(cur.lastrowid)

    def list_sessions(self) -> list[Session]:
        """列出所有会话（按更新时间倒序）。"""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT s.id, s.title, s.created_at, s.updated_at,
                       (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS message_count
                FROM sessions s
                ORDER BY s.updated_at DESC
                """
            ).fetchall()
        return [
            Session(
                id=r["id"], title=r["title"], created_at=r["created_at"],
                updated_at=r["updated_at"], message_count=r["message_count"],
            )
            for r in rows
        ]

    def rename_session(self, session_id: int, title: str) -> None:
        """重命名会话。"""
        with self._connect() as conn:
            conn.execute(
                "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
                (title, datetime.now().isoformat(timespec="seconds"), session_id),
            )

    def delete_session(self, session_id: int) -> None:
        """删除会话及其消息。"""
        with self._connect() as conn:
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    # -- 消息 ---------------------------------------------------------------

    def add_message(
        self, session_id: int, role: str, content: str, tool_calls: Any = None
    ) -> None:
        """写入一条消息。"""
        payload = json.dumps(tool_calls, ensure_ascii=False, default=str) if tool_calls else ""
        now = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO messages (session_id, role, content, tool_calls, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, role, content, payload, now),
            )
            conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))

    def get_messages(self, session_id: int) -> list[dict[str, Any]]:
        """读取会话的全部消息。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT role, content, tool_calls, created_at FROM messages "
                "WHERE session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def clear_messages(self, session_id: int) -> None:
        """清空会话消息（保留会话本身）。"""
        with self._connect() as conn:
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))


class LongTermMemory:
    """长期记忆：AGENTS.md 文件 + 学生档案。"""

    def __init__(self, memory_file: Path | None = None) -> None:
        settings = get_settings()
        self.memory_file = memory_file or settings.agents_memory_file
        if not self.memory_file.exists():
            self.memory_file.parent.mkdir(parents=True, exist_ok=True)
            self.memory_file.write_text(DEFAULT_PROFILE, encoding="utf-8")

    def read(self) -> str:
        """读取长期记忆全文。"""
        return self.memory_file.read_text(encoding="utf-8")

    def summary(self) -> str:
        """提取用于注入系统上下文的简明摘要。"""
        text = self.read()
        lines: list[str] = []
        capture = False
        for line in text.splitlines():
            if line.startswith("## "):
                capture = "学生画像" in line or "交互偏好" in line
                if capture:
                    lines.append(line)
                continue
            if capture and line.strip():
                lines.append(line)
        return "\n".join(lines) if lines else text[:800]

    # 已办事项记录上限：超过后只保留最近 N 条，防止文件无限增长
    MAX_RECORDS = 30

    def append_record(self, entry: str) -> None:
        """在"已办事项记录"小节追加一条记录。

        为防止长期演示把文件撑爆，自动只保留最近 `MAX_RECORDS` 条记录
        （画像与交互偏好小节不受影响）。
        """
        text = self.read()
        marker = "## 已办事项记录"
        if marker not in text:
            text += f"\n\n{marker}\n\n{entry}\n"
            self.memory_file.write_text(text, encoding="utf-8")
            return

        head, _, rest = text.partition(marker)
        # rest = 记录区 + 后续小节（如 ## 合规声明）
        tail_idx = rest.find("\n## ", 1)
        records_block = rest if tail_idx < 0 else rest[:tail_idx]
        tail = "" if tail_idx < 0 else rest[tail_idx:]

        # 收集现有记录行（以 "- " 开头），新记录插到最前
        lines = [ln for ln in records_block.splitlines() if ln.strip().startswith("- ")]
        lines.insert(0, entry)
        lines = lines[: self.MAX_RECORDS]

        rebuilt = f"{head}{marker}\n\n" + "\n".join(lines) + "\n" + tail
        self.memory_file.write_text(rebuilt, encoding="utf-8")

    def write(self, content: str) -> None:
        """覆盖写入长期记忆全文（供用户在界面手动编辑）。"""
        self.memory_file.write_text(content, encoding="utf-8")

    def sync_profile(self, profile: dict[str, Any]) -> bool:
        """用**当前档案**刷新「学生画像」小节，返回是否发生改动。

        **为什么需要**：长期记忆是 AGENTS.md 里的静态文本，起初写的是演示档案
        （学号 20260001 / 计算机科学与技术）。学生把自己的学号专业改掉之后，
        侧栏「长期记忆」仍显示旧专业，看起来像"改了没生效"。
        这里在每次加载时比对并覆盖画像小节，交互偏好 / 已办事项 / 合规声明保持不变。
        """
        if not profile:
            return False

        sid = str(profile.get("student_id") or "").strip()
        if not sid:
            return False

        grade = str(profile.get("grade") or "").strip()
        dept = str(profile.get("department") or "").strip()
        gpa = profile.get("gpa")
        earned = profile.get("credit_earned")
        required = profile.get("credit_required")
        rank = profile.get("class_rank_ratio")
        interests = profile.get("interests") or []

        lines = ["## 学生画像", ""]
        lines.append(f"- 学号：{sid}（**演示用虚构样例数据**）")
        if profile.get("name"):
            lines.append(f"- 姓名：{profile['name']}")
        if grade or dept:
            lines.append(f"- 年级专业：{(grade + ' ' + dept).strip()}")
        if gpa is not None:
            rank_txt = f"，专业排名前 {int(rank * 100)}%" if rank else ""
            credits = (
                f"，已修 {earned:.0f} / 需修 {required:.0f} 学分"
                if earned is not None and required
                else ""
            )
            lines.append(f"- 学业情况：GPA {gpa}{rank_txt}{credits}")
        lines.append(
            "- 家庭情况："
            + ("已通过家庭经济困难认定（由学校认定，系统仅读取结论）"
               if profile.get("family_difficulty") else "未通过家庭经济困难认定")
        )
        if profile.get("avg_weekly_free_hours") is not None:
            lines.append(f"- 每周期可支配课外时间：约 {profile['avg_weekly_free_hours']:.0f} 小时")
        if interests:
            lines.append(f"- 兴趣方向：{'、'.join(str(i) for i in interests)}")
        new_section = "\n".join(lines)

        text = self.read()
        # 用正则定位「## 学生画像」到下一个二级标题之间的内容并替换
        pattern = re.compile(r"## 学生画像\s*\n.*?(?=\n## |\Z)", re.S)
        if pattern.search(text):
            updated = pattern.sub(new_section + "\n", text, count=1)
        else:
            # 没有画像小节时插到文件最前面（标题之后）
            updated = text.rstrip() + "\n\n" + new_section + "\n"

        if updated.strip() == text.strip():
            return False
        self.write(updated)
        return True
