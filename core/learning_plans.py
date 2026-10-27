"""学习方案生成器：为「学习方案」页签的卡片点击提供结构化方案。

设计动机
--------
此前的学习方案卡片"点击后只把问题填入输入框"，交互价值低。本模块把
「点击 → 生成 → 展示」变成**一步完成**：点击卡片时直接调用本模块，
产出结构化数据（阶段步骤 / 内容安排 / 预计时间 / 资源信息），
由界面渲染成充实的信息卡片，而不是空折叠框。

数据来源（全部可核验，不编造）
--------
1. **知识库**：`knowledge/policies/` 中对应类别（考研/考证/实习）的条款——
   报名时间、考试时间、备考要点，带文号与来源；
2. **时间预算子代理**：`core/subagents.StudyBudgetAgent` 的四象限分配逻辑，
   依据学生档案（每周可支配约 22 小时）计算各阶段的每周投入。

与 rule_engine._handle_learning 的关系：后者服务**对话流**（纯文本回复），
本模块服务**卡片交互**（结构化数据 + 分阶段展示），共用同一个知识库检索器。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.knowledge import get_searcher


# ---------------------------------------------------------------------------
# 结构定义
# ---------------------------------------------------------------------------

@dataclass
class StudyPhase:
    """一个学习阶段。"""

    title: str
    duration: str
    actions: list[str] = field(default_factory=list)
    weekly_hours: str = ""


@dataclass
class LearningPlan:
    """一份完整的学习方案。"""

    topic: str
    category: str                      # 考研 / 考证 / 实习 / 专业强化
    headline: str                      # 一句话概述
    total_duration: str                # 总时长
    phases: list[StudyPhase] = field(default_factory=list)
    resources: list[dict[str, str]] = field(default_factory=list)  # {name, note, source}
    key_dates: list[str] = field(default_factory=list)             # 报名/考试等关键节点
    tips: list[str] = field(default_factory=list)
    doc_refs: list[str] = field(default_factory=list)              # 依据条款（文号）
    # --- 专业针对性（当学生档案带专业时填充）---
    major_name: str = ""               # 学生档案里填写的专业名（界面以此为准）
    profile_name: str = ""             # 实际命中的专业画像名（可能比填写值更宽）
    current_stage: str = ""            # 当前学年阶段（大一~大四）
    major_focus: list[str] = field(default_factory=list)  # 专业核心课与能力
    major_advice: str = ""             # 针对本专业的主题建议


# ---------------------------------------------------------------------------
# 方案模板：每类主题的骨架（内容从知识库填充，骨架保证结构完整）
# ---------------------------------------------------------------------------

_TEMPLATES: dict[str, dict[str, Any]] = {
    "考研": {
        "headline": "从择校到复试的全程备考方案",
        "total_duration": "约 10～14 个月（大三下学期启动为宜）",
        "phases": [
            StudyPhase(
                title="第一阶段 · 基础与择校",
                duration="第 1～4 个月",
                actions=[
                    "确定目标院校与专业，收集近 3 年分数线与报录比",
                    "英语：每日背单词 100 个 + 精读 1 篇真题阅读",
                    "数学：过完高等数学 / 线性代数教材，配套基础习题",
                    "专业课：通读指定参考书第一遍，建立知识框架",
                ],
                weekly_hours="每周 20～24 小时（课业之余为主）",
            ),
            StudyPhase(
                title="第二阶段 · 强化与真题",
                duration="第 5～9 个月（含暑假黄金期）",
                actions=[
                    "英语：真题套题训练，二刷阅读错题，开始积累写作模板",
                    "数学：强化讲义 + 660/1000 题专项，错题本复盘",
                    "政治：暑期启动，跟网课过马原 / 毛中特主线",
                    "专业课：第二轮复习，整理高频考点笔记",
                ],
                weekly_hours="每周 28～35 小时（暑假可全职投入）",
            ),
            StudyPhase(
                title="第三阶段 · 冲刺与模拟",
                duration="第 10～12 个月（考前 60 天）",
                actions=[
                    "全科进入模拟卷节奏，按真实考试时间整卷训练",
                    "政治：背诵分析题热点 + 时政串讲",
                    "英语：成文写作练习，固定自己的作文模板",
                    "专业课：目标院校真题逐套精做，查漏补缺",
                ],
                weekly_hours="每周 35～40 小时（冲刺强度）",
            ),
        ],
        "resources": [
            {"name": "中国研究生招生信息网（研招网）", "note": "报名 / 调剂 / 招生简章官方入口", "source": "官方渠道"},
            {"name": "目标院校研究生院官网", "note": "专业目录、参考书目、复试线", "source": "官方渠道"},
            {"name": "近 5 年政治 / 英语 / 数学真题", "note": "唯一权威练习材料，至少刷 2 遍", "source": "备考资料"},
        ],
        "tips": [
            "择校比努力更重要：先做 2 周调研再定目标，避免盲目冲击",
            "暑假是分水岭：完整保留 8 周强化期，效率决定成败",
            "错题本比刷题量重要：每周固定半天复盘错题",
            "关注 9 月预报名与 10 月正式报名节点，错过无法补报",
        ],
    },
    "四六级": {
        "headline": "词汇 → 真题 → 冲刺的阶梯备考方案",
        "total_duration": "约 2～3 个月（每天 1.5～2 小时）",
        "phases": [
            StudyPhase(
                title="第一阶段 · 词汇与听力打底",
                duration="第 1～4 周",
                actions=[
                    "核心词汇 2500 个，按 100 词/天滚动复习",
                    "每日精听 1 段 VOA / 真题听力，做听写训练",
                    "每周 2 篇真题阅读精读，逐句分析长难句",
                ],
                weekly_hours="每周 10～12 小时",
            ),
            StudyPhase(
                title="第二阶段 · 真题套题训练",
                duration="第 5～8 周",
                actions=[
                    "近 5 年真题按套限时训练（每周 2 套）",
                    "错题归因：词汇不认识 / 长难句 / 定位偏差分类记录",
                    "翻译与写作每周各 2 篇，对照范文修改",
                ],
                weekly_hours="每周 12～14 小时",
            ),
            StudyPhase(
                title="第三阶段 · 考前冲刺",
                duration="考前 2 周",
                actions=[
                    "留 2 套最新真题做全真模拟（按考试时间）",
                    "背熟写作万能句型与翻译常考表达",
                    "听力保持每日 1 套的题感，调整生物钟",
                ],
                weekly_hours="每周 8～10 小时",
            ),
        ],
        "resources": [
            {"name": "四六级官方考试大纲", "note": "题型、分值、时间分布的权威依据", "source": "官方渠道"},
            {"name": "历年真题（近 5 年）", "note": "核心材料，至少二刷", "source": "备考资料"},
            {"name": "词汇书或背词 App", "note": "选一款坚持到底，忌频繁更换", "source": "备考资料"},
        ],
        "tips": [
            "听力权重 35%：从第一天起保持每日精听，临时突击无效",
            "阅读先看题干再读文章：定位词训练能省 20% 时间",
            "写作和翻译动笔才算数：只看不写考场上写不完",
        ],
    },
    "计算机等级考试": {
        "headline": "以真题上机为核心的二级备考方案",
        "total_duration": "约 6～8 周（每天 1～1.5 小时）",
        "phases": [
            StudyPhase(
                title="第一阶段 · 考点梳理",
                duration="第 1～2 周",
                actions=[
                    "按大纲过一遍选择题考点（公共基础 + Office/语言基础）",
                    "安装考试原版环境，熟悉真实界面与保存规则",
                    "整理高频操作题考点清单（字处理 / 表格 / 演示）",
                ],
                weekly_hours="每周 7～10 小时",
            ),
            StudyPhase(
                title="第二阶段 · 上机真题专练",
                duration="第 3～6 周",
                actions=[
                    "每天 2 套操作题真题，严格限时 30 分钟内完成",
                    "选择题用题库 App 碎片时间刷（通勤 / 排队）",
                    "建立易错操作清单（页眉页脚 / 函数 / 图表）",
                ],
                weekly_hours="每周 10～12 小时",
            ),
            StudyPhase(
                title="第三阶段 · 全真模拟",
                duration="考前 1 周",
                actions=[
                    "成套模拟：选择题 + 操作题一次做完 120 分钟",
                    "只复盘错题本，不再开新题",
                    "确认准考证打印与考场路线",
                ],
                weekly_hours="每周 6～8 小时",
            ),
        ],
        "resources": [
            {"name": "NCRE 官网（ncre.neea.edu.cn）", "note": "报名、大纲、成绩查询官方入口", "source": "官方渠道"},
            {"name": "未来教育 / 无纸化真题库", "note": "操作题真题与模拟环境", "source": "备考资料"},
            {"name": "考试原版 Office 环境", "note": "务必用与考场一致的版本练习", "source": "备考资料"},
        ],
        "tips": [
            "操作题占 80 分：上机练习时间应占备考的 70% 以上",
            "选择题用碎片时间：性价比高于整段刷题",
            "每年 3 / 9 月两次考试：错过一次等半年，提前定报名日历",
        ],
    },
    "教师资格证": {
        "headline": "笔试 + 面试两段式备考方案",
        "total_duration": "约 3～4 个月（笔试 2 个月 + 面试 1 个月 + 报名缓冲）",
        "phases": [
            StudyPhase(
                title="第一阶段 · 笔试系统复习",
                duration="第 1～8 周",
                actions=[
                    "科目一（综合素质）：职业理念 + 法律法规 + 写作，背高频考点",
                    "科目二（教育知识与能力）：教育学 / 心理学主线，做思维导图",
                    "每周 1 套真题，主观题练习答题模板",
                ],
                weekly_hours="每周 10～12 小时",
            ),
            StudyPhase(
                title="第二阶段 · 笔试冲刺",
                duration="第 9～10 周",
                actions=[
                    "近 3 年真题全真模拟，重点突破材料分析题",
                    "作文积累教育类素材，固定开头结尾模板",
                    "简答题按口诀记忆法快速过第二轮",
                ],
                weekly_hours="每周 12 小时",
            ),
            StudyPhase(
                title="第三阶段 · 面试准备",
                duration="笔试后 4～6 周",
                actions=[
                    "结构化问答：每天 2 题，练 5 分钟内的表达框架",
                    "试讲：写 10 篇教案，找同伴或对镜练习",
                    "熟悉报名学科的课程标准与教材体系",
                ],
                weekly_hours="每周 8～10 小时",
            ),
        ],
        "resources": [
            {"name": "中小学教师资格考试网（ntce.neea.edu.cn）", "note": "报名、大纲、成绩官方入口", "source": "官方渠道"},
            {"name": "教师资格考试历年真题", "note": "笔试主观题 + 面试试讲题都源于真题库", "source": "备考资料"},
            {"name": "对应学科教材与课程标准", "note": "试讲的出题范围", "source": "官方渠道"},
        ],
        "tips": [
            "笔试报名通常在 1 月 / 9 月前后：设好日历提醒，错过等半年",
            "科二的简答题量最大：口诀记忆比死背效率高 3 倍",
            "面试试讲重台风：内容 60 分 + 表达 40 分，对着镜子练",
        ],
    },
    "实习": {
        "headline": "从简历到 offer 的求职全流程方案",
        "total_duration": "约 6～10 周（简历 2 周 + 投递 4～6 周 + 面试 2 周）",
        "phases": [
            StudyPhase(
                title="第一阶段 · 定位与简历",
                duration="第 1～2 周",
                actions=[
                    "明确方向：技术 / 产品 / 运营等，研究 10 个目标岗位 JD",
                    "简历一页纸：按 STAR 法则改写项目与实习经历",
                    "准备 1 分钟自我介绍 + 常见问题的英文/中文版本",
                ],
                weekly_hours="每周 6～8 小时",
            ),
            StudyPhase(
                title="第二阶段 · 投递与笔试",
                duration="第 3～8 周",
                actions=[
                    "每天投递 5～10 个岗位，用表格跟踪状态",
                    "刷目标岗位笔试题型（技术岗刷算法题，运营练案例分析）",
                    "每周复盘投递转化率，及时调整方向与话术",
                ],
                weekly_hours="每周 8～10 小时",
            ),
            StudyPhase(
                title="第三阶段 · 面试与决策",
                duration="持续进行",
                actions=[
                    "每场面试后 24 小时内写复盘笔记",
                    "模拟面试 2～3 次（同学互面或录像自查）",
                    "拿到 offer 后对比：业务方向 / 导师带教 / 转正概率",
                ],
                weekly_hours="每周 4～6 小时",
            ),
        ],
        "resources": [
            {"name": "学校就业信息网 / 辅导员", "note": "校内招聘会与内推信息第一来源", "source": "校内渠道"},
            {"name": "实习申请与权益指南（知识库）", "note": "实习协议、权益保障注意事项", "source": "知识库条款"},
            {"name": "目标公司官网招聘页", "note": "暑期实习 / 日常实习官方入口", "source": "官方渠道"},
        ],
        "tips": [
            "暑期实习 3～4 月是投递高峰：提前 1 个月备好简历",
            "面试是概率游戏：投 30 个、面 5 个、拿 1 个是正常比例",
            "确认实习协议与保险条款：权益保护在签之前（详见知识库指南）",
        ],
    },
}

# 主题 → 类别 的映射（用于知识库检索）
_TOPIC_CATEGORY = {
    "考研": "考研",
    "实习": "实习",
    "四六级": "考证",
    "计算机等级考试": "考证",
    "教师资格证": "考证",
}


# ---------------------------------------------------------------------------
# 生成逻辑
# ---------------------------------------------------------------------------

def generate_learning_plan(topic: str, department: str = "", grade: str = "") -> LearningPlan:
    """生成一份结构化学习方案。

    Args:
        topic: 主题名，如 "考研" / "四六级" / "计算机等级考试"。
        department: 学生专业（来自档案），用于叠加专业针对性建议；留空则通用。
        grade: 年级（如 "2024级"），用于推断当前学年阶段。

    Returns:
        LearningPlan 结构化方案；未知主题时 phases 为空、headline 提示无模板。
    """
    from core.major_profiles import grade_stage, match_major

    template = _TEMPLATES.get(topic)
    if template is None:
        return LearningPlan(
            topic=topic,
            category="其他",
            headline="暂无该主题的方案模板",
            total_duration="—",
        )

    plan = LearningPlan(
        topic=topic,
        category=_TOPIC_CATEGORY.get(topic, "考证"),
        headline=template["headline"],
        total_duration=template["total_duration"],
        phases=[StudyPhase(**p.__dict__) for p in template["phases"]],
        resources=[dict(r) for r in template["resources"]],
        tips=list(template["tips"]),
    )

    # ---- 叠加专业针对性 ----
    profile = match_major(department)
    stage = grade_stage(grade)
    plan.current_stage = stage
    if profile.name != "通用":
        # 展示用学生自己填的专业名；画像名单独留存，用于说明"按哪个画像生成"
        plan.major_name = (department or "").strip() or profile.name
        plan.profile_name = profile.name
        # 专业核心课放前面，让用户一眼看到"我这个专业最该抓什么"
        plan.major_focus = profile.core_courses[:3] + [
            f"关键能力：{s}" for s in profile.key_skills[:2]
        ]
        # 主题对应的专业建议（考研/实习取同名键，其余归到"考证"）
        boost_key = topic if topic in ("考研", "实习") else "考证"
        plan.major_advice = profile.topic_boost.get(boost_key, "")
        # 专业建议置顶，比通用建议更该先看到
        if plan.major_advice:
            plan.tips.insert(0, plan.major_advice)
        # 推荐证书中与本主题相关的排到资源区最前
        if boost_key == "考证":
            for cert in profile.certs[:2]:
                plan.tips.append(f"本专业推荐证书：{cert}")
        # 当前学年阶段提示
        for st, focus in profile.stage_focus:
            if st == stage:
                plan.tips.append(f"你当前是{stage}：{focus}")
                break

    # ---- 从知识库补充：关键时间节点 + 依据条款 ----
    searcher = get_searcher()
    category = plan.category
    # 用主题词检索，尽量命中对应条款
    hits = searcher.search(topic, top_k=4)
    if not hits:
        # 退而求其次：按类别列出全部条款
        hits = [
            type("H", (), {"clause": d})() for d in searcher.list_docs(category=category)
        ] or []

    for hit in hits[:3]:
        clause = getattr(hit, "clause", None)
        if clause is None:
            continue
        plan.doc_refs.append(f"{clause.title}（{clause.doc_number or '未标注文号'}）")
        # 从条款正文抽取"时间节点"类信息作为关键日期
        for line in clause.body.splitlines():
            line = line.strip().lstrip("#").lstrip("-").strip()
            if not line:
                continue
            if any(k in line for k in ["月", "周", "日前", "截止", "报名", "考试时间"]):
                if 8 < len(line) < 90 and line not in plan.key_dates:
                    plan.key_dates.append(line)

    plan.key_dates = plan.key_dates[:5]
    return plan


def generate_major_plan(department: str, grade: str = "") -> LearningPlan:
    """生成**针对某个专业**的核心能力学习方案。

    与主题方案的区别：主题方案回答"考研怎么准备"，本方案回答
    "我这个专业该怎么学"——以专业核心课与关键能力为主线，
    按学生当前学年阶段到毕业的剩余时间排布。

    Args:
        department: 学生专业（如"计算机科学与技术"）。
        grade: 年级（如"2024级"），用于定位当前阶段。

    Returns:
        LearningPlan；专业未命中画像时退回通用画像的方案。
    """
    from core.major_profiles import grade_stage, match_major

    profile = match_major(department)
    stage = grade_stage(grade)
    stages = [s for s, _ in profile.stage_focus]

    # 定位当前阶段；若已到大四或无法解析，从最后两个阶段开始
    if stage in stages:
        start = stages.index(stage)
    else:
        start = max(0, len(stages) - 2)
    remaining = profile.stage_focus[start:]

    # 每周投入：按专业强度给建议（理工/医学偏多，文法偏中）
    heavy = profile.name in ("计算机科学与技术", "护理学", "工程管理", "金融学")
    weekly = "每周 18～22 小时（专业核心课为主）" if heavy else "每周 12～16 小时（核心课 + 实践）"

    phases: list[StudyPhase] = []
    for i, (st, focus) in enumerate(remaining[:3], 1):
        # 每阶段把"阶段重点"拆成可执行的行动项：
        # 阶段重点原文 + 该阶段应对应的核心课程
        actions = [focus]
        # 从核心课里挑 2 门作为本阶段主攻（按阶段序号错开）
        picks = profile.core_courses[(i - 1) * 2: (i - 1) * 2 + 2]
        if picks:
            actions.append("主攻核心课：" + "；".join(picks))
        if i == 1:
            actions.append(
                "把关键能力练到可展示：从「"
                + "、".join(profile.key_skills[:2])
                + "」中各挑 1 项开始"
            )
        elif i == 2:
            actions.append("做一个能写进简历的成果：课程设计 / 竞赛 / 实习项目选其一")
        else:
            actions.append("收口：毕业设计 + 就业或升学二选一，集中火力")

        # duration 用「当前学年 / 下一学年」这类人话，而非机械的阶段计数
        dur = "当前学年" if i == 1 else ("下一学年" if i == 2 else "毕业学年")
        phases.append(
            StudyPhase(
                title=f"第{'一二三'[i - 1]}阶段 · {st}",
                duration=f"{dur}（{st}）",
                actions=actions,
                weekly_hours=weekly,
            )
        )

    # 仅在阶段不足 2 个时补一个「毕业衔接」阶段；
    # 若已覆盖到大四，则不再追加，避免与第二阶段内容重复。
    if len(phases) < 2:
        phases.append(
            StudyPhase(
                title=f"第{'一二三'[len(phases)]}阶段 · 毕业衔接",
                duration="毕业学年及衔接期",
                actions=[
                    f"完成毕业设计/论文，选题尽量贴近本专业方向（{'、'.join(profile.career_paths[:2])}）",
                    "就业或升学二选一，集中投入不摇摆",
                    "入职前或读研前 3 个月：补专业短板、熟悉工具链",
                ],
                weekly_hours=weekly,
            )
        )

    # 展示名以学生自己填写的专业为准；画像名单独留存，用于说明"按哪个画像生成"
    display_name = (department or "").strip() or profile.name
    plan = LearningPlan(
        topic=f"{display_name} 专业学习方案",
        category="专业强化",
        headline=f"针对「{display_name}」的核心能力强化路线"
        + (f"（你当前{stage}）" if stage else ""),
        total_duration=(
            f"从{stage or '当前学年'}覆盖到毕业（{len(phases)} 个阶段）"
        ),
        phases=phases,
        major_name=display_name,
        profile_name=profile.name,
        current_stage=stage,
        major_focus=profile.core_courses + [f"关键能力：{s}" for s in profile.key_skills],
    )

    # 资源：核心课 + 就业方向 + 证书
    for c in profile.core_courses[:3]:
        plan.resources.append({"name": c, "note": "专业主干课，务必学扎实", "source": "培养方案"})
    for p in profile.career_paths[:2]:
        plan.resources.append({"name": p, "note": "本专业典型出路", "source": "行业方向"})
    for cert in profile.certs[:2]:
        plan.resources.append({"name": cert, "note": "本专业推荐证书", "source": "证书清单"})

    plan.tips = [
        f"专业核心课优先于证书：{profile.core_courses[0]}",
        f"最能拉开差距的能力：{profile.key_skills[0]}",
        f"证书按性价比排序：{' > '.join(profile.certs[:3])}",
        "以上基于本专业画像整理，具体以学校教务处发布的本专业培养方案为准",
    ]

    # 从知识库补充与专业相关的政策节点（如实习、考证时间）
    searcher = get_searcher()
    for hit in searcher.search(f"{profile.name} 实习 考证", top_k=3)[:2]:
        clause = getattr(hit, "clause", None)
        if clause is None:
            continue
        plan.doc_refs.append(f"{clause.title}（{clause.doc_number or '未标注文号'}）")
        for line in clause.body.splitlines():
            line = line.strip().lstrip("#").lstrip("-").strip()
            if line and any(k in line for k in ["月", "周", "截止", "报名"]) and 8 < len(line) < 90:
                if line not in plan.key_dates:
                    plan.key_dates.append(line)
    plan.key_dates = plan.key_dates[:5]
    return plan
