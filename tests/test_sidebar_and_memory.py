"""侧栏「智能体能力」区的渲染与长期记忆同步测试。

背景：用户反馈侧栏两个区块显示乱码——
1. 「长期记忆」原用 `st.text()` 渲染成等宽 `<pre>`，保留 `##`/`**` 原始符号且不折行，
   窄侧栏里中文被横向截断；
2. 「工具与能力清单」原用 `st.dataframe()` 三列表格，塞进 ~300px 侧栏后被压扁、
   文字互相覆盖。
两者都需要改成可换行的卡片式渲染。

另外，长期记忆里的「学生画像」是静态文本，学生改了自己的学号/专业后不会跟着变，
读起来像"改了没生效"，因此增加 `sync_profile()` 按当前档案刷新画像小节。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.memory import LongTermMemory  # noqa: E402

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")


def _app() -> AppTest:
    at = AppTest.from_file(APP_PATH, default_timeout=180)
    at.run()
    return at


def _all_markdown(at: AppTest) -> str:
    return "\n".join(str(m.value) for m in at.markdown)


class TestSidebarRendering:
    """两个区块不再使用不可折行的控件。"""

    def test_memory_uses_card_html(self):
        """长期记忆渲染成键值卡，且不再出现原始 Markdown 符号。"""
        at = _app()
        md = _all_markdown(at)
        assert 'class="co-mem"' in md
        # 出现小节标题样式，而不是裸的 "## 学生画像"
        assert 'class="co-mem-h"' in md
        # 键值行成对出现
        assert 'class="k"' in md and 'class="v"' in md

    def test_memory_not_rendered_as_pre(self):
        """不再用 st.text（等宽 <pre> 不折行，是乱码感的来源）。"""
        at = _app()
        assert len(at.text) == 0, "长期记忆仍在用 st.text 渲染"

    def test_long_key_still_key_value(self):
        """长键（如「每周期可支配课外时间」）也应拆成键值行，不整句变段落。"""
        at = _app()
        md = _all_markdown(at)
        assert "每周期可支配课外时间" in md
        # 该行必须落在 .co-mem-row 内（键值），而不是 co-mem-p（段落）
        idx = md.index("每周期可支配课外时间")
        before = md[:idx]
        assert before.rfind('class="co-mem-row"') > before.rfind('class="co-mem-p"')

    def test_tool_list_uses_list_not_table(self):
        """工具清单改为纵向列表，不再用 dataframe。"""
        at = _app()
        md = _all_markdown(at)
        assert 'class="co-tool"' in md
        assert len(at.dataframe) == 0, "工具清单仍在用 st.dataframe"

    def test_tool_items_have_badge_and_purpose(self):
        """每条工具都有类型徽章与作用说明。"""
        at = _app()
        md = _all_markdown(at)
        assert 'class="badge' in md
        assert "查询课程目录" in md
        # MCP 工具用不同徽章样式区分
        assert "badge mcp" in md


class TestMemoryProfileSync:
    """长期记忆中的学生画像跟随当前档案。"""

    SAMPLE = """# AGENTS.md —— 长期记忆

## 学生画像

- 学号：20260001（**演示用虚构样例数据**）
- 姓名：示例同学
- 年级专业：2024级 计算机科学与技术

## 交互偏好

- 回答偏好：简洁

## 已办事项记录

- 2026-10-26 完成选课组合求解

## 合规声明

- 所有数据均为演示样例
"""

    def _memory(self, tmp_path) -> LongTermMemory:
        p = tmp_path / "AGENTS.md"
        p.write_text(self.SAMPLE, encoding="utf-8")
        return LongTermMemory(memory_file=p)

    def test_profile_section_updated(self, tmp_path):
        """学号 / 专业 / GPA 被刷新为当前档案的值。"""
        mem = self._memory(tmp_path)
        changed = mem.sync_profile(
            {
                "student_id": "2025016",
                "name": "韩思琰",
                "grade": "2025级",
                "department": "大数据",
                "gpa": 3.6,
                "credit_earned": 68.0,
                "credit_required": 165.0,
                "class_rank_ratio": 0.08,
                "family_difficulty": True,
                "avg_weekly_free_hours": 22.0,
                "interests": ["人工智能"],
            }
        )
        assert changed is True
        text = mem.read()
        assert "2025016" in text
        assert "2025级 大数据" in text
        assert "20260001" not in text
        assert "计算机科学与技术" not in text

    def test_other_sections_preserved(self, tmp_path):
        """交互偏好 / 已办事项 / 合规声明不受影响。"""
        mem = self._memory(tmp_path)
        mem.sync_profile({"student_id": "2025016", "department": "大数据"})
        text = mem.read()
        assert "## 交互偏好" in text and "回答偏好：简洁" in text
        assert "完成选课组合求解" in text
        assert "## 合规声明" in text

    def test_idempotent(self, tmp_path):
        """同样内容再同步一次不写盘（返回 False）。"""
        mem = self._memory(tmp_path)
        profile = {"student_id": "2025016", "department": "大数据", "gpa": 3.6}
        assert mem.sync_profile(profile) is True
        assert mem.sync_profile(profile) is False

    def test_empty_profile_is_noop(self, tmp_path):
        """空档案不做任何改动，避免把画像清空。"""
        mem = self._memory(tmp_path)
        before = mem.read()
        assert mem.sync_profile({}) is False
        assert mem.sync_profile({"student_id": ""}) is False
        assert mem.read() == before

    def test_difficulty_reflects_false(self, tmp_path):
        """困难认定为未通过时，画像如实说明，不沿用旧文案。"""
        mem = self._memory(tmp_path)
        mem.sync_profile({"student_id": "2025016", "family_difficulty": False})
        assert "未通过家庭经济困难认定" in mem.read()


class TestIconFontNotOverridden:
    """自定义 CSS 不能覆盖 Streamlit 的图标字体。

    历史 bug：全局字体规则写成 `.stApp span { font-family: 中文字体 }`，
    而展开箭头等图标正是 `<span data-testid="stIconMaterial">arrow_drop_down</span>`
    靠 Material Symbols 的字体连字渲染成字形。字体被覆盖后连字失效，
    页面上直接把 `arrow_drop_down` 当文字显示，并与标题重叠——看起来像乱码。
    """

    @staticmethod
    def _css() -> str:
        src = Path(APP_PATH).read_text(encoding="utf-8")
        return src.split('BRAND_CSS = """', 1)[1].split('"""', 1)[0]

    def test_no_global_font_rule_on_span(self):
        """全局字体规则不得包含 span 选择器。"""
        css = self._css().replace(" ", "").replace("\n", "")
        assert ".stAppspan{" not in css
        assert ".stAppspan," not in css

    def test_icon_font_is_restored(self):
        """显式声明图标字体，防止后代选择器误伤。"""
        css = self._css()
        assert 'data-testid="stIconMaterial"' in css
        assert "Material Symbols" in css

    def test_text_font_rule_still_present(self):
        """正文仍使用中文字体（修 bug 不能把正文字体也丢掉）。"""
        css = self._css()
        assert "Microsoft YaHei" in css
        assert ".stApp p" in css
