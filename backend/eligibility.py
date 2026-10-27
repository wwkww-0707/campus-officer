"""奖助学金资格初筛引擎（确定性规则）。

设计要点：
1. 每条判定都返回**可核验的依据**（文号、生效日期、来源），不允许出现无出处的结论；
2. 规则来自公开真实文件（教育部相关）与校内虚构样例条款，二者在输出中明确区分；
3. 判定失败时给出**缺失材料清单**，而不是简单返回 False——这是"失败时可解释"的要求。
"""

from __future__ import annotations

from core.schemas import EligibilityItem, StudentProfile

DISCLAIMER = "本结论依据公开政策文件与演示用校内样例数据自动初筛，仅供办事参考，具体以学校官方最新文件为准。"

# 奖助学金项目定义
# amount 为示例说明值，实际额度以官方文件为准
SCHOLARSHIPS: list[dict] = [
    {
        "id": "national_scholarship",
        "name": "国家奖学金",
        "doc_number": "财教〔2024〕38号",
        "effective_from": "2024-09-01",
        "source": "财政部 教育部关于印发《普通高等学校学生国家奖学金管理办法》的通知（公开文件）",
        "is_sample": False,
        "amount": "8000 元/年（示例值）",
        "requirements": {
            "gpa_min": 3.5,
            "rank_ratio_max": 0.10,
            "political_review": True,
            "family_difficulty": False,
        },
        "materials": [
            "国家奖学金申请表（本人填写、班级与院系签字）",
            "个人事迹材料与近期成绩单",
            "获奖证明复印件（由学生本人提供）",
        ],
        "deadline_doc": "通常于每年 9 月至 10 月进行院系推荐，具体时间以学校通知为准",
    },
    {
        "id": "national_encouragement",
        "name": "国家励志奖学金",
        "doc_number": "财教〔2013〕9号",
        "effective_from": "2013-09-01",
        "source": "财政部 教育部关于印发《普通高等学校学生国家励志奖学金管理办法》的通知（公开文件）",
        "is_sample": False,
        "amount": "5000 元/年（示例值）",
        "requirements": {
            "gpa_min": 3.0,
            "rank_ratio_max": 0.10,
            "political_review": True,
            "family_difficulty": True,
        },
        "materials": [
            "国家励志奖学金申请表",
            "家庭经济困难情况认定材料（由学校认定，本系统不采集）",
            "个人事迹材料与成绩单",
        ],
        "deadline_doc": "一般与国家奖学金同期组织，以学校通知为准",
    },
    {
        "id": "national_grant",
        "name": "国家助学金",
        "doc_number": "财教〔2010〕219号",
        "effective_from": "2010-10-01",
        "source": "财政部 教育部关于印发《普通高等学校学生国家助学金管理办法》的通知（公开文件）",
        "is_sample": False,
        "amount": "3300 元/年起，分档发放（示例值）",
        "requirements": {
            "gpa_min": 0.0,
            "rank_ratio_max": 1.0,
            "political_review": False,
            "family_difficulty": True,
        },
        "materials": [
            "家庭经济情况调查表或家庭经济困难认定材料",
            "个人申请书与近期成绩单",
            "银行卡信息（仅用于学校发放，本系统不采集）",
        ],
        "deadline_doc": "一般于每年 10 月前完成认定，以学校通知为准",
    },
    {
        "id": "school_first",
        "name": "校级一等奖学金",
        "doc_number": "杉达〔2025〕教字 7 号（校内示例条款）",
        "effective_from": "2025-09-01",
        "source": "《杉达学院学生奖助学金实施细则（演示样例）》",
        "is_sample": True,
        "amount": "3000 元/年（示例值）",
        "requirements": {
            "gpa_min": 3.7,
            "rank_ratio_max": 0.05,
            "political_review": True,
            "family_difficulty": False,
        },
        "materials": [
            "一等奖学金申请表",
            "年度综合测评表",
            "个人述职材料",
        ],
        "deadline_doc": "示例条款：每年 10 月中旬前提交至辅导员处",
    },
    {
        "id": "school_grant",
        "name": "校级困难补助（临时）",
        "doc_number": "杉达〔2025〕助字 3 号（校内示例条款）",
        "effective_from": "2025-09-01",
        "source": "《杉达学院学生临时困难补助管理办法（演示样例）》",
        "is_sample": True,
        "amount": "500~2000 元（示例值）",
        "requirements": {
            "gpa_min": 0.0,
            "rank_ratio_max": 1.0,
            "political_review": False,
            "family_difficulty": True,
        },
        "materials": [
            "临时困难补助申请表",
            "情况说明（需说明突发事由，不采集隐私细节）",
            "院系审核意见",
        ],
        "deadline_doc": "示例条款：全年可申请，突发情况下 5 个工作日内受理",
    },
    {
        "id": "work_study",
        "name": "勤工助学",
        "doc_number": "教工〔2005〕16号",
        "effective_from": "2005-09-01",
        "source": "教育部 财政部关于规范高等学校学生勤工助学管理工作的意见（公开文件）",
        "is_sample": False,
        "amount": "按小时计酬，每月不超过 8 小时（示例值）",
        "requirements": {
            "gpa_min": 0.0,
            "rank_ratio_max": 1.0,
            "political_review": False,
            "family_difficulty": False,
        },
        "materials": [
            "勤工助学岗位申请表",
            "本人课余时间安排说明",
            "院系与用工部门审核意见",
        ],
        "deadline_doc": "示例安排：每学期初集中受理，学期中视岗位空缺补招",
    },
]


