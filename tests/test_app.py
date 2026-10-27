"""界面端到端交互测试（Streamlit AppTest）。

验证：
1. 界面能正常渲染，不抛异常；
2. 输入一句话后，**工具被真实调用**（检查执行过程面板）；
3. 会话被持久化；
4. 产出物被写入 outputs/。

用法：
    python -m pytest tests/test_app.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")


def _message_text(message) -> str:
    """提取 chat_message 的全部文本，兼容不同 Streamlit 版本。"""
    # 新版：ChatMessage.markdown 列表
    blocks = getattr(message, "markdown", None)
    if blocks is not None:
        return " ".join(str(b.value) for b in blocks)
    # 旧版：直接有 .value
    return str(getattr(message, "value", ""))


def _new_app(timeout: int = 180) -> AppTest:
    """创建新的 AppTest 实例。"""
    return AppTest.from_file(APP_PATH, default_timeout=timeout)


class TestAppRender:
    """界面渲染。"""

    def test_renders_without_exception(self):
        """首屏渲染不抛异常。"""
        at = _new_app()
        at.run()
        assert not at.exception, f"界面异常: {at.exception}"

    def test_has_chat_input(self):
        """存在聊天输入框。"""
        at = _new_app()
        at.run()
        assert len(at.chat_input) >= 1

    def test_shows_backend_status(self):
        """侧栏显示后端与模型状态指示。"""
        at = _new_app()
        at.run()
        texts = " ".join(str(c.value) for c in at.caption) + " ".join(
            str(m.value) for m in at.markdown
        )
        assert "教务后端" in texts or "模型" in texts


class TestConversation:
    """对话流程。"""

    def test_query_triggers_tool_call(self):
        """提问后应触发工具调用并产生回复。"""
        at = _new_app()
        at.run()
        at.chat_input[0].set_value("我GPA3.6能申请哪些奖助学金？要什么材料？").run()

        assert not at.exception, f"界面异常: {at.exception}"
        assert len(at.chat_message) >= 2, "没有产生对话"

        # 提取全部消息文本
        all_text = " ".join(_message_text(m) for m in at.chat_message)

        # 回复中应包含资格判定相关内容（证明真实调用了业务接口）
        assert "奖学金" in all_text or "助学金" in all_text or "资格" in all_text

        # 应显示工具调用痕迹（执行过程面板）
        assert "工具" in all_text or "调用" in all_text

    def test_course_query_returns_courses(self):
        """选课类问题应返回课程信息。"""
        at = _new_app()
        at.run()
        at.chat_input[0].set_value("下学期能选哪些课？挑3门").run()

        assert not at.exception, f"界面异常: {at.exception}"
        all_text = " ".join(_message_text(m) for m in at.chat_message)
        assert "学分" in all_text or "课程" in all_text or "选课" in all_text

class TestAgentVisibleBehaviour:
    """智能体行为的可见性。

    这是"这是一个智能体应用"与"这是一个查表界面"的分界线：
    规划过程、工具调用、执行轨迹三样必须在界面上可见。
    """

    def test_plan_is_rendered(self):
        """规划过程被渲染出来（先想清楚再动手）。"""
        at = _new_app()
        at.run()
        at.chat_input[0].set_value("下学期能选哪些课？挑3门").run()

        assert not at.exception
        all_text = " ".join(_message_text(m) for m in at.chat_message)
        assert "规划" in all_text, "没有渲染规划过程"
        assert "目标" in all_text, "规划中没有展示目标"

    def test_plan_lists_tools(self):
        """规划步骤标注了要用的工具。"""
        at = _new_app()
        at.run()
        at.chat_input[0].set_value("我GPA3.6能申请哪些奖助学金？").run()

        all_text = " ".join(_message_text(m) for m in at.chat_message)
        assert "check_eligibility" in all_text, "规划未标注工具名"

    def test_execution_trace_visible(self):
        """工具调用轨迹默认可见（不是折叠隐藏）。"""
        at = _new_app()
        at.run()
        at.chat_input[0].set_value("我GPA3.6能申请哪些奖助学金？").run()

        all_text = " ".join(_message_text(m) for m in at.chat_message)
        # 新版用"执行"分区标题 + 工具胶囊呈现
        assert "执行" in all_text or "调用工具" in all_text
        assert "返回" in all_text or "ms" in all_text

    def test_plan_and_execution_aligned(self):
        """计划里承诺的工具，真实被调用了。"""
        at = _new_app()
        at.run()
        at.chat_input[0].set_value("下学期能选哪些课？挑3门").run()

        all_text = " ".join(_message_text(m) for m in at.chat_message)
        # 计划提到 list_courses，执行轨迹里必须真的调用了它
        assert "list_courses" in all_text

    def test_plan_persisted_across_rerun(self):
        """规划随会话持久化，刷新后仍可见。"""
        at = _new_app()
        at.run()
        at.chat_input[0].set_value("我GPA3.6能申请哪些奖助学金？").run()

        # 重新运行（等价于刷新页面），历史里的计划应仍能渲染
        at.run()
        assert not at.exception
        all_text = " ".join(_message_text(m) for m in at.chat_message)
        assert "规划" in all_text, "刷新后规划丢失"


class TestSessionPersisted:
    """会话持久化。"""

    def test_session_persisted(self):
        """会话被持久化到 SQLite。

        注意：conftest.py 已把会话库重定向到临时目录，
        因此本用例自己写入一条消息再验证，不依赖其他测试的副作用。
        """
        from core.memory import SessionStore

        store = SessionStore()
        sid = store.create_session("测试会话")
        store.add_message(sid, "user", "测试消息")

        sessions = store.list_sessions()
        assert sessions, "没有会话被保存"
        assert any(s.message_count > 0 for s in sessions), "会话没有消息记录"


class TestLearningPlanCards:
    """学习方案卡片交互。

    需求：点击方案卡片必须**立即在本页生成并展示完整方案**
    （分阶段步骤 / 时间投入 / 关键节点 / 资源），而不是只把问题填入输入框。
    """

    TOPICS = ["考研", "实习", "四六级", "计算机等级考试", "教师资格证"]

    def test_generator_returns_full_structure(self):
        """生成器对每个主题都返回完整结构。"""
        from core.learning_plans import generate_learning_plan

        for topic in self.TOPICS:
            plan = generate_learning_plan(topic)
            assert plan.topic == topic
            assert plan.headline and plan.total_duration
            assert len(plan.phases) >= 3, f"{topic} 阶段不足"
            for ph in plan.phases:
                assert ph.title and ph.duration
                assert len(ph.actions) >= 3, f"{topic}/{ph.title} 行动项不足"
                assert ph.weekly_hours, f"{topic}/{ph.title} 缺每周投入"
            assert len(plan.resources) >= 3, f"{topic} 资源不足"
            assert plan.tips, f"{topic} 缺备考要点"

    def test_unknown_topic_degrades_gracefully(self):
        """未知主题不抛异常，返回空方案。"""
        from core.learning_plans import generate_learning_plan

        plan = generate_learning_plan("不存在的主题")
        assert plan.phases == []
        assert "暂无" in plan.headline

    @pytest.mark.parametrize("topic", TOPICS)
    def test_click_renders_plan_inline(self, topic: str):
        """点击卡片后，方案内容立即渲染在当前页（而非等待对话）。"""
        at = _new_app()
        at.run()

        buttons = [b for b in at.button if b.key == f"plan_{topic}"]
        assert buttons, f"找不到 {topic} 的生成按钮"

        buttons[0].click().run()
        assert not at.exception

        text = " ".join(str(m.value) for m in at.markdown)
        # 四个必备区块
        assert "已生成学习方案" in text, "缺少方案头部"
        assert "分阶段学习步骤" in text, "缺少分阶段步骤"
        assert "每周时间投入" in text, "缺少时间投入"
        assert "资源信息" in text, "缺少资源信息"

    def test_cards_rendered_before_click(self):
        """未点击时展示 5 张方案卡片（引导点击）。"""
        at = _new_app()
        at.run()
        text = " ".join(str(m.value) for m in at.markdown)
        assert "学习方案" in text
        for topic in self.TOPICS:
            assert any(b.key == f"plan_{topic}" for b in at.button), f"缺少 {topic} 卡片"

    def test_can_escalate_to_chat(self):
        """方案可一键转入对话继续细化（保留原有能力）。"""
        at = _new_app()
        at.run()
        [b for b in at.button if b.key == "plan_考研"][0].click().run()

        captions = " ".join(str(c.value) for c in at.caption)
        assert "转入对话" in captions
        assert any("转入对话" in b.label for b in at.button)
