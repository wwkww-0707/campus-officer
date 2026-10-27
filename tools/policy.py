"""政策检索、档案读取与资格判定工具（3 个）。"""

from __future__ import annotations

from core.api_client import BackendUnavailable, get_client
from core.knowledge import get_searcher
from langchain_core.tools import tool

STUDENT_ID = "current"  # 由后端解析为当前学生，避免写死学号


@tool
def get_my_profile(student_id: str = STUDENT_ID) -> str:
    """读取我的学生档案。

    包含 GPA、专业排名、家庭经济困难认定结论、已修学分等字段，
    是奖助学金资格判定的输入依据。档案为**演示用虚构样例数据**。

    Args:
        student_id: 学号，默认使用演示账号 20260001。
    """
    try:
        profile = get_client().get_profile(student_id)
    except BackendUnavailable as exc:
        return f"【工具失败】{exc}"
    except Exception as exc:  # noqa: BLE001
        return f"【工具失败】读取档案出错：{exc}"

    rank = profile.get("class_rank_ratio")
    rank_text = f"专业排名前 {rank * 100:.0f}%" if rank is not None else "专业排名未核定"
    return (
        f"{profile.get('name', '')}（学号 {profile.get('student_id', '')}）\n"
        f"年级专业：{profile.get('grade', '')} {profile.get('department', '')}\n"
        f"学业情况：GPA {profile.get('gpa', '')}｜{rank_text}\n"
        f"已修学分：{profile.get('credit_earned', 0)} / 需修 {profile.get('credit_required', 0)}\n"
        f"家庭经济困难认定：{'已认定' if profile.get('family_difficulty') else '未认定'}\n"
        f"思想品德审核：{'通过' if profile.get('political_review') else '未通过'}\n"
        f"每周期可支配时间：约 {profile.get('avg_weekly_free_hours', 0)} 小时\n"
        f"兴趣方向：{'、'.join(profile.get('interests', [])) or '未设置'}"
    )


@tool
def search_policy(keyword: str, top_k: int = 4) -> str:
    """检索校规与政策条款。

    每条结果都带文号、生效日期与来源，可核验；已失效条款会被自动降权并提示以官方最新文件为准。

    Args:
        keyword: 检索关键词，如"国家奖学金 申请条件 金额"。
        top_k: 返回条数，默认 4。
    """
    searcher = get_searcher()
    hits = searcher.search(keyword, top_k=top_k)
    if not hits:
        return (
            f"知识库中未检索到与「{keyword}」相关的条款。"
            "请更换关键词，或以学校官网、教务处/学生处发布的正式文件为准。"
        )

    lines = [f"检索「{keyword}」命中 {len(hits)} 条："]
    for hit in hits:
        c = hit.clause
        lines.append("")
        lines.append(f"◆ {c.title}")
        lines.append(f"  文号：{c.doc_number or '未标注'}")
        lines.append(
            f"  生效期：{c.effective_from or '未标注'}"
            f"{' 至 ' + c.effective_to if c.effective_to else ' 起（现行）'}"
            f" ｜ 状态：{c.status_label}"
        )
        lines.append(f"  来源：{c.source or '未标注'}")
        if hit.note:
            lines.append(f"  ⚠ {hit.note}")
        lines.append(f"  相关度：{hit.score}")
        body = c.body.strip()
        if body:
            excerpt = body[:600] + ("……（完整条款见知识库）" if len(body) > 600 else "")
            lines.append("  内容摘要：")
            for para in [p for p in excerpt.split("\n") if p.strip()][:8]:
                lines.append(f"    {para.strip()}")
    lines.append("")
    lines.append("提示：以上结论依据公开文件与演示样例条款自动汇总，具体以学校官方最新文件为准。")
    return "\n".join(lines)


@tool
def check_eligibility(student_id: str = STUDENT_ID) -> str:
    """查询我的奖助学金资格初筛结果。

    依据档案中的成绩、家庭经济困难认定等字段逐项判定，返回可申请项目、判定依据与待交材料清单。

    Args:
        student_id: 学号，默认演示账号 20260001。
    """
    try:
        data = get_client().get_eligibility(student_id)
    except BackendUnavailable as exc:
        return f"【工具失败】{exc}"
    except Exception as exc:  # noqa: BLE001
        return f"【工具失败】资格查询出错：{exc}"

    profile = data.get("profile", {})
    lines = [
        f"学生档案（演示虚构数据）：{profile.get('name', '')} "
        f"学号{profile.get('student_id', '')} {profile.get('grade', '')} "
        f"{profile.get('department', '')} GPA {profile.get('gpa', '')}",
        f"共 {len(data.get('items', []))} 个奖助学金项目，"
        f"其中 {data.get('eligible_count', 0)} 项符合申请条件：",
        "",
    ]
    for item in data.get("items", []):
        mark = "✔ 符合" if item["eligible"] else "✘ 不符合"
        lines.append(f"{mark} 【{item['name']}】")
        if item["eligible"]:
            lines.append(f"    额度：{item['amount']}")
        lines.append(f"    依据：{item['document_ref']}（生效 {item['effective_from']}）")
        for reason in item.get("reasons", []):
            lines.append(f"    · {reason}")
        if item["eligible"] and item.get("missing_materials"):
            lines.append("    需准备材料：")
            for m in item["missing_materials"]:
                lines.append(f"      - {m}")
        lines.append("")

    lines.append(data.get("disclaimer", ""))
    return "\n".join(lines)
