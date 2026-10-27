"""LangGraph 编排核心。

本项目**不使用** `create_react_agent` 这类预构建封装，而是用 `langgraph.graph.StateGraph`
**手写节点与状态转移**，以便：
1. 在节点间精确控制**工具调用的流式事件**（界面实时显示执行过程）；
2. 插入**危险操作二次确认**环节（选课提交前必须用户确认）；
3. 在模型不可用时**自动切换到规则降级引擎**，而不是直接报错；
4. 在每轮结束后做**反思校验**，确保回答里有工具真实产出、有合规提示。

状态图（真正的 LangGraph 编译图）：

    START ──> planner ──(有模型)──> agent ──(还有工具调用)──> tools ──> agent
                   │                   └──(无工具调用 / 达上限)──> reflect ──> END
                   └─(无模型 / 模型失败)──> rule ──────────────────────────> END

    planner：把一句话拆成执行步骤（真实模型层自主拆解 / 规则层意图映射）
    agent：   调用模型（bind_tools），产出文本或工具调用请求
    tools：   真实执行工具，产出流式事件 + 二次确认 + 进度
    reflect： 确定性合规校验（不经过模型），确保"以官方最新文件为准"等提示到位
    rule：    规则降级引擎（纯本地），照样调用真实业务工具
"""

from __future__ import annotations

import json
import time
from operator import add
from typing import Annotated, Any, Iterator, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph

from core.config import get_settings
from core.llm import get_provider
from core.prompts import build_tool_error_hint
from core.rule_engine import rule_engine
from core.schemas import ToolEvent


# ---------------------------------------------------------------------------
# 状态定义（LangGraph StateGraph 的 state schema）
# ---------------------------------------------------------------------------

class CampusState(TypedDict, total=False):
    """状态图节点间共享的状态。

    `messages` 与 `events` 使用 `operator.add` 归约器：节点返回 `{..., "events": [e1, e2]}`
    时会**追加**到既有列表，而非覆盖——这是 LangGraph 处理"节点间累积"的惯用法。
    """

    user_input: str
    history: list[dict[str, Any]]
    confirmed_op: str | None
    # 累积型字段（add 归约）
    messages: Annotated[list[Any], add]
    events: Annotated[list[dict[str, Any]], add]
    collected_tool_events: Annotated[list[ToolEvent], add]
    # 覆盖型字段
    plan: dict[str, Any]
    response: Any
    final_text: str
    round_index: int
    model_failed: bool
    force_reflect: bool


# 待确认操作的登记表：{操作ID: 描述}
_pending_operations: dict[str, dict[str, Any]] = {}


def register_pending(op_id: str, description: str, payload: dict[str, Any]) -> None:
    """登记一个待用户确认的操作。"""
    _pending_operations[op_id] = {"description": description, "payload": payload}


def consume_pending(op_id: str) -> dict[str, Any] | None:
    """取出并移除一个待确认操作。"""
    return _pending_operations.pop(op_id, None)


def list_pending() -> list[dict[str, Any]]:
    """列出所有待确认操作。"""
    return [{"op_id": k, **v} for k, v in _pending_operations.items()]