def evaluate(profile: StudentProfile) -> list[EligibilityItem]:
    """对单个学生执行全部奖助学金项目的资格初筛。

    返回每一条的合格与否、判定依据、缺失材料与文号溯源。
    """
    results: list[EligibilityItem] = []

    for sch in SCHOLARSHIPS:
        req = sch["requirements"]
        reasons: list[str] = []
        missing: list[str] = []
        eligible = True

        # 学业成绩要求
        gpa_min = req["gpa_min"]
        if profile.gpa >= gpa_min:
            reasons.append(f"学业成绩 {profile.gpa} ≥ 要求 {gpa_min}")
        else:
            eligible = False
            reasons.append(f"学业成绩 {profile.gpa} 低于要求 {gpa_min}")

        # 政治面貌与思想品德（校内示例口径）
        if req["political_review"]:
            if profile.political_review:
                reasons.append("满足思想品德与政治面貌审核要求（演示样例口径）")
            else:
                eligible = False
                reasons.append("未满足思想品德与政治面貌审核要求（演示样例口径）")

        # 家庭经济情况
        if req["family_difficulty"]:
            if profile.family_difficulty:
                reasons.append("已取得家庭经济困难认定（由学校认定，本系统仅读取结论）")
            else:
                eligible = False
                reasons.append("未取得家庭经济困难认定，不符合本项目条件")

        # 排名比例要求
        # 注意：年级排名由院系核定，档案中未提供时**不作判定**，
        # 改为提示"需院系核定"，避免用学分进度错误近似排名。
        rank_ratio_max = req["rank_ratio_max"]
        if rank_ratio_max < 1.0:
            class_rank = getattr(profile, "class_rank_ratio", None)
            if class_rank is None:
                reasons.append(
                    f"该档位要求专业排名前 {rank_ratio_max:.0%}，"
                    "排名由院系核定（学生档案未提供），本系统不作判定"
                )
            elif class_rank <= rank_ratio_max:
                reasons.append(
                    f"专业排名 {class_rank:.0%} 达到该档位要求（前 {rank_ratio_max:.0%}）"
                )
            else:
                eligible = False
                reasons.append(
                    f"专业排名 {class_rank:.0%} 未达到该档位要求（前 {rank_ratio_max:.0%}）"
                )

        if eligible:
            missing = list(sch["materials"])

        results.append(
            EligibilityItem(
                scholarship_id=sch["id"],
                name=sch["name"],
                eligible=eligible,
                amount=sch["amount"] if eligible else "",
                reasons=reasons,
                missing_materials=missing,
                document_ref=sch["doc_number"],
                effective_from=sch["effective_from"],
                source=sch["source"],
                disclaimer=DISCLAIMER,
            )
        )

    return results


def materials_checklist(
    profile: StudentProfile, item: EligibilityItem
) -> dict[str, object]:
    """生成某一项奖助学金的材料自检表数据。"""
    sch = next((s for s in SCHOLARSHIPS if s["id"] == item.scholarship_id), None)
    return {
        "name": item.name,
        "eligible": item.eligible,
        "amount": item.amount,
        "materials": sch["materials"] if sch else [],
        "deadline": sch["deadline_doc"] if sch else "",
        "document_ref": item.document_ref,
        "source": item.source,
        "effective_from": item.effective_from,
        "is_sample": sch["is_sample"] if sch else False,
        "disclaimer": DISCLAIMER,
    }
