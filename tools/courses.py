"""课程与选课相关工具（4 个）。

这些工具是 Agent 与教务业务后端之间的桥梁，全部通过 HTTP 调用真实业务接口。
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from core.api_client import BackendUnavailable, get_client

STUDENT_ID = "current"  # 由后端解析为当前学生，避免写死学号


def _fmt_course(c: dict[str, Any]) -> str:
    """把课程字典格式化成可读文本。"""
    slots = "、".join(
        f"周{'一二三四五六日'[s['weekday'] - 1]}{s['start_period']}-{s['end_period']}节"
        for s in c.get("slots", [])
    )
    return (
        f"{c['code']}《{c['name']}》{c['credits']}学分 "
        f"教师:{c.get('teacher', '—')} "
        f"余量:{max(0, c['capacity'] - c['enrolled'])}/{c['capacity']} "
        f"{slots}"
    )


@tool
def list_courses(
    keyword: str = "",
    category: str = "",
    department: str = "",
    min_credits: float = 0,
    max_credits: float = 0,
) -> str:
    """查询本学期课程目录。可按关键词、类别（必修/选修/通识）、学院、学分区间筛选。

    Args:
        keyword: 课程名/代码/教师关键词，留空返回全部。
        category: 课程类别：必修、选修、限选、通识，留空不限。
        department: 开课学院，留空不限。
        min_credits: 最低学分，0 表示不限。
        max_credits: 最高学分，0 表示不限。
    """
    try:
        data = get_client().list_courses(
            keyword=keyword or None,
            category=category or None,
            department=department or None,
            min_credits=min_credits or None,
            max_credits=max_credits or None,
        )
    except BackendUnavailable as exc:
        return f"【工具失败】{exc}"
    except Exception as exc:  # noqa: BLE001
        return f"【工具失败】查询课程目录出错：{exc}"

    courses = data.get("courses", [])
    if not courses:
        return "没有查询到符合条件的课程。"

    lines = [f"共 {data['count']} 门课程："]
    for c in courses:
        lines.append("- " + _fmt_course(c))
    return "\n".join(lines)


@tool
def get_my_enrollments(student_id: str = STUDENT_ID) -> str:
    """查询我当前已选的全部课程、已选学分与上课时段。

    Args:
        student_id: 学号，默认使用演示账号 20260001。
    """
    try:
        data = get_client().get_enrollments(student_id)
    except BackendUnavailable as exc:
        return f"【工具失败】{exc}"
    except Exception as exc:  # noqa: BLE001
        return f"【工具失败】查询已选课程出错：{exc}"

    enrollments = data.get("enrollments", [])
    if not enrollments:
        return f"当前尚未选课（已选学分 {data.get('total_credits', 0)}）。"

    lines = [f"已选 {data['count']} 门课，合计 {data.get('total_credits', 0)} 学分："]
    for e in enrollments:
        slots = "、".join(
            f"周{'一二三四五六日'[s['weekday'] - 1]}{s['start_period']}-{s['end_period']}节"
            for s in e.get("slots", [])
        )
        lines.append(
            f"- {e['course_code']}《{e['course_name']}》{e['credits']}学分  {slots}"
        )
    return "\n".join(lines)


@tool
def check_schedule_conflict(course_ids: str, student_id: str = STUDENT_ID) -> str:
    """检测指定课程与我的已选课程之间是否存在时间冲突。

    冲突判定由后端确定性算法完成（星期相同且节次区间相交），不依赖大模型判断。

    Args:
        course_ids: 课程 id 列表，用逗号分隔，例如 "5,7"。
        student_id: 学号，默认演示账号。
    """
    ids: list[int] = []
    for part in course_ids.replace("，", ",").split(","):
        part = part.strip()
        if part.isdigit():
            ids.append(int(part))
    if not ids:
        return "【工具失败】未提供有效的课程 id，请用逗号分隔的数字课程 id 调用。"

    try:
        data = get_client().check_conflicts(student_id, ids)
    except BackendUnavailable as exc:
        return f"【工具失败】{exc}"
    except Exception as exc:  # noqa: BLE001
        return f"【工具失败】冲突检测出错：{exc}"

    if not data.get("has_conflict"):
        cand = "、".join(c["name"] for c in data.get("candidates", [])) or "所选课程"
        return f"无冲突：{cand} 与已选课程的时间安排不冲突。判定依据：{data.get('algorithm', '')}"

    lines = ["检测到时间冲突："]
    for c in data.get("conflicts", []):
        lines.append(f"- {c['course_a']} 与 {c['course_b']} 在 {'、'.join(c['slots'])} 冲突")
    lines.append(f"判定依据：{data.get('algorithm', '')}")
    return "\n".join(lines)


@tool
def enroll_course(course_id: int, student_id: str = STUDENT_ID) -> str:
    """选课（写入操作）。会校验课程容量、时间冲突与学分上限。

    ⚠️ 这是一个会修改教务数据的操作，调用前必须先向用户确认。
    系统会在提交前自动检测冲突与容量，若不通过会返回明确原因。

    Args:
        course_id: 课程 id。
        student_id: 学号，默认演示账号。
    """
    try:
        data = get_client().enroll(student_id, course_id)
    except BackendUnavailable as exc:
        return f"【工具失败】{exc}"
    except Exception as exc:  # noqa: BLE001
        # 后端对容量/冲突/学分问题返回 4xx，这里转成可读信息
        return f"【选课未成功】{exc}"

    if data.get("ok"):
        return f"选课成功：{data.get('message', '')}"
    return f"【选课未成功】{data.get('message', '未知原因')}"
