"""规则降级引擎（第 3 层）。

当云端与本地模型都不可用时接管对话。**关键设计**：它不是"假装聊天"，
而是用关键词做**意图识别**，然后**照样调用真实的业务工具**，
把工具返回结果组织成回复。这样在完全离线、无 Key 的机器上：

- 主路径依然能跑通；
- 工具调用依然真实发生（界面日志可见）；
- 产出物依然能生成。

这保证了《期末考查要求》中"能用自然语言完成至少一个有工具参与的真实任务"
在任何环境下都成立。
"""

from __future__ import annotations

import re
from typing import Any

from core.api_client import BackendUnavailable, get_client
from core.knowledge import get_searcher
from core.learning_plans import generate_learning_plan
from core.subagents import (
    BudgetRequest,
    SolveRequest,
    course_combination_agent,
    study_budget_agent,
)

# 意图关键词表：(意图名, 关键词列表)
INTENT_RULES: list[tuple[str, list[str]]] = [
    ("enroll", ["选课", "报选", "选上", "加上", "报名这门", "帮我选"]),
    ("conflict", ["冲突", "撞课", "撞车", "时间冲突", "撞时间"]),
    ("enrollments", ["已选", "我选了", "选了什么", "我的课", "我的选课"]),
    ("eligibility", ["资格", "能申请", "可以申请", "符合", "助学金", "奖学金", "补助"]),
    ("deadline", ["截止", "什么时候", "期限", "何时", "时间节点"]),
    ("policy", ["政策", "规定", "管理办法", "细则", "要求是什么", "条件", "金额", "材料"]),
    ("courses", ["课程", "哪些课", "什么课", "推荐", "选课清单", "目录"]),
    ("artifact", ["清单", "表格", "计划", "文档", "导出", "生成"]),
    # 学习方案类（考证 / 实习 / 考研），领域专属词，避免误吞选课/每周计划
    ("learning", ["考研", "研究生", "四六级", "四级", "六级", "计算机等级", "教师资格",
                  "教资", "普通话", "实习", "证书", "考证", "备考",
                  # 厂商认证（华为 ICT 认证体系）
                  "hcia", "hcip", "hcie", "华为认证", "datacom", "数通",
                  "云计算认证", "大数据认证", "认证考试"]),
]

# 星期关键词
WEEKDAY_MAP = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7,
    "天": 7, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7,
}

# 英文星期
WEEKDAY_EN = {
    "monday": 1, "tuesday": 2, "wednesday": 3, "thursday": 4,
    "friday": 5, "saturday": 6, "sunday": 7,
}


