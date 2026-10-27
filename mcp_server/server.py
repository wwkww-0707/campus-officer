"""MCP 服务器：把校规知识库以标准 MCP 协议暴露为工具。

本服务器使用 **stdio 传输**，实现 3 个工具：
1. `search_policy`     —— 政策条款检索（BM25 + 失效条款降权）
2. `get_deadlines`     —— 事项截止时间查询
3. `list_policy_docs`  —— 制度文件目录列举

与本地工具的分工：本地工具走 FastAPI 业务后端（事务型能力），
MCP 工具走知识库（只读检索型能力）。两者互补，体现 MCP 在"标准化接入
外部知识服务"上的作用。

运行方式（由 Agent 通过 stdio 自动拉起）：
    python mcp_server/server.py

也可手动用 MCP Inspector 连通性自测。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 保证以脚本方式运行时能 import 到项目根目录
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from core.knowledge import get_searcher  # noqa: E402

mcp = FastMCP(
    "campus-policy-server",
    instructions=(
        "校园政策知识库 MCP 服务器。提供校规条款检索、事项截止时间查询、"
        "制度文件目录列举三个工具。所有条款均带文号与生效期，"
        "已失效条款会被自动降权。"
    ),
)


@mcp.tool()
def search_policy(keyword: str, top_k: int = 4) -> str:
    """检索校规与政策条款。

    Args:
        keyword: 检索关键词，例如"国家奖学金 申请条件 金额"。
        top_k: 返回条数，默认 4。
    """
    hits = get_searcher().search(keyword, top_k=top_k)
    if not hits:
        return f"未检索到与「{keyword}」相关的条款。请更换关键词，或以学校官网正式文件为准。"

    lines = [f"检索「{keyword}」命中 {len(hits)} 条："]
    for hit in hits:
        c = hit.clause
        lines.append("")
        lines.append(f"◆ {c.title}")
        lines.append(f"  文号：{c.doc_number or '未标注'}")
        lines.append(f"  生效期：{c.effective_from or '未标注'} 起（状态：{c.status_label}）")
        lines.append(f"  来源：{c.source or '未标注'}")
        if hit.note:
            lines.append(f"  ⚠ {hit.note}")
    lines.append("")
    lines.append("提示：具体以学校官方最新文件为准。")
    return "\n".join(lines)


@mcp.tool()
def get_deadlines(keyword: str = "") -> str:
    """查询各类校务事项的截止时间。

    Args:
        keyword: 事项关键词，留空返回全部（如"助学金"、"选课"、"奖学金"）。
    """
    items = get_searcher().deadlines(keyword)
    if not items:
        return (
            f"未找到与「{keyword}」相关的时间节点。"
            "建议直接查看学校教务处或学生工作处发布的正式通知。"
        )
    lines = [f"与「{keyword or '全部'}」相关的时间节点（{len(items)} 条）："]
    for item in items:
        lines.append("")
        lines.append(f"◆ {item['title']}")
        lines.append(f"  时间：{item['text']}")
        lines.append(f"  文号：{item['doc_number']}")
        lines.append(f"  状态：{item['status']}")
    lines.append("")
    lines.append("提示：具体截止时间以学校官方最新通知为准。")
    return "\n".join(lines)


@mcp.tool()
def list_policy_docs(category: str = "") -> str:
    """列出知识库中的制度文件目录。

    Args:
        category: 类别过滤（奖助学金/选课/其他），留空返回全部。
    """
    clauses = get_searcher().list_docs(category or None)
    if not clauses:
        return f"没有找到类别为「{category}」的制度文件。"
    lines = [f"制度文件目录（{len(clauses)} 份）："]
    for c in clauses:
        sample_tag = "［校内演示样例］" if c.is_sample else "［公开文件］"
        lines.append(
            f"- {sample_tag}{c.title} ｜ 类别：{c.category} ｜ "
            f"文号：{c.doc_number or '未标注'} ｜ 状态：{c.status_label}"
        )
    lines.append("")
    lines.append("说明：［校内演示样例］为虚构条款，仅用于功能演示，不代表真实校规。")
    return "\n".join(lines)


if __name__ == "__main__":
    # 以 stdio 方式运行，供 MCP 客户端拉起
    mcp.run(transport="stdio")