class CampusGraph:
    """校园事务官状态图（LangGraph 编译图）。"""

    def __init__(self, tools: list[BaseTool] | None = None, memory_summary: str = "") -> None:
        self.settings = get_settings()
        self.provider = get_provider()
        self.tools = tools or []
        self.tool_map = {t.name: t for t in self.tools}
        self.memory_summary = memory_summary
        # 降级路径上收集到的工具事件，待补发给界面
        self._deferred_events: list[dict[str, Any]] = []
        # 编译状态图
        self._graph = self._build()

    # -- 图构建 -------------------------------------------------------------

    def _build(self) -> Any:
        """把节点与条件边组装成可执行的 LangGraph 状态图。"""
        graph = StateGraph(CampusState)

        graph.add_node("planner", self._node_plan)
        graph.add_node("agent", self._node_agent)
        graph.add_node("tools", self._node_tools)
        graph.add_node("reflect", self._node_reflect)
        graph.add_node("rule", self._node_rule)

        graph.add_edge(START, "planner")
        graph.add_conditional_edges(
            "planner",
            self._route_after_plan,
            {"agent": "agent", "rule": "rule"},
        )
        graph.add_conditional_edges(
            "agent",
            self._route_after_agent,
            {"tools": "tools", "reflect": "reflect", "rule": "rule"},
        )
        graph.add_edge("tools", "agent")  # 工具执行后回到 agent 继续推理
        graph.add_edge("reflect", END)
        graph.add_edge("rule", END)

        return graph.compile()

    # -- 条件路由 -----------------------------------------------------------

    @staticmethod
    def _route_after_plan(state: CampusState) -> str:
        """规划后：有真实模型走 agent 循环，无模型直接走规则引擎。"""
        provider = get_provider()
        return "agent" if provider.has_real_model else "rule"

    @staticmethod
    def _route_after_agent(state: CampusState) -> str:
        """agent 之后：模型失败→规则；达上限或不再调用→反思；仍有调用→执行工具。"""
        if state.get("model_failed"):
            return "rule"
        if state.get("force_reflect"):
            return "reflect"
        response = state.get("response")
        if response is not None and (getattr(response, "tool_calls", []) or []):
            return "tools"
        return "reflect"

    # -- 节点：规划 ---------------------------------------------------------

    def _node_plan(self, state: CampusState) -> dict[str, Any]:
        """planner 节点：产出计划、上报层级，并为真实模型层预装配消息。"""
        plan = self._plan(state["user_input"], state.get("history"))
        events: list[dict[str, Any]] = [
            {"type": "plan", "plan": plan},
            {
                "type": "level",
                "level": self.provider.level,
                "display": self.provider.status().display,
            },
        ]
        update: dict[str, Any] = {"plan": plan, "events": events}
        if self.provider.has_real_model:
            update["messages"] = self._build_messages(
                state["user_input"], state.get("history"), plan
            )
        return update

    def _build_messages(
        self,
        user_input: str,
        history: list[dict[str, Any]] | None,
        plan: dict[str, Any],
    ) -> list[Any]:
        """拼装模型层首轮消息：系统提示 + 历史 + 用户输入 + 规划注入。"""
        messages: list[Any] = [SystemMessage(content=self._system_prompt())]
        for item in (history or [])[-12:]:
            role = item.get("role")
            content = item.get("content", "")
            if role == "user":
                messages.append(HumanMessage(content=content))
            elif role == "assistant":
                messages.append(AIMessage(content=content))
            elif role == "tool":
                messages.append(ToolMessage(content=content, tool_call_id="prev"))
        messages.append(HumanMessage(content=user_input))

        if plan.get("steps"):
            plan_text = "\n".join(
                f"{i}. {s.get('title', '')} —— {s.get('action', '')}"
                + (f"（建议工具：{s['tool']}）" if s.get("tool") else "")
                for i, s in enumerate(plan["steps"], 1)
            )
            messages.append(
                SystemMessage(
                    content=f"本轮执行计划（由规划器生成，请按此推进，必要时可调整）：\n{plan_text}"
                )
            )
        return messages

    # -- 节点：agent --------------------------------------------------------

    def _node_agent(self, state: CampusState) -> dict[str, Any]:
        """agent 节点：调用模型。产出文本则进入反思；产出工具调用则进入执行。"""
        round_index = state.get("round_index", 0) + 1
        if round_index > self.settings.max_tool_rounds:
            # 达上限仍未收敛，强制进入反思（沿用已收集的文本）
            return {"round_index": round_index, "force_reflect": True}

        try:
            response = self.provider.llm.bind_tools(self.tools).invoke(state["messages"])
        except Exception:  # noqa: BLE001
            # 模型中途失败 → 降级到规则引擎，保证不中断
            return {"round_index": round_index, "model_failed": True}

        tool_calls = getattr(response, "tool_calls", []) or []
        if not tool_calls:
            return {
                "round_index": round_index,
                "response": response,
                "final_text": _extract_text(response),
            }
        return {"round_index": round_index, "response": response, "messages": [response]}

    # -- 节点：执行工具 -----------------------------------------------------

    def _node_tools(self, state: CampusState) -> dict[str, Any]:
        """tools 节点：真实执行模型请求的工具调用，产出流式事件与二次确认。"""
        response = state.get("response")
        events: list[dict[str, Any]] = []
        tool_events: list[ToolEvent] = []
        new_messages: list[Any] = []
        prior_events = state.get("collected_tool_events", [])

        for call in (response.tool_calls or []):
            tool_name = call.get("name", "")
            args = call.get("args", {}) or {}
            events.append({"type": "tool_start", "tool": tool_name, "args": args})

            start = time.perf_counter()
            ok, result_text = self._invoke_tool(tool_name, args)
            elapsed = int((time.perf_counter() - start) * 1000)

            tool_events.append(
                ToolEvent(
                    tool=tool_name,
                    phase="end",
                    args=args,
                    summary=result_text[:300],
                    ok=ok,
                    elapsed_ms=elapsed,
                )
            )
            events.append(
                {
                    "type": "tool_end",
                    "tool": tool_name,
                    "summary": result_text[:300],
                    "ok": ok,
                    "elapsed_ms": elapsed,
                }
            )
            new_messages.append(
                ToolMessage(
                    content=result_text,
                    tool_call_id=call.get("id", f"call_{tool_name}"),
                    name=tool_name,
                )
            )

            # 汇报计划推进进度：当前执行到第几步
            done_tools = {e.tool for e in prior_events + tool_events}
            step_idx = 0
            for i, s in enumerate(state.get("plan", {}).get("steps", []), 1):
                if s.get("tool") and s["tool"] in done_tools:
                    step_idx = i
            events.append(
                {
                    "type": "progress",
                    "step": step_idx,
                    "total": len(state.get("plan", {}).get("steps", [])),
                    "tool": tool_name,
                }
            )

            # ---- 危险操作二次确认 ----
            if tool_name == "enroll_course" and ok:
                op_id = f"enroll_{int(time.time())}"
                register_pending(op_id, f"提交选课：{args}", dict(args))
                events.append(
                    {
                        "type": "confirm",
                        "op_id": op_id,
                        "tool": tool_name,
                        "args": args,
                        "message": "该操作会修改教务数据，需要您确认后才会真正提交。",
                    }
                )

        return {
            "events": events,
            "collected_tool_events": tool_events,
            "messages": new_messages,
        }

    # -- 节点：反思 ---------------------------------------------------------

    def _node_reflect(self, state: CampusState) -> dict[str, Any]:
        """reflect 节点：确定性合规校验 + 产出最终答复。"""
        final_text = self._reflect(
            state.get("final_text", ""), state.get("collected_tool_events", [])
        )
        return {
            "final_text": final_text,
            "events": [
                {"type": "done", "content": final_text, "level": self.provider.level}
            ],
        }

    # -- 节点：规则降级 -----------------------------------------------------

    def _node_rule(self, state: CampusState) -> dict[str, Any]:
        """rule 节点：规则降级引擎（照样调用真实工具），产出最终答复。"""
        events: list[dict[str, Any]] = []
        if state.get("model_failed"):
            events.append(
                {
                    "type": "level",
                    "level": "mock",
                    "display": "规则降级引擎（模型调用失败后自动接管）",
                }
            )
        reply = self._run_rule_engine(state["user_input"], state.get("confirmed_op"))
        events.extend(self._drained_deferred())
        events.append(
            {"type": "done", "content": reply, "level": self.provider.level}
        )
        return {"final_text": reply, "events": events}

    # -- 对外入口 -----------------------------------------------------------

    def run(
        self,
        user_input: str,
        history: list[dict[str, Any]] | None = None,
        confirmed_op: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        """执行一次对话，流式产出事件（同步生成器）。

        用 `self._graph.invoke(...)` 一次性跑完整个状态图（节点内部是同步工具调用），
        再把节点按执行顺序累积到 state.events 的事件依次 yield 出来，
        保持与旧版**完全相同的事件协议**，因此界面与测试零改动。

        产出事件类型：
        - {"type": "plan", "plan": ...}           规划阶段
        - {"type": "level", ...}                  当前接入层级
        - {"type": "tool_start", "tool", "args"}  工具调用开始
        - {"type": "tool_end", "tool", "summary", "ok", "elapsed_ms"}
        - {"type": "confirm", ...}                需要用户确认
        - {"type": "progress", "step", "total"}  计划推进进度
        - {"type": "done", "content", "level"}   完成
        """
        initial: CampusState = {
            "user_input": user_input,
            "history": history or [],
            "confirmed_op": confirmed_op,
            "messages": [],
            "events": [],
            "collected_tool_events": [],
            "plan": {},
            "response": None,
            "final_text": "",
            "round_index": 0,
            "model_failed": False,
            "force_reflect": False,
        }
        result = self._graph.invoke(initial)
        for event in result.get("events", []):
            yield event

    # -- 系统提示 -----------------------------------------------------------

    def _system_prompt(self) -> str:
        """拼装系统提示词（含长期记忆注入）。"""
        base = self.provider.system_message()
        parts = [base]
        if self.memory_summary:
            parts.append(
                "\n# 已知的学生画像（来自长期记忆，无需重复询问）\n" + self.memory_summary
            )
        parts.append(
            "\n# 当前模型接入状态\n"
            f"当前层级：{self.provider.status().display}。"
            + (
                "这是真实大模型，请优先调用工具获取数据。"
                if self.provider.has_real_model
                else "⚠️ 当前为规则降级层，你应直接给出基于工具结果的结构化回复，不要编造数据。"
            )
        )
        return "\n".join(parts)

    # -- 规划阶段 -----------------------------------------------------------

    def _plan(self, user_input: str, history: list[dict[str, Any]] | None) -> dict[str, Any]:
        """规划阶段：把一句话拆解成可执行的步骤。

        这是"智能体"与"问答机器人"的分界线——
        机器人直接给答案，智能体先**想清楚要做什么、要用哪些工具、按什么顺序做**。

        - 真实模型层：由大模型生成结构化计划（步骤 / 依据 / 预期工具）；
        - 规则降级层：由意图识别直接映射出计划（保证无 Key 时依然可见规划过程）。
        """
        provider = self.provider

        if not provider.has_real_model:
            return self._plan_by_rules(user_input)

        planner_prompt = (
            "你是一个教务事务智能体的规划器。请把用户的请求拆解成 2~5 个执行步骤。\n"
            "严格输出 JSON，格式为：\n"
            '{"intent":"一句话概括用户意图","steps":[{"title":"步骤标题",'
            '"action":"这一步要做什么","tool":"用到的工具名或留空"}],'
            '"need_confirm":false}\n'
            "可用工具：list_courses、get_my_enrollments、check_schedule_conflict、"
            "enroll_course、search_policy、check_eligibility、write_artifact、"
            "get_deadlines（查截止时间，走 MCP）、list_policy_docs（列政策文件，走 MCP）。\n"
            "只输出 JSON，不要解释。"
        )
        messages: list[Any] = [SystemMessage(content=planner_prompt)]
        for item in (history or [])[-4:]:
            if item.get("role") == "user":
                messages.append(HumanMessage(content=item.get("content", "")))
        messages.append(HumanMessage(content=user_input))

        try:
            raw = provider.llm.invoke(messages).content
            if isinstance(raw, list):
                raw = "".join(
                    p.get("text", "") if isinstance(p, dict) else str(p) for p in raw
                )
            text = str(raw).strip()
            if text.startswith("```"):
                text = text.split("```")[1] if "```" in text[3:] else text[3:]
                text = text.replace("json", "").strip()
            data = json.loads(text)
            steps = [
                {
                    "title": s.get("title", "执行步骤"),
                    "action": s.get("action", ""),
                    "tool": s.get("tool", ""),
                }
                for s in data.get("steps", [])
            ][:5]
            return {
                "intent": data.get("intent", user_input[:40]),
                "steps": steps,
                "source": "model",
            }
        except Exception:  # noqa: BLE001
            return self._plan_by_rules(user_input)

    @staticmethod
    def _plan_by_rules(user_input: str) -> dict[str, Any]:
        """规则规划：把意图映射为固定的执行步骤。"""
        from core.rule_engine import detect_intent

        intent, _ = detect_intent(user_input)
        templates: dict[str, tuple[str, list[tuple[str, str, str]]]] = {
            "courses": (
                "查询可选课程并求解无冲突组合",
                [
                    ("查询课程目录", "拉取本学期全部课程及容量、学分、时段", "list_courses"),
                    ("读取已选课程", "获取我当前已选课程，作为冲突判定基准", "get_my_enrollments"),
                    ("求解课程组合", "在先修、无冲突、学分上限、容量四重约束下求解", "子代理·回溯搜索"),
                    ("生成选课清单", "整理推荐课程与未选中原因，写入产出物", "write_artifact"),
                ],
            ),
            "conflict": (
                "检测课程时间冲突",
                [
                    ("定位课程", "在课程目录中匹配用户提到的课程", "list_courses"),
                    ("计算冲突", "闭区间求交判定时段是否重叠", "check_schedule_conflict"),
                ],
            ),
            "enrollments": (
                "查询已选课程",
                [("读取选课记录", "获取已选课程、已选学分与上课时段", "get_my_enrollments")],
            ),
            "eligibility": (
                "初筛奖助学金资格",
                [
                    ("读取学生档案", "获取 GPA、专业排名、家庭经济困难认定结论", "get_my_profile"),
                    ("逐项比对门槛", "按各奖助学金项目的条件逐条比对并给出依据", "check_eligibility"),
                    ("汇总待办材料", "汇总可申请项目与需准备的材料清单", "check_eligibility"),
                ],
            ),
            "policy": (
                "检索政策条款",
                [
                    ("检索校规", "BM25 检索条款，失效条款自动降权", "search_policy"),
                    ("核验时效", "核对文号与生效期，标注现行/失效", "search_policy"),
                ],
            ),
            "deadline": (
                "查询事项截止时间",
                [("抽取时间节点", "从条款中抽取截止时间表述并保留限定词", "search_policy")],
            ),
            # 学习方案类（考研 / 四六级 / 教资 / 计算机等级 / 实习）——与
            # rule_engine 的 learning 意图对齐，保证计划面板与实际执行一致
            "learning": (
                "制定学习方案（考证 / 考研 / 实习）",
                [
                    ("检索备考指南", "BM25 检索对应考试的条款（时间线 / 报名 / 备考）", "search_policy"),
                    ("抽取关键节点", "从指南中抽取报名时间、考试时间与备考阶段", "get_deadlines"),
                    ("汇总行动建议", "按检索结果整理可执行的备考建议与材料清单", "search_policy"),
                ],
            ),
            "artifact": (
                "生成事务产出物",
                [
                    ("求解课程组合", "得出可执行的选课方案", "子代理·回溯搜索"),
                    ("排布学习时间", "按周课表计算四象限时间预算", "子代理·时间预算"),
                    ("写入文件", "生成 Markdown 清单到 outputs/", "write_artifact"),
                ],
            ),
            "enroll": (
                "提交选课（需确认）",
                [
                    ("定位课程", "匹配目标课程 id", "list_courses"),
                    ("提交选课", "校验容量、冲突、学分后写入教务数据", "enroll_course"),
                ],
            ),
        }
        intent_name, steps = templates.get(
            intent,
            ("处理你的请求", [("分析请求", "识别意图并调用相关工具", "")]),
        )
        return {
            "intent": intent_name,
            "steps": [{"title": t, "action": a, "tool": tool} for t, a, tool in steps],
            "source": "rules",
        }

    # -- 工具调用 -----------------------------------------------------------

    def _invoke_tool(self, name: str, args: dict[str, Any]) -> tuple[bool, str]:
        """执行一个工具，返回 (是否成功, 结果文本)。同步调用，无异步开销。"""
        tool = self.tool_map.get(name)
        if tool is None:
            return False, f"【工具失败】未找到工具 `{name}`。"
        try:
            result = tool.invoke(args)
            text = str(result)
            ok = not text.startswith("【工具失败】") and not text.startswith("【选课未成功】")
            if not ok:
                text = text + "\n\n" + build_tool_error_hint(name, text)
            return ok, text
        except Exception as exc:  # noqa: BLE001
            return False, f"【工具失败】执行 `{name}` 出错：{type(exc).__name__}: {exc}"

    # -- 反思 ---------------------------------------------------------------

    def _reflect(self, text: str, events: list[ToolEvent]) -> str:
        """反思校验：确保合规提示到位。

        这一步刻意**不用模型**——因为合规提示是确定性规则，
        用规则保证它一定存在，比让模型"记得加上"更可靠。
        """
        if not text:
            return "抱歉，我没有理解你的需求。可以试着这样问：\n" \
                   "- 「下学期能选哪些课，避开周三下午，挑3门」\n" \
                   "- 「我GPA 3.6能申请哪些奖助学金，要什么材料」\n" \
                   "- 「帮我排一份每周学习计划」"

        policy_markers = ["奖学金", "助学金", "补助", "认定", "政策", "规定", "材料", "截止"]
        if any(m in text for m in policy_markers) and "官方最新文件为准" not in text:
            text += "\n\n> 以上结论依据公开政策文件与演示用校内样例条款自动汇总，**以学校官方最新文件为准**。"

        if events:
            names = "、".join(dict.fromkeys(e.tool for e in events))
            text += f"\n\n<sub>本轮已调用工具：{names}（共 {len(events)} 次）</sub>"

        return text

    # -- 规则降级 -----------------------------------------------------------

    def _run_rule_engine(self, user_input: str, confirmed_op: str | None) -> str:
        """调用规则降级引擎。

        注意：规则引擎内部调用的是**与模型路径完全相同的工具对象**，
        因此降级时界面上的"执行过程"面板依然会显示工具被调用。
        """
        context: dict[str, Any] = {}
        if confirmed_op:
            payload = consume_pending(confirmed_op)
            context["confirmed"] = True
            context.update(payload or {})

        reply = rule_engine.handle(user_input, context)

        for event in getattr(rule_engine, "tool_events", []):
            self._deferred_events.append(event)

        return reply

    def _drained_deferred(self) -> list[dict[str, Any]]:
        """取出并清空补发队列，转换为界面事件。

        每个工具事件补发为「开始 + 结束」两条，
        使降级路径下的执行过程面板与模型路径表现一致。
        """
        events: list[dict[str, Any]] = []
        for ev in self._deferred_events:
            tool_name = ev.get("tool", "")
            args = ev.get("args", {})
            events.append({"type": "tool_start", "tool": tool_name, "args": args})
            events.append(
                {
                    "type": "tool_end",
                    "tool": tool_name,
                    "args": args,
                    "summary": ev.get("summary", ""),
                    "ok": ev.get("ok", True),
                    "elapsed_ms": ev.get("elapsed_ms", 0),
                }
            )
        self._deferred_events = []
        return events


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _extract_text(message: Any) -> str:
    """从 AIMessage 中提取文本内容。"""
    content = getattr(message, "content", "")
    if isinstance(content, list):
        return "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    return str(content)


def build_graph(tools: list[BaseTool] | None = None, memory_summary: str = "") -> CampusGraph:
    """构建状态图实例。

    未显式传入 tools 时默认挂载**全部本地工具 + 2 个 MCP 工具**
    （`get_deadlines` / `list_policy_docs`，经由标准 MCP 协议访问知识库）。
    """
    if tools is None:
        from core.mcp import get_mcp_tools
        from tools.registry import get_local_tools

        tools = get_local_tools() + get_mcp_tools()
    return CampusGraph(tools=tools, memory_summary=memory_summary)


def export_session_log(events: list[dict[str, Any]]) -> str:
    """把本轮工具调用导出为事务日志 JSON（用于产出物留痕）。"""
    log = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "events": events,
    }
    return json.dumps(log, ensure_ascii=False, indent=2)