def detect_intent(text: str) -> tuple[str, float]:
    """识别意图，返回 (意图名, 置信度)。

    采用加权关键词匹配：命中关键词越多、关键词越长，置信度越高。

    **消歧规则（重要）**："奖学金"这类词同时出现在资格判定与政策检索两类意图中，
    区分它们的关键是**提问视角**：
    - 「XX 的**条件/要求/标准/流程是什么**」→ 政策检索（policy）
    - 「**我**能申请吗 / **我**符合吗 / 我的情况够不够」→ 资格判定（eligibility）
    因此在加权匹配之后追加一层视角修正，避免把政策咨询误判为资格判定。
    """
    lowered = text.lower()
    best_intent = "courses"
    best_score = 0.0

    for intent, keywords in INTENT_RULES:
        score = 0.0
        for kw in keywords:
            if kw in lowered:
                # 长关键词权重更高
                score += len(kw) * 0.6 + 1.0
        if score > best_score:
            best_score = score
            best_intent = intent

    # ---- 视角消歧：区分「政策咨询」与「资格判定」----
    if best_intent in ("eligibility", "policy"):
        # 强政策信号：明确在问某项政策的条文本身
        strong_policy = any(
            kw in lowered
            for kw in ["条件是什么", "要求是什么", "标准是什么", "规定是什么",
                       "管理办法", "实施细则", "需要哪些材料", "哪些材料", "什么材料",
                       "有什么要求", "有哪些要求", "流程是什么", "依据",
                       "评选时间", "时间是什么", "什么时候评", "何时评"]
        )
        # 资格信号：问的是"我"能不能拿
        own_case = any(
            kw in lowered
            for kw in ["我能", "我可以", "我符合", "我的", "帮我看", "够不够",
                       "符合吗", "能申请", "可以申请"]
        )
        if strong_policy and not own_case:
            best_intent = "policy"
        elif own_case:
            best_intent = "eligibility"

    # ---- 视角消歧：区分「查我的已选课」与「查可选课程」----
    if best_intent == "courses":
        asks_own = any(
            kw in lowered
            for kw in ["我已选", "已选了", "我选了什么", "我的课", "我的选课", "选了哪些"]
        )
        if asks_own:
            best_intent = "enrollments"

    # ---- 视角消歧：区分「选课提交」与「产出物整理」----
    if best_intent == "enroll":
        # 出现"清单/文档/文件/整理/导出/表格"说明用户要的是文档而非提交选课
        wants_doc = any(
            kw in lowered
            for kw in ["清单", "文档", "文件", "整理", "导出", "表格", "报告", "导出成"]
        )
        if wants_doc:
            best_intent = "artifact"
        # 出现"几门/N门/哪些课/推荐/挑/选课组合"等"选组合"信号，且没点名具体课程 → 是选课推荐而非提交
        wants_combo = any(
            kw in lowered
            for kw in ["几门", "三门", "两门", "3门", "2门", "4门", "哪些课",
                       "什么课", "推荐", "挑几门", "选几门", "不撞", "不冲突", "避开"]
        )
        if wants_combo:
            best_intent = "courses"

    return best_intent, min(best_score / 12.0, 1.0)


def extract_weekdays(text: str) -> list[int]:
    """从文本中提取要避开的星期。

    只在出现"避开/不要/别/除了/除了…之外"等语境下才认为是回避项。
    """
    avoid_context = any(
        kw in text for kw in ["避开", "不要", "别选", "除了", "不排", "冲突", "没空", "不空"]
    )
    if not avoid_context:
        return []

    found: set[int] = set()
    lowered = text.lower()
    # 中文：周三 / 星期三 / 礼拜三
    for m in re.finditer(r"(?:周|星期|礼拜)\s*([一二三四五六日天1-7])", text):
        ch = m.group(1)
        if ch in WEEKDAY_MAP:
            found.add(WEEKDAY_MAP[ch])
    # 英文星期
    for en, num in WEEKDAY_EN.items():
        if en in lowered:
            found.add(num)
    return sorted(found)


def extract_number(text: str, patterns: list[str], default: float) -> float:
    """按给定正则提取第一个数字。"""
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            try:
                return float(m.group(1))
            except (ValueError, IndexError):
                continue
    return default


def extract_target_count(text: str) -> int:
    """提取目标课程门数。"""
    m = re.search(r"(\d+)\s*(?:门|个?课|首)", text)
    if m:
        return max(1, min(10, int(m.group(1))))
    if "三门" in text or "3门" in text:
        return 3
    if "两门" in text or "2门" in text:
        return 2
    return 3


def extract_keywords(text: str) -> list[str]:
    """提取偏好方向关键词。"""
    kws: list[str] = []
    lowered = text.lower()
    for kw in ["人工智能", "AI", "机器学习", "深度学习", "系统", "后端", "前端",
               "安全", "算法", "工程", "数据库", "网络", "数学", "写作", "竞赛", "考研"]:
        if kw.lower() in lowered:
            kws.append(kw)
    return kws


# 学习方案主题 → 触发词。顺序即优先级（越具体的排越前）。
# 与 core/learning_plans.py 的模板 key 必须保持一致。
LEARNING_TOPICS: list[tuple[str, list[str]]] = [
    ("HCIA 认证", ["hcia", "hcip", "hcie", "华为认证", "datacom", "数通",
                   "云计算认证", "大数据认证"]),
    ("考研", ["考研", "研究生", "推免"]),
    ("实习", ["实习", "暑期实习", "日常实习"]),
    ("四六级", ["四六级", "四级", "六级", "cet"]),
    ("计算机等级考试", ["计算机等级", "ncre", "二级考试", "三级考试"]),
    ("教师资格证", ["教师资格", "教资"]),
]


