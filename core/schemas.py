"""核心数据模型（Pydantic）。

这些模型是 Agent、工具、业务后端之间传递数据的统一契约。
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# 课表与时间
# ---------------------------------------------------------------------------

class Timeslot(BaseModel):
    """一个上课时间段。

    星期取 1~7（周一~周日），节次取 1~12（每节 45 分钟）。
    采用闭区间 [start_period, end_period]，冲突判定为区间求交。
    """

    weekday: int = Field(..., ge=1, le=7, description="星期几，1=周一，7=周日")
    start_period: int = Field(..., ge=1, le=12, description="起始节次")
    end_period: int = Field(..., ge=1, le=12, description="结束节次（含）")
    weeks: str = Field("1-16周", description="周次范围说明")
    room: str = Field("", description="上课教室")

    def overlaps(self, other: "Timeslot") -> bool:
        """判断两个时间段是否冲突：同一天且节次区间有交集。"""
        if self.weekday != other.weekday:
            return False
        return not (self.end_period < other.start_period or other.end_period < self.start_period)

    def describe(self) -> str:
        """生成人类可读的时段描述。"""
        names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        day = names[self.weekday - 1] if 1 <= self.weekday <= 7 else f"周{self.weekday}"
        room = f" @{self.room}" if self.room else ""
        return f"{day}第{self.start_period}-{self.end_period}节{room}"


class Course(BaseModel):
    """课程信息。"""

    id: int
    code: str = Field(..., description="课程代码，如 CS201")
    name: str
    credits: float
    teacher: str = ""
    department: str = ""
    category: Literal["必修", "选修", "限选", "通识"] = "选修"
    capacity: int = 30
    enrolled: int = 0
    slots: list[Timeslot] = Field(default_factory=list)
    prerequisites: list[str] = Field(default_factory=list, description="先修课程代码")
    difficulty: int = Field(3, ge=1, le=5, description="难度 1~5")
    rating: float = Field(3.0, ge=0, le=5, description="参考评分 0~5")
    tags: list[str] = Field(default_factory=list)
    hours_per_week: float = Field(2.0, description="每周课外学时估计")

    @property
    def remaining(self) -> int:
        """剩余名额。"""
        return max(0, self.capacity - self.enrolled)

    @property
    def full(self) -> bool:
        """是否已满员。"""
        return self.remaining <= 0


class Enrollment(BaseModel):
    """一条选课记录。"""

    id: int
    student_id: str
    course_id: int
    course_code: str
    course_name: str
    credits: float
    slots: list[Timeslot] = Field(default_factory=list)
    created_at: str = ""


class ConflictPair(BaseModel):
    """一对冲突课程及其冲突时段。"""

    course_a: str
    course_b: str
    slots: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 奖助学金与资格
# ---------------------------------------------------------------------------

class EligibilityItem(BaseModel):
    """单条奖助学金项目的资格判定结果。"""

    scholarship_id: str
    name: str
    eligible: bool
    amount: str = Field("", description="资助额度描述")
    reasons: list[str] = Field(default_factory=list, description="判定依据（可核验）")
    missing_materials: list[str] = Field(default_factory=list, description="需补交材料")
    document_ref: str = Field("", description="依据文号")
    effective_from: str = Field("", description="生效日期")
    source: str = Field("", description="来源")
    disclaimer: str = Field("以学校官方最新文件为准", description="合规提示")


# ---------------------------------------------------------------------------
# 知识库
# ---------------------------------------------------------------------------

class PolicyClause(BaseModel):
    """校规条款：带文号与时效，供溯源与失效降权。"""

    clause_id: str
    title: str
    category: str = Field(..., description="类别：奖助学金/选课/学籍/其他")
    doc_number: str = Field("", description="文号，如 教学〔2025〕12号")
    effective_from: str = Field("", description="生效日期 YYYY-MM-DD")
    effective_to: str = Field("", description="失效日期 YYYY-MM-DD，空表示现行有效")
    source: str = Field("", description="来源说明")
    body: str = Field(..., description="条款正文")
    is_sample: bool = Field(False, description="是否为虚构演示样例")
    keywords: list[str] = Field(default_factory=list)

    @property
    def is_expired(self) -> bool:
        """条款是否已过失效期（以运行日期为准）。"""
        if not self.effective_to:
            return False
        from datetime import date

        try:
            end = date.fromisoformat(self.effective_to)
        except ValueError:
            return False
        return end < date.today()

    @property
    def status_label(self) -> str:
        """给界面展示的状态标签。"""
        if self.is_expired:
            return "已失效"
        if self.is_sample:
            return "校内示例条款"
        return "现行有效"


class PolicyHit(BaseModel):
    """检索命中的一条条款，含相关度与降权说明。"""

    clause: PolicyClause
    score: float
    note: str = Field("", description="降权/加权说明")


# ---------------------------------------------------------------------------
# 工具调用与产出物
# ---------------------------------------------------------------------------

class ToolEvent(BaseModel):
    """一次工具调用的流式事件，用于界面实时展示执行过程。"""

    tool: str
    phase: Literal["start", "end"] = "start"
    args: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    ok: bool = True
    elapsed_ms: int = 0


class Artifact(BaseModel):
    """产出物文件。"""

    filename: str
    path: str
    kind: str = Field("markdown", description="markdown/ics/json")
    summary: str = ""
    created_at: str = ""


class StudentProfile(BaseModel):
    """学生档案（虚构样例数据，用于长期记忆与资格初筛）。"""

    student_id: str = ""  # 真实学号，保存时必须非空；"current" 仅用于读取时解析
    name: str = "示例同学"
    school: str = "杉达学院"
    grade: str = "2024级"
    department: str = "计算机科学与技术"
    gpa: float = 3.6
    credit_earned: float = 68.0
    credit_required: float = 165.0
    class_rank_ratio: Optional[float] = Field(
        None,
        description="专业排名百分比（如 0.08 表示前 8%）；None 表示未核定，资格判定时不作判定",
    )
    family_difficulty: bool = True
    political_review: bool = True
    has_dormitory: bool = True
    avg_weekly_free_hours: float = 22.0
    interests: list[str] = Field(default_factory=lambda: ["人工智能", "系统"])
    enrolled_course_ids: list[int] = Field(default_factory=list)
    notes: str = "演示用虚构档案"
