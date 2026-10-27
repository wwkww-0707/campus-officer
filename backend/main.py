"""教务业务后端（FastAPI）。

这是本项目**自建的业务 API**——不是 mock，而是真实可运行、可被 Agent 通过工具调用的服务。
Agent 与界面之间的所有业务数据都经由这里，形成清晰的"业务闭环"。

启动方式：
    uvicorn backend.main:app --host 127.0.0.1 --port 8811
或直接运行：
    python backend/main.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# 支持 `python backend/main.py` 直接运行：把项目根目录加入模块搜索路径
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from fastapi import FastAPI, HTTPException  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from backend import db, eligibility  # noqa: E402
from backend.schedule import detect_conflicts  # noqa: E402
from backend.seed import seed_all  # noqa: E402
from core.schemas import StudentProfile  # noqa: E402

app = FastAPI(
    title="Campus Officer 教务业务 API",
    description="自建教务业务接口，供 Agent 工具层调用。所有数据为教学演示用虚构样例。",
    version="1.0.0",
)


# ---------------------------------------------------------------------------
# 启动时初始化
# ---------------------------------------------------------------------------

@app.on_event("startup")
def _startup() -> None:
    """首次启动自动建库并灌入种子数据。"""
    result = seed_all()
    print(f"[backend] 数据库就绪，新增课程 {result['courses_inserted']} 条")


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------

class EnrollRequest(BaseModel):
    """选课请求。"""

    student_id: str = "current"
    course_id: int = Field(..., description="课程 id")


class ConflictRequest(BaseModel):
    """冲突检测请求。"""

    student_id: str = "current"
    candidate_course_ids: list[int] = Field(default_factory=list, description="待并入的候选课程 id")


class AnnouncementRequest(BaseModel):
    """公告录入请求（学校新政策 / 活动，由用户手动录入）。"""

    title: str = Field(..., description="公告标题")
    category: str = Field("其他", description="类别：政策 / 活动 / 通知 / 其他")
    content: str = Field("", description="公告正文")
    source: str = Field("", description="来源（如学校官网 / 教务处）")
    event_date: str = Field("", description="活动日期 YYYY-MM-DD，仅活动类使用")
    url: str = Field("", description="原文链接，界面据此渲染「查看原文」跳转")


@app.get("/api/health")
def health() -> dict[str, Any]:
    """健康检查：供工具层与前端探测后端是否在线。"""
    return {"status": "ok", "service": "campus-officer-api", "version": app.version}


@app.get("/api/courses")
def api_list_courses(
    department: str | None = None,
    category: str | None = None,
    min_credits: float | None = None,
    max_credits: float | None = None,
    keyword: str | None = None,
) -> dict[str, Any]:
    """查询课程目录。"""
    courses = db.list_courses(
        department=department,
        category=category,
        min_credits=min_credits,
        max_credits=max_credits,
        keyword=keyword,
    )
    return {
        "count": len(courses),
        "courses": [c.model_dump() for c in courses],
        "note": "课程为演示用虚构样例数据",
    }


@app.get("/api/courses/{course_id}")
def api_get_course(course_id: int) -> dict[str, Any]:
    """按 id 查询单个课程。"""
    course = db.get_course(course_id)
    if course is None:
        raise HTTPException(status_code=404, detail=f"课程 id={course_id} 不存在")
    return course.model_dump()


@app.get("/api/enrollments/{student_id}")
def api_get_enrollments(student_id: str) -> dict[str, Any]:
    """查询学生已选课程。"""
    enrollments = db.get_enrollments(student_id)
    return {
        "student_id": student_id,
        "count": len(enrollments),
        "total_credits": sum(e.credits for e in enrollments),
        "enrollments": [e.model_dump() for e in enrollments],
    }


@app.post("/api/enrollments")
def api_enroll(req: EnrollRequest) -> dict[str, Any]:
    """提交选课（含容量校验与学分校验）。"""
    profile = db.load_profile(req.student_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"学生 {req.student_id} 档案不存在")

    course = db.get_course(req.course_id)
    if course is None:
        raise HTTPException(status_code=404, detail=f"课程 id={req.course_id} 不存在")

    # 学分上限校验（演示口径：单学期不超过 25 学分）
    current = sum(e.credits for e in db.get_enrollments(req.student_id))
    if current + course.credits > 25:
        raise HTTPException(
            status_code=400,
            detail=f"超出单学期学分上限：已选 {current} + 本门 {course.credits} > 25",
        )

    # 冲突校验（确定性算法）
    enrolled_courses = []
    for e in db.get_enrollments(req.student_id):
        c = db.get_course(e.course_id)
        if c:
            enrolled_courses.append(c)
    conflicts = detect_conflicts(enrolled_courses + [course])
    if conflicts:
        detail = "；".join(
            f"{c.course_a} 与 {c.course_b} 在 {', '.join(c.slots)} 冲突" for c in conflicts
        )
        raise HTTPException(status_code=409, detail=f"存在时间冲突：{detail}")

    ok, message = db.enroll(req.student_id, req.course_id)
    return {"ok": ok, "message": message, "course": course.model_dump()}


@app.delete("/api/enrollments/{student_id}/{course_id}")
def api_unenroll(student_id: str, course_id: int) -> dict[str, Any]:
    """退课。"""
    ok, message = db.unenroll(student_id, course_id)
    return {"ok": ok, "message": message}


@app.post("/api/schedule/conflicts")
def api_conflicts(req: ConflictRequest) -> dict[str, Any]:
    """计算时间冲突（确定性算法，不经过大模型）。"""
    enrolled = []
    for e in db.get_enrollments(req.student_id):
        c = db.get_course(e.course_id)
        if c:
            enrolled.append(c)

    candidates = []
    for cid in req.candidate_course_ids:
        c = db.get_course(cid)
        if c:
            candidates.append(c)

    conflicts = detect_conflicts(enrolled + candidates)
    return {
        "enrolled": [{"code": c.code, "name": c.name} for c in enrolled],
        "candidates": [{"code": c.code, "name": c.name} for c in candidates],
        "has_conflict": bool(conflicts),
        "conflicts": [c.model_dump() for c in conflicts],
        "algorithm": "闭区间 [start_period, end_period] 求交，同星期且节次区间相交即为冲突",
    }


@app.get("/api/eligibility/{student_id}")
def api_eligibility(student_id: str) -> dict[str, Any]:
    """奖助学金资格初筛（student_id 传 current 表示当前学生）。"""
    student_id = db.resolve_student_id(student_id)
    profile = db.load_profile(student_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"学生 {student_id} 档案不存在")
    items = eligibility.evaluate(profile)
    return {
        "student_id": student_id,
        "profile": profile.model_dump(),
        "items": [i.model_dump() for i in items],
        "eligible_count": sum(1 for i in items if i.eligible),
        "disclaimer": eligibility.DISCLAIMER,
    }


@app.get("/api/profile/current")
def api_get_current_profile() -> dict[str, Any]:
    """读取**当前学生**档案。

    前端与工具应优先使用本接口，而不是写死某个学号——
    用户可能把学号改成自己的，写死学号会导致改了专业却读不到。
    """
    sid = db.get_current_student_id()
    if not sid:
        raise HTTPException(status_code=404, detail="尚无学生档案，请先在侧栏填写并保存")
    profile = db.load_profile(sid)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"学生 {sid} 档案不存在")
    return profile.model_dump()


@app.get("/api/profile/{student_id}")
def api_get_profile(student_id: str) -> dict[str, Any]:
    """读取学生档案（student_id 传 current 表示当前学生）。"""
    student_id = db.resolve_student_id(student_id)
    profile = db.load_profile(student_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"学生 {student_id} 档案不存在")
    return profile.model_dump()


@app.post("/api/profile")
def api_save_profile(profile: StudentProfile) -> dict[str, Any]:
    """保存学生档案，并把它设为当前学生。"""
    try:
        db.save_profile(profile)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "student_id": profile.student_id}


@app.get("/api/summary")
def api_summary() -> dict[str, Any]:
    """总览：课程数、已选数、种子状态，用于界面顶部展示。"""
    courses = db.list_courses()
    enrollments = db.get_enrollments("current")
    return {
        "total_courses": len(courses),
        "total_enrolled": sum(e.credits for e in enrollments),
        "enrolled_count": len(enrollments),
        "full_courses": sum(1 for c in courses if c.full),
    }


@app.get("/api/announcements")
def api_list_announcements() -> dict[str, Any]:
    """列出全部公告（学校新政策 / 活动）。"""
    items = db.list_announcements()
    return {"announcements": items, "count": len(items)}


@app.post("/api/announcements")
def api_add_announcement(req: AnnouncementRequest) -> dict[str, Any]:
    """录入一条公告。"""
    ann_id = db.add_announcement(
        title=req.title,
        category=req.category,
        content=req.content,
        source=req.source,
        event_date=req.event_date,
        url=req.url,
    )
    return {"ok": True, "id": ann_id}


@app.delete("/api/announcements/{ann_id}")
def api_delete_announcement(ann_id: int) -> dict[str, Any]:
    """删除一条公告。"""
    ok = db.delete_announcement(ann_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"公告 {ann_id} 不存在")
    return {"ok": True}


@app.post("/api/sync/sandau")
async def api_sync_sandau() -> dict[str, Any]:
    """从杉达学院官网（news.sandau.edu.cn）抓取并同步校园信息到公告库。

    用 Playwright + 系统 Edge 真实渲染绕过 JS 反爬，提取通知/讲座/活动/新闻，
    按标题去重后作为公告入库（source 记为学校官网来源）。
    """
    import asyncio

    from backend.scraper import scrape_sandau

    try:
        items = await scrape_sandau()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"抓取官网失败：{type(exc).__name__}: {exc}")

    existing = {a["title"] for a in db.list_announcements()}
    added = 0
    for it in items:
        if it.title in existing:
            continue
        # content 只放"时间 · 地点"摘要，原文链接单独存 url 字段，
        # 界面据此渲染「查看原文」跳转按钮
        summary = " · ".join(x for x in (it.time, it.location) if x).strip()
        db.add_announcement(
            title=it.title[:200],
            category=it.category,
            content=summary,
            source="杉达学院官网",
            event_date=it.time,
            url=it.url,
        )
        existing.add(it.title)
        added += 1

    return {"ok": True, "added": added, "total": len(db.list_announcements())}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8811, log_level="info")