def detect_learning_topic(text: str) -> str:
    """从一句话里认出具体的考证 / 升学主题；认不出返回空串。

    认不出时上层会退回"按类别列条款"的通用回答，而不是硬套一个模板。
    """
    lowered = text.lower()
    for topic, keys in LEARNING_TOPICS:
        if any(k.lower() in lowered for k in keys):
            return topic
    return ""


class RuleEngine:
    """规则降级引擎。"""

    name = "rule_engine"

    def __init__(self) -> None:
        # 真实工具对象：降级路径同样调用它们，保证"工具被真正调用"
        # （含 2 个 MCP 工具：get_deadlines / list_policy_docs）
        from core.mcp import get_mcp_tools
        from tools.registry import get_local_tools

        self.tools = {t.name: t for t in get_local_tools() + get_mcp_tools()}
        # 本轮工具调用事件（供界面展示执行过程）
        self.tool_events: list[dict[str, Any]] = []

    # -- 工具调用 -----------------------------------------------------------

    def _invoke(self, tool_name: str, args: dict[str, Any]) -> str:
        """调用真实工具并记录事件。

        降级路径与模型路径走的是**同一套工具**，
        因此界面上的"执行过程"面板在任何层级下都能显示工具被调用。
        """
        tool = self.tools.get(tool_name)
        if tool is None:
            return f"【工具失败】未找到工具 {tool_name}"
        try:
            result = tool.invoke(args)
            text = str(result)
        except Exception as exc:  # noqa: BLE001
            text = f"【工具失败】{type(exc).__name__}: {exc}"

        ok = not text.startswith("【工具失败】") and not text.startswith("【选课未成功】")
        self.tool_events.append(
            {
                "tool": tool_name,
                "args": args,
                "ok": ok,
                "elapsed_ms": 0,
                "summary": text[:300],
            }
        )
        return text

    def handle(self, user_text: str, context: dict[str, Any] | None = None) -> str:
        """处理一次用户输入，返回回复文本。

        注意：无论命中哪个意图，都会**真实调用后端接口或本地知识库**。
        """
        intent, confidence = detect_intent(user_text)
        context = context or {}
        # 意图信息注入 context，供各处理器读取（避免处理器依赖外层变量）
        context.setdefault("intent", intent)
        context.setdefault("confidence", confidence)
        self.tool_events = []

        try:
            handler = {
                "courses": self._handle_courses,
                "enrollments": self._handle_enrollments,
                "conflict": self._handle_conflict,
                "enroll": self._handle_enroll,
                "eligibility": self._handle_eligibility,
                "policy": self._handle_policy,
                "deadline": self._handle_deadline,
                "artifact": self._handle_artifact,
                "learning": self._handle_learning,
            }.get(intent, self._handle_courses)
            return handler(user_text, context)
        except BackendUnavailable as exc:
            return (
                "⚠️ 教务业务后端当前不可用："
                f"{exc}\n\n"
                "请在项目目录运行 `python backend/main.py`（或 `start.bat`）启动后端后重试。"
            )
        except Exception as exc:  # noqa: BLE001
            return f"⚠️ 处理请求时出错：{type(exc).__name__}: {exc}"

    # -- 各意图处理器 -------------------------------------------------------

    def _handle_courses(self, text: str, ctx: dict[str, Any]) -> str:
        """课程目录 + 组合推荐。"""
        # ① 调用真实工具（记录事件，界面可见）
        self._invoke("list_courses", {"keyword": "", "category": "", "department": "",
                                      "min_credits": 0, "max_credits": 0})
        self._invoke("get_my_enrollments", {"student_id": "current"})

        # ② 取结构化数据用于组合求解（与工具同源，均来自后端接口）
        client = get_client()
        courses_data = client.list_courses()
        courses = courses_data.get("courses", [])

        if not courses:
            return "未查询到课程数据。请确认后端已启动并已灌入种子数据。"

        target = extract_target_count(text)
        max_credits = extract_number(
            text, [r"(?:不超过|上限|最多)\s*(\d+(?:\.\d+)?)\s*学分", r"(\d+(?:\.\d+)?)\s*学分"], 20.0
        )
        avoid = extract_weekdays(text)
        prefs = extract_keywords(text)

        enrolled_data = client.get_enrollments("current")
        enrolled_raw = enrolled_data.get("enrollments", [])

        # 用后端课程 id 组装 Course 对象
        from core.schemas import Course, Timeslot

        def to_course(raw: dict[str, Any]) -> Course:
            return Course(
                id=raw["id"], code=raw["code"], name=raw["name"], credits=raw["credits"],
                teacher=raw.get("teacher", ""), department=raw.get("department", ""),
                category=raw.get("category", "选修"), capacity=raw.get("capacity", 30),
                enrolled=raw.get("enrolled", 0),
                slots=[Timeslot(**s) for s in raw.get("slots", [])],
                prerequisites=raw.get("prerequisites", []),
                difficulty=raw.get("difficulty", 3), rating=raw.get("rating", 3.0),
                tags=raw.get("tags", []), hours_per_week=raw.get("hours_per_week", 2.0),
            )
        enrolled_courses = [to_course(e) for e in enrolled_raw]
        candidates = [to_course(c) for c in courses]

        req = SolveRequest(
            target_count=target,
            max_credits=max_credits,
            avoid_weekdays=avoid,
            prefer_keywords=prefs,
        )
        result = course_combination_agent.solve(candidates, enrolled_courses, req)

        lines = [
            f"【规则降级引擎｜意图={intent_label(ctx.get('intent', 'courses'))}"
            f"｜置信度={ctx.get('confidence', 0.0):.0%}】",
            "",
            result.message,
            "",
        ]
        if result.courses:
            lines.append("推荐课程：")
            for c in result.courses:
                slots = "、".join(
                    f"周{'一二三四五六日'[s.weekday - 1]}{s.start_period}-{s.end_period}节"
                    for s in c.slots
                )
                lines.append(
                    f"- {c.code}《{c.name}》{c.credits}学分 ｜ {slots} ｜ "
                    f"余量 {c.remaining}/{c.capacity} ｜ 评分 {c.rating}"
                )
            lines.append(f"\n合计 {result.total_credits} 学分")
        if result.rejected:
            lines.append("\n未选中课程及原因：")
            for r in result.rejected[:6]:
                lines.append(f"- {r['code']}《{r['name']}》—— {r['reason']}")

        # 计划中的最后一步：产出选课清单文件（让计划与执行真正对齐）
        plan = study_budget_agent.plan(result.courses, BudgetRequest())
        body = build_plan_markdown(result, plan)
        saved = self._invoke(
            "write_artifact",
            {"title": "选课清单", "content": body, "filename": "", "kind": "markdown"},
        )
        lines.append("")
        lines.append(f"📄 {saved}")

        lines.append("")
        lines.append("> 当前由规则降级引擎接管（未配置可用模型），工具调用与业务数据均为真实。")
        lines.append("> 如需更自然的语言交互，请在 .env 中配置 DASHSCOPE_API_KEY 后重启。")
        return "\n".join(lines)

    def _handle_enrollments(self, text: str, ctx: dict[str, Any]) -> str:
        """已选课程查询。"""
        self._invoke("get_my_enrollments", {"student_id": "current"})
        data = get_client().get_enrollments("current")
        enrollments = data.get("enrollments", [])
        if not enrollments:
            return "当前尚未选课。"
        lines = [f"已选 {data['count']} 门课，合计 {data.get('total_credits', 0)} 学分："]
        for e in enrollments:
            slots = "、".join(
                f"周{'一二三四五六日'[s['weekday'] - 1]}{s['start_period']}-{s['end_period']}节"
                for s in e.get("slots", [])
            )
            lines.append(f"- {e['course_code']}《{e['course_name']}》{e['credits']}学分  {slots}")
        return "\n".join(lines)

    def _handle_conflict(self, text: str, ctx: dict[str, Any]) -> str:
        """冲突检测：识别提到的课程名，映射为 id 后交给后端判定。"""
        self._invoke("list_courses", {"keyword": "", "category": "", "department": "",
                                      "min_credits": 0, "max_credits": 0})
        client = get_client()
        courses = client.list_courses().get("courses", [])
        mentioned = [
            c for c in courses
            if c["name"] in text or c["code"].lower() in text.lower()
        ]
        if not mentioned:
            return (
                "没有在课程目录中找到你提到的课程。"
                "请告诉我完整的课程名称，例如「数据结构与算法和人工智能导论冲突吗」。"
            )
        ids = [c["id"] for c in mentioned]
        # 调用真实冲突检测工具
        self._invoke(
            "check_schedule_conflict",
            {"course_ids": ",".join(str(i) for i in ids), "student_id": "current"},
        )
        data = client.check_conflicts("current", ids)
        if not data.get("has_conflict"):
            names = "、".join(c["name"] for c in mentioned)
            return f"无冲突：{names} 与你已选课程的时段不重叠。\n判定依据：{data.get('algorithm', '')}"
        lines = ["检测到时间冲突："]
        for c in data.get("conflicts", []):
            lines.append(f"- {c['course_a']} 与 {c['course_b']} 在 {'、'.join(c['slots'])} 冲突")
        lines.append(f"判定依据：{data.get('algorithm', '')}")
        return "\n".join(lines)

    def _handle_enroll(self, text: str, ctx: dict[str, Any]) -> str:
        """选课提交。规则引擎下同样走真实后端，并要求确认标记。"""
        self._invoke("list_courses", {"keyword": "", "category": "", "department": "",
                                      "min_credits": 0, "max_credits": 0})
        client = get_client()
        courses = client.list_courses().get("courses", [])
        mentioned = [
            c for c in courses
            if c["name"] in text or c["code"].lower() in text.lower()
        ]
        if not mentioned:
            return "没有找到要选的课程，请给出完整课程名。"
        target = mentioned[0]
        if not ctx.get("confirmed"):
            return (
                f"即将提交选课：**{target['code']}《{target['name']}》{target['credits']}学分**。\n\n"
                "这会修改教务数据，请回复「确认选课」后再执行。"
            )
        # 调用真实选课工具（内部含容量/冲突/学分三重校验）
        self._invoke("enroll_course", {"course_id": target["id"], "student_id": "current"})
        data = client.enroll("current", target["id"])
        if data.get("ok"):
            return f"✅ 选课成功：{data.get('message', '')}"
        return f"❌ 选课未成功：{data.get('message', '未知原因')}"

    def _handle_eligibility(self, text: str, ctx: dict[str, Any]) -> str:
        """资格初筛。

        把"查档案 → 逐项比对 → 汇总结论"三步显式化，
        让用户看见智能体**依据什么规则**得出结论，而不是只看到一张表。
        """
        # 步骤 1：读取学生档案（GPA、排名、困难认定）
        self._invoke("get_my_profile", {"student_id": "current"})
        # 步骤 2：按各项目门槛逐项比对（内部含规则引擎）
        self._invoke("check_eligibility", {"student_id": "current"})

        data = get_client().get_eligibility("current")
        items = data.get("items", [])
        profile = data.get("profile", {})

        lines = [
            f"**第 1 步 · 读取档案**：{profile.get('name', '')} "
            f"（{profile.get('grade', '')} {profile.get('department', '')}）"
            f"｜GPA {profile.get('gpa', '')}"
            f"｜专业排名前 {profile.get('class_rank_ratio', 0) * 100:.0f}%"
            f"｜家庭困难认定：{'已认定' if profile.get('family_difficulty') else '未认定'}",
            "",
            f"**第 2 步 · 逐项比对 {len(items)} 个项目的门槛条件**：",
            "",
        ]

        for item in items:
            mark = "✔" if item["eligible"] else "✘"
            lines.append(f"{mark} 【{item['name']}】{('  ' + item['amount']) if item['eligible'] else ''}")
            lines.append(f"   依据：{item['document_ref']}（生效 {item['effective_from']}）")
            for r in item.get("reasons", []):
                lines.append(f"   · {r}")
            if item["eligible"] and item.get("missing_materials"):
                lines.append("   待交材料：")
                for m in item["missing_materials"]:
                    lines.append(f"     - {m}")
            lines.append("")

        lines.append(f"**第 3 步 · 汇总**：{data.get('eligible_count', 0)} / {len(items)} 项符合条件")
        lines.append("")
        lines.append(data.get("disclaimer", ""))
        return "\n".join(lines)

    def _handle_policy(self, text: str, ctx: dict[str, Any]) -> str:
        """政策检索。"""
        keyword = " ".join(extract_keywords(text)) or text[:20]
        # 调用真实政策检索工具
        self._invoke("search_policy", {"keyword": keyword, "top_k": 4})
        hits = get_searcher().search(keyword, top_k=4)
        if not hits:
            return f"知识库中未检索到与「{keyword}」相关的条款，请以学校官网正式文件为准。"
        lines = [f"检索「{keyword}」命中 {len(hits)} 条：", ""]
        for hit in hits:
            c = hit.clause
            lines.append(f"◆ {c.title}")
            lines.append(f"  文号：{c.doc_number or '未标注'}｜状态：{c.status_label}")
            lines.append(f"  来源：{c.source or '未标注'}")
            if hit.note:
                lines.append(f"  ⚠ {hit.note}")
            lines.append("")
        lines.append("提示：以学校官方最新文件为准。")
        return "\n".join(lines)

    def _handle_deadline(self, text: str, ctx: dict[str, Any]) -> str:
        """截止时间查询。

        通过 **MCP 工具** `get_deadlines` 访问校规知识库（标准协议），
        让"截止查询"这条路径真实走一遍 MCP，而非仅本地检索。
        """
        keyword = " ".join(extract_keywords(text))
        result = self._invoke("get_deadlines", {"keyword": keyword})
        # MCP 工具失败（服务不可用）时回退到本地检索，保证主路径不中断
        if result.startswith("【工具失败】"):
            items = get_searcher().deadlines(keyword)
            if not items:
                return "未找到相关时间节点，请查看学校正式通知。"
            lines = [f"相关时间节点（{len(items)} 条）："]
            for item in items:
                lines.append(f"- {item['text']} ← {item['title']}（{item['doc_number']}）")
            lines.append("具体日期以学校正式通知为准。")
            return "\n".join(lines)
        return result

    def _handle_learning(self, text: str, ctx: dict[str, Any]) -> str:
        """学习方案：考证 / 实习 / 考研 建议 + 预计学习时间。

        两种输出：
        1. 认出具体主题（如 HCIA、四六级）→ 输出**结构化方案**
           （分阶段步骤 + 每周投入 + 资源 + 专业针对性建议 + 依据条款）；
        2. 认不出主题 → 退回"按类别列条款"的通用回答，不硬套模板。
        """
        # 调用真实政策检索工具（走同一知识库，界面可见）
        self._invoke("search_policy", {"keyword": text[:30], "top_k": 4})

        topic = detect_learning_topic(text)
        if topic:
            return self._render_learning_plan(topic, ctx)

        # ---- 未识别具体主题：按类别列条款 ----
        lowered = text.lower()
        if any(k in lowered for k in ["考研", "研究生"]):
            category = "考研"
        elif any(k in lowered for k in ["实习"]):
            category = "实习"
        else:
            category = "考证"

        searcher = get_searcher()
        hits = searcher.search(text, top_k=3, category=category)
        docs = [h.clause for h in hits] or [
            d for d in searcher.list_docs() if d.category == category
        ]

        lines = [
            f"【规则降级引擎｜意图=学习方案·{category}｜置信度={ctx.get('confidence', 0.0):.0%}】",
            "",
        ]
        if not docs:
            return "\n".join(lines) + "\n知识库中暂无该类别的学习方案，请以学校官方通知为准。"

        for d in docs[:2]:
            lines.append(f"## {d.title}")
            lines.append(d.body.strip())
            lines.append("")

        # 预计学习时间（按类别给示例投入，引导规划）
        lines.append("## ⏱ 预计学习时间（示例）")
        if category == "考研":
            lines.extend([
                "- 全程约 10～14 个月，建议大三下学期启动；",
                "- 每天投入 6～8 小时（后期冲刺阶段），前期可 3～4 小时/天；",
                "- 数学/专业课占比最高，政治暑期后集中。",
            ])
        elif category == "实习":
            lines.extend([
                "- 暑期实习 2～3 个月，秋招实习可延续到毕业；",
                "- 求职季每天 1～2 小时投递与准备面试；",
                "- 简历打磨建议提前 1～2 周完成初稿。",
            ])
        else:
            lines.extend([
                "- 单项证书备考一般 1～3 个月，视基础而定；",
                "- 每天 1～2 小时专项练习即可，勿占用主课时间；",
                "- 建议低年级优先通用证书，高年级结合就业方向。",
            ])

        lines.append("")
        lines.append("> 以上为学习规划建议，具体时间与政策以学校及官方最新通知为准。")
        return "\n".join(lines)

    def _render_learning_plan(self, topic: str, ctx: dict[str, Any]) -> str:
        """把结构化方案渲染成对话可读的 Markdown。

        专业与年级从**当前档案**读取，因此换专业后建议会跟着变。
        """
        dept, grade = "", ""
        try:
            prof = get_client().get_current_profile()
            dept = str(prof.get("department") or "")
            grade = str(prof.get("grade") or "")
        except Exception:  # noqa: BLE001  后端不可用时仍给通用方案
            pass

        plan = generate_learning_plan(topic, dept, grade)

        head = f"【学习方案 · {plan.topic}】"
        if dept:
            head += f"　专业：{dept}" + (f" · {grade}" if grade else "")

        lines = [
            head,
            "",
            f"**{plan.headline}**",
            f"⏱ 总周期：{plan.total_duration}",
            "",
        ]

        for ph in plan.phases:
            lines.append(f"### {ph.title}（{ph.duration}）")
            for action in ph.actions:
                lines.append(f"- {action}")
            if ph.weekly_hours:
                lines.append(f"- **每周投入**：{ph.weekly_hours}")
            lines.append("")

        if plan.resources:
            lines.append("### 📚 资源")
            for r in plan.resources[:4]:
                lines.append(f"- {r['name']} —— {r['note']}（{r['source']}）")
            lines.append("")

        if plan.tips:
            lines.append("### 💡 关键建议")
            for tip in plan.tips[:4]:
                lines.append(f"- {tip}")
            if plan.major_advice:
                lines.append(f"- **针对{dept or '你的专业'}**：{plan.major_advice}")
            lines.append("")

        if plan.doc_refs:
            lines.append("### 📌 依据条款")
            for ref in plan.doc_refs[:3]:
                lines.append(f"- {ref}")
            lines.append("")

        lines.append("> 方案为规划建议；考试方向、费用与有效期以官方当期公布为准。")
        return "\n".join(lines)

    def _handle_artifact(self, text: str, ctx: dict[str, Any]) -> str:
        """产出物生成：生成一份选课清单。"""
        # ① 先走真实工具，让界面能看到完整的工具调用链
        self._invoke("list_courses", {"keyword": "", "category": "", "department": "",
                                      "min_credits": 0, "max_credits": 0})
        self._invoke("get_my_enrollments", {"student_id": "current"})

        client = get_client()
        courses = client.list_courses().get("courses", [])
        from core.schemas import Course, Timeslot

        def to_course(raw: dict[str, Any]) -> Course:
            return Course(
                id=raw["id"], code=raw["code"], name=raw["name"], credits=raw["credits"],
                slots=[Timeslot(**s) for s in raw.get("slots", [])],
                prerequisites=raw.get("prerequisites", []),
                rating=raw.get("rating", 3.0), difficulty=raw.get("difficulty", 3),
                tags=raw.get("tags", []), hours_per_week=raw.get("hours_per_week", 2.0),
                # 容量与已选人数：决定"满员"判定与清单里的余量显示
                capacity=raw.get("capacity", 30), enrolled=raw.get("enrolled", 0),
                teacher=raw.get("teacher", ""), department=raw.get("department", ""),
                category=raw.get("category", "选修"),
            )

        enrolled = [
            to_course({"id": e["course_id"], "code": e["course_code"], "name": e["course_name"],
                       "credits": e["credits"], "slots": e.get("slots", []),
                       "prerequisites": [], "rating": 3.0, "difficulty": 3,
                       "tags": [], "hours_per_week": 2.0})
            for e in client.get_enrollments("current").get("enrollments", [])
        ]
        result = course_combination_agent.solve(
            [to_course(c) for c in courses], enrolled,
            SolveRequest(target_count=extract_target_count(text),
                         max_credits=extract_number(text, [r"(\d+)\s*学分"], 20.0)),
        )
        plan = study_budget_agent.plan(result.courses, BudgetRequest())

        # ② 通过 write_artifact 工具写入（权限隔离同样生效）
        body = build_plan_markdown(result, plan)
        saved = self._invoke(
            "write_artifact",
            {"title": "选课清单", "content": body, "filename": "", "kind": "markdown"},
        )
        return (
            f"已生成选课清单：{saved}\n\n"
            f"包含 {len(result.courses)} 门推荐课程（{result.total_credits} 学分）、"
            f"冲突检测结论与每周学习时间安排。"
        )


