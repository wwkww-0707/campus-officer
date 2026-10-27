"""工具注册表。

把自定义工具与 MCP 服务器工具汇总为 Agent 可用的工具列表。

注意区分两类工具：
- **本地工具**（本文件）：直接调用自建后端 / 本地知识库；
- **MCP 工具**：由 mcp_server/server.py 通过 stdio 协议暴露，
  在 core/graph.py 中按需挂载（避免 Streamlit 启动时因 MCP 不可用而失败）。
"""

from __future__ import annotations

from langchain_core.tools import BaseTool

from tools.artifacts import write_artifact
from tools.courses import (
    check_schedule_conflict,
    enroll_course,
    get_my_enrollments,
    list_courses,
)
from tools.policy import check_eligibility, get_my_profile, search_policy


def get_local_tools() -> list[BaseTool]:
    """获取全部本地自定义工具。"""
    return [
        list_courses,
        get_my_enrollments,
        get_my_profile,
        check_schedule_conflict,
        enroll_course,
        search_policy,
        check_eligibility,
        write_artifact,
    ]


# 供 README 与答辩引用的工具清单
TOOL_MANIFEST: list[dict[str, str]] = [
    {
        "name": "list_courses",
        "kind": "本地工具",
        "purpose": "查询课程目录（关键词/类别/学院/学分过滤）",
        "backend": "GET /api/courses",
    },
    {
        "name": "get_my_enrollments",
        "kind": "本地工具",
        "purpose": "查询我的已选课程与上课时段",
        "backend": "GET /api/enrollments/{sid}",
    },
    {
        "name": "check_schedule_conflict",
        "kind": "本地工具",
        "purpose": "确定性时间冲突检测（区间求交，不经模型）",
        "backend": "POST /api/schedule/conflicts",
    },
    {
        "name": "enroll_course",
        "kind": "本地工具",
        "purpose": "选课写入（容量/冲突/学分三重校验，需二次确认）",
        "backend": "POST /api/enrollments",
    },
    {
        "name": "search_policy",
        "kind": "本地工具",
        "purpose": "校规条款检索（BM25 + 失效条款降权 + 文号溯源）",
        "backend": "本地知识库 knowledge/policies",
    },
    {
        "name": "get_my_profile",
        "kind": "本地工具",
        "purpose": "读取学生档案（GPA/排名/困难认定/已修学分）",
        "backend": "GET /api/profile/{sid}",
    },
    {
        "name": "check_eligibility",
        "kind": "本地工具",
        "purpose": "奖助学金资格初筛（确定性规则，输出判定依据）",
        "backend": "GET /api/eligibility/{sid}",
    },
    {
        "name": "write_artifact",
        "kind": "本地工具",
        "purpose": "产出物写入（受限于 outputs/ 目录，防路径穿越）",
        "backend": "本地文件系统",
    },
    {
        "name": "search_policy (MCP)",
        "kind": "MCP 工具",
        "purpose": "MCP 版政策检索，走 stdio 协议",
        "backend": "mcp_server/server.py",
    },
    {
        "name": "get_deadlines (MCP)",
        "kind": "MCP 工具",
        "purpose": "MCP 版截止时间查询",
        "backend": "mcp_server/server.py",
    },
    {
        "name": "list_policy_docs (MCP)",
        "kind": "MCP 工具",
        "purpose": "MCP 版制度文件目录列举",
        "backend": "mcp_server/server.py",
    },
]