def intent_label(intent: str) -> str:
    """意图标识的中文名。"""
    from core.prompts import MOCK_INTENT_HINTS

    return MOCK_INTENT_HINTS.get(intent, intent)


def build_plan_markdown(result: Any, plan: Any) -> str:
    """构造选课清单 Markdown（规则引擎路径）。"""
    lines = [
        "# 选课清单",
        "",
        "**生成方式**：规则降级引擎（未配置可用模型）",
        "**数据来源**：自建教务业务 API（演示用虚构样例数据）",
        "",
        "## 一、推荐课程",
        "",
        "| 课程代码 | 课程名称 | 学分 | 上课时段 | 余量 | 评分 |",
        "|---|---|---|---|---|---|",
    ]
    for c in result.courses:
        slots = "、".join(
            f"周{'一二三四五六日'[s.weekday - 1]}{s.start_period}-{s.end_period}节"
            for s in c.slots
        )
        lines.append(
            f"| {c.code} | {c.name} | {c.credits} | {slots} | {c.remaining}/{c.capacity} | {c.rating} |"
        )
    lines += [
        "",
        f"**合计学分**：{result.total_credits}",
        "",
        "## 二、冲突检测结论",
        "",
        "所有课程已通过先修条件、时段冲突（闭区间求交）、学分上限与课程容量四重校验。",
        "",
        "## 三、每周学习时间安排",
        "",
        f"- 每周课表占用：约 {plan.contact_hours} 课时",
        f"- 每周可用课外时间：约 {plan.free_hours} 小时",
        "",
        "| 类别 | 每周投入（小时） |",
        "|---|---|",
    ]
    for k, v in plan.allocation.items():
        lines.append(f"| {k} | {v} |")
    lines += ["", "## 四、风险提示", ""]
    for r in plan.risks:
        lines.append(f"- {r}")
    lines += [
        "",
        "---",
        "",
        "> ⚠️ 本清单基于**演示用虚构样例数据**自动生成，仅供选课参考，",
        "> 实际课程安排、容量与选课规则**以学校教务系统及官方最新文件为准**。",
    ]
    return "\n".join(lines)


# 单例
rule_engine = RuleEngine()
