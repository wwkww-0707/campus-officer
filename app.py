"""Campus Officer · 校园事务官 —— Streamlit 界面。

界面承担四件事：
1. 多轮对话（会话可切换、重命名、删除）；
2. **工具调用过程的实时展示**——这是《期末考查要求》中"能看见工具被调用"的可视化落点；
3. 产出物下载区；
4. 当前模型层级与后端健康状态的指示灯。

视觉设计采用统一的设计令牌（CSS 变量），通过 `st.markdown(unsafe_allow_html=True)`
注入企业级卡片、状态条、规划-执行-结果三段式容器，保证整体观感正式、过渡自然。
"""

from __future__ import annotations

import html
import json
import re
import time

import streamlit as st

from core.api_client import BackendUnavailable, get_client
from core.graph import build_graph, list_pending
from core.llm import get_provider
from core.memory import LongTermMemory, SessionStore

st.set_page_config(
    page_title="Campus Officer · 校园事务官",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ===========================================================================
# 设计令牌与企业级视觉系统
# ===========================================================================

BRAND_CSS = """
<style>
/* ============================================================
   设计令牌：颜色 / 间距 / 字阶 / 圆角
   原则：层次用「颜色深浅 + 字号 + 间距」表达，不用大面积色块堆叠
   ============================================================ */
:root{
    /* 文字层级：主 → 次 → 弱 */
    --c-ink:#0f172a; --c-ink2:#334155; --c-mute:#64748b; --c-faint:#94a3b8;
    /* 表面与描边 */
    --c-bg:#ffffff; --c-bg2:#f8fafc; --c-bg3:#f1f5f9; --c-line:#e2e8f0; --c-line2:#cbd5e1;
    /* 主色与语义色（仅用于强调，不铺满） */
    --c-brand:#1d4ed8; --c-brand2:#3b82f6; --c-brand-bg:#eff6ff; --c-brand-line:#bfdbfe;
    --c-ok:#15803d; --c-ok-bg:#f0fdf4; --c-warn:#b45309; --c-warn-bg:#fffbeb;
    --c-err:#be123c; --c-err-bg:#fff1f2;

    /* 间距刻度（4 的倍数） */
    --sp1:4px; --sp2:8px; --sp3:12px; --sp4:16px; --sp5:24px; --sp6:32px;
    /* 字阶 */
    --fs-xs:11px; --fs-sm:12px; --fs-base:13px; --fs-md:14px; --fs-lg:16px; --fs-xl:20px;
    /* 圆角 */
    --r1:6px; --r2:8px; --r3:10px;
}

/* 全局字体与行高：中文优先、数字等宽
   注意：这里**不能**包含 `span`。Streamlit 的图标（展开箭头、侧栏折叠等）
   是用 Material Symbols 字体渲染的 `<span>`，靠字体连字把
   "arrow_drop_down" 变成箭头字形。一旦被中文字体覆盖，连字失效，
   就会把 arrow_drop_down 当普通文字显示出来（表现为"乱码"并与标题重叠）。 */
.stApp, .stApp p, .stApp li, .stApp div{
    font-family:"Microsoft YaHei","PingFang SC","Hiragino Sans GB",-apple-system,sans-serif;
}

/* 显式恢复图标字体，防止任何后代选择器误伤 */
.stApp [data-testid="stIconMaterial"],
.stApp [data-testid="stExpanderToggleIcon"],
.stApp [data-testid="stIcon"],
.stApp .material-symbols-rounded,
.stApp .material-symbols-outlined,
.stApp .material-icons{
    font-family:"Material Symbols Rounded","Material Symbols Outlined",
                "Material Icons",sans-serif !important;
    font-weight:400 !important;
    font-size:20px;
    line-height:1;
    letter-spacing:normal;
    text-transform:none;
    white-space:nowrap;
    /* 图标不参与正文换行，避免与标题文字叠压 */
    flex:0 0 auto;
}
.stApp{ background:var(--c-bg); }
.stApp p, .stApp li { font-size:var(--fs-base); line-height:1.7; color:var(--c-ink2); }
.co-num, .co-metric .v, .co-status-card .value{ font-variant-numeric:tabular-nums; }

/* 统一 Streamlit 原生块间距，避免忽宽忽窄 */
[data-testid="stVerticalBlock"]{ gap:var(--sp3); }
[data-testid="stHorizontalBlock"]{ gap:var(--sp3); }
hr{ margin:var(--sp4) 0; border-color:var(--c-line); }

/* ---------- 顶部品牌栏：纯色 + 细边，去掉花哨光晕 ---------- */
.co-brand-bar{
    background:linear-gradient(100deg,#0f172a 0%,#1e3a8a 100%);
    color:#fff; border-radius:var(--r3); padding:var(--sp5) var(--sp6);
    margin:0 0 var(--sp4) 0;
}
.co-brand-bar .title{ font-size:var(--fs-xl); font-weight:700; letter-spacing:-.2px; }
.co-brand-bar .sub{
    font-size:var(--fs-sm); color:#c7d2fe; margin-top:var(--sp1); line-height:1.6;
}
.co-brand-bar .tags{ margin-top:var(--sp3); display:flex; flex-wrap:wrap; gap:var(--sp2); }
.co-brand-bar .tag{
    background:rgba(255,255,255,.10); border:1px solid rgba(255,255,255,.22);
    padding:2px 10px; border-radius:999px; font-size:var(--fs-xs); color:#e0e7ff;
    letter-spacing:.2px; white-space:nowrap;
}

/* ---------- 状态卡片栏：等高等宽，信息压缩 ---------- */
.co-status-row{
    display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr));
    gap:var(--sp3); margin:0 0 var(--sp5) 0;
}
.co-status-card{
    background:var(--c-bg2); border:1px solid var(--c-line);
    border-radius:var(--r2); padding:var(--sp3) var(--sp3);
}
.co-status-card .label{
    font-size:var(--fs-xs); color:var(--c-faint); letter-spacing:.3px; margin-bottom:2px;
}
.co-status-card .value{
    font-size:var(--fs-md); font-weight:600; color:var(--c-ink);
    display:flex; align-items:center; gap:6px; line-height:1.4;
}
.co-dot{ width:6px; height:6px; border-radius:50%; flex:0 0 6px; }
.co-dot.ok{ background:var(--c-ok); } .co-dot.warn{ background:var(--c-warn); }
.co-dot.err{ background:var(--c-err); } .co-dot.info{ background:var(--c-brand2); }

/* ---------- 分区标题：小字大写 + 上下留白，形成节奏 ---------- */
.co-section{
    font-size:var(--fs-xs); font-weight:600; color:var(--c-faint);
    letter-spacing:.8px; text-transform:uppercase;
    margin:var(--sp5) 0 var(--sp2) 0; padding-bottom:var(--sp1);
    border-bottom:1px solid var(--c-line);
}
.co-divider{ height:1px; background:var(--c-line); margin:var(--sp4) 0; }

/* ---------- 三段式：统一中性底 + 左侧色条区分，降低色彩噪音 ---------- */
.co-plan, .co-exec, .co-result{
    border:1px solid var(--c-line); border-radius:var(--r2);
    padding:var(--sp3) var(--sp4); margin-bottom:var(--sp3);
    background:var(--c-bg);
}
.co-plan{ border-left:3px solid var(--c-brand); background:var(--c-brand-bg); border-color:var(--c-brand-line); }
.co-exec{ border-left:3px solid var(--c-line2); background:var(--c-bg2); }
.co-result{ border-left:3px solid var(--c-ok); }

.co-plan .hd, .co-exec .hd{
    font-size:var(--fs-xs); font-weight:700; letter-spacing:.6px;
    text-transform:uppercase; margin-bottom:var(--sp2);
    display:flex; align-items:center; gap:6px;
}
.co-plan .hd{ color:var(--c-brand); }
.co-exec .hd{ color:var(--c-mute); }

.co-step{ font-size:var(--fs-base); color:var(--c-ink2); line-height:1.65; margin:var(--sp1) 0; }
.co-step b{ color:var(--c-ink); font-weight:600; }
.co-meta{ font-size:var(--fs-xs); color:var(--c-faint); line-height:1.55; margin-top:2px; }
.co-tool-pill{
    display:inline-block; background:#fff; border:1px solid var(--c-line);
    color:var(--c-brand); font-family:"Cascadia Code",Consolas,monospace;
    font-size:10px; padding:1px 7px; border-radius:999px; margin-left:6px;
    vertical-align:middle;
}

/* ---------- 提示条（专业定制说明等）---------- */
.co-note{
    background:var(--c-brand-bg); border:1px solid var(--c-brand-line);
    border-left:3px solid var(--c-brand); border-radius:var(--r1);
    padding:var(--sp2) var(--sp3); margin:var(--sp2) 0;
    font-size:var(--fs-sm); color:var(--c-ink2); line-height:1.6;
}
.co-note b{ color:var(--c-brand); font-weight:600; }

/* ---------- 方案头部（浅色，靠排版建立层次而非色块）---------- */
.co-plan-head{
    border-top:3px solid var(--c-brand);
    background:var(--c-bg); border-right:1px solid var(--c-line);
    border-bottom:1px solid var(--c-line); border-left:1px solid var(--c-line);
    border-radius:0 0 var(--r2) var(--r2);
    padding:var(--sp4); margin:var(--sp3) 0 var(--sp4) 0;
}
.co-plan-head .eyebrow{
    font-size:10px; color:var(--c-faint); letter-spacing:.8px;
    text-transform:uppercase;
}
.co-plan-head .title{
    font-size:var(--fs-xl); font-weight:700; color:var(--c-ink);
    margin-top:var(--sp1); letter-spacing:-.3px;
}
.co-plan-head .desc{ font-size:var(--fs-base); color:var(--c-ink2); margin-top:var(--sp1); }
.co-plan-head .meta{ font-size:var(--fs-sm); color:var(--c-mute); margin-top:var(--sp2); }

/* ---------- 专业定制横幅（浅色，避免与品牌栏两个深色块叠加）---------- */
.co-major-banner{
    background:var(--c-brand-bg); border:1px solid var(--c-brand-line);
    border-left:3px solid var(--c-brand); border-radius:var(--r2);
    padding:var(--sp3) var(--sp4); margin-bottom:var(--sp4);
}
.co-major-banner .eyebrow{
    font-size:10px; color:var(--c-brand); letter-spacing:.8px;
    text-transform:uppercase; font-weight:700;
}
.co-major-banner .title{
    font-size:var(--fs-lg); font-weight:700; color:var(--c-ink);
    margin-top:2px; letter-spacing:-.2px;
}
.co-major-banner .desc{ font-size:var(--fs-sm); color:var(--c-ink2); margin-top:var(--sp1); line-height:1.55; }

/* ---------- 可选择的方案卡 ---------- */
.co-plan-pick{
    background:var(--c-bg2); border:1px solid var(--c-line);
    border-radius:var(--r2); padding:var(--sp3) var(--sp4);
}
.co-plan-pick.active{ background:var(--c-brand-bg); border-color:var(--c-brand); }
.co-plan-pick .t{ font-size:var(--fs-md); font-weight:700; color:var(--c-ink); }
.co-plan-pick .s{ font-size:var(--fs-xs); color:var(--c-mute); margin-top:3px; line-height:1.5; }

/* ---------- 侧栏身份卡：标签列定宽，值右对齐，视觉整齐 ---------- */
.co-identity{
    background:var(--c-bg2); border:1px solid var(--c-line);
    border-radius:var(--r2); padding:var(--sp4);
}
.co-identity .eyebrow{
    font-size:10px; color:var(--c-faint); letter-spacing:.8px;
    text-transform:uppercase; margin-bottom:var(--sp1);
}
.co-identity .name{
    font-size:var(--fs-lg); font-weight:700; color:var(--c-ink);
    margin-bottom:var(--sp3); letter-spacing:-.2px;
}
.co-identity .row{
    display:flex; justify-content:space-between; align-items:baseline;
    gap:var(--sp3); padding:3px 0;
}
.co-identity .row .k{ font-size:var(--fs-xs); color:var(--c-faint); flex:0 0 auto; }
.co-identity .row .v{
    font-size:var(--fs-sm); color:var(--c-ink2); text-align:right;
    line-height:1.5; word-break:break-word;
}
.co-identity .row .v.mono{ font-family:"Cascadia Code",Consolas,monospace; font-size:var(--fs-xs); }

/* ---------- 侧栏卡片 ---------- */
.co-side-card{
    background:var(--c-bg2); border:1px solid var(--c-line);
    border-radius:var(--r2); padding:var(--sp3); margin:var(--sp1) 0;
}
.co-side-card.active{ border-color:var(--c-brand); background:var(--c-brand-bg); }
.co-side-card .t{ font-size:var(--fs-md); font-weight:600; color:var(--c-ink); }
.co-side-card .s{ font-size:var(--fs-xs); color:var(--c-faint); margin-top:2px; }

/* ---------- 侧栏关键指标：三列等宽网格 ---------- */
.co-metric-grid{
    display:grid; grid-template-columns:repeat(3,1fr);
    gap:var(--sp2); margin-top:var(--sp3);
}
.co-metric{
    background:var(--c-bg2); border:1px solid var(--c-line);
    border-radius:var(--r1); padding:var(--sp2) var(--sp2);
    text-align:center;
}
.co-metric .k{ font-size:10px; color:var(--c-faint); letter-spacing:.3px; }
.co-metric .v{
    font-size:var(--fs-lg); font-weight:700; color:var(--c-ink);
    line-height:1.3; margin-top:1px;
}
.co-metric .v.ok{ color:var(--c-ok); }
.co-metric .v.muted{ color:var(--c-faint); font-weight:500; }

/* ---------- 演示话术卡片 ---------- */
.co-demo-card{
    background:var(--c-bg2); border:1px solid var(--c-line); border-radius:var(--r2);
    padding:var(--sp3); height:100%;
}
.co-demo-card .n{
    font-size:10px; color:var(--c-brand); font-weight:700;
    letter-spacing:.5px; text-transform:uppercase;
}
.co-demo-card .q{ font-size:var(--fs-sm); color:var(--c-ink2); margin-top:var(--sp1); line-height:1.55; }
.co-demo-card .h{ font-size:10px; color:var(--c-faint); margin-top:var(--sp1); line-height:1.5; }

/* ---------- 合规声明 ---------- */
.co-compliance{
    font-size:var(--fs-xs); color:var(--c-faint); line-height:1.6;
    border-top:1px solid var(--c-line); padding-top:var(--sp3); margin-top:var(--sp4);
}

/* ---------- 长期记忆：键值行（侧栏窄，必须允许换行）---------- */
.co-mem{ font-size:var(--fs-sm); line-height:1.65; color:var(--c-ink2); }
.co-mem-h{
    font-size:10px; font-weight:700; color:var(--c-brand);
    letter-spacing:.6px; text-transform:uppercase;
    margin:var(--sp3) 0 var(--sp1) 0;
}
.co-mem-h:first-child{ margin-top:0; }
.co-mem-row{
    display:flex; gap:var(--sp2); align-items:flex-start;
    padding:3px 0; border-bottom:1px dashed var(--c-line);
    /* 关键：长文本正常换行，不撑破侧栏 */
    white-space:normal; overflow-wrap:break-word; word-break:break-word;
}
.co-mem-row:last-child{ border-bottom:none; }
.co-mem-row .k{
    flex:0 0 auto; color:var(--c-mute); font-size:var(--fs-xs);
    padding-top:2px;
}
.co-mem-row .v{ flex:1 1 auto; min-width:0; color:var(--c-ink); }
.co-mem-p{
    padding:3px 0; color:var(--c-ink2);
    white-space:normal; overflow-wrap:break-word;
}
.co-mem em{ color:var(--c-mute); font-style:normal; font-size:var(--fs-xs); }

/* ---------- 工具清单：窄侧栏下用列表替代表格 ---------- */
.co-tool{
    padding:var(--sp2) 0; border-bottom:1px dashed var(--c-line);
}
.co-tool:last-child{ border-bottom:none; }
.co-tool .n{
    display:flex; align-items:center; gap:var(--sp2);
    font-family:"Cascadia Code",Consolas,monospace;
    font-size:var(--fs-xs); color:var(--c-ink); font-weight:600;
    white-space:normal; overflow-wrap:anywhere;
}
.co-tool .badge{
    flex:0 0 auto; font-family:inherit; font-size:10px; font-weight:500;
    padding:0 6px; border-radius:999px; line-height:16px;
    border:1px solid var(--c-line); color:var(--c-mute); background:var(--c-bg2);
}
.co-tool .badge.mcp{ color:var(--c-brand); border-color:#bfdbfe; background:var(--c-brand-bg); }
.co-tool .d{
    font-size:var(--fs-xs); color:var(--c-mute); line-height:1.55;
    margin-top:2px; white-space:normal; overflow-wrap:break-word;
}

/* ---------- 表格 ---------- */
.stDataFrame, .stTable{ border-radius:var(--r2); overflow:hidden; }

/* 隐藏默认元素 */
#MainMenu{ visibility:hidden; }
footer{ visibility:hidden; }
[data-testid="stSidebarCollapseButton"]{ display:none; }
</style>
"""


def _inject_css() -> None:
    """注入全局设计令牌与企业级样式。"""
    st.markdown(BRAND_CSS, unsafe_allow_html=True)


def _brand_bar(tags: list[str]) -> None:
    """渲染顶部品牌栏。"""
    tag_html = "".join(f'<span class="tag">{t}</span>' for t in tags)
    # 注意：HTML 必须顶格写。st.markdown 的内容若缩进 ≥4 空格，
    # 会被 Markdown 解析成「代码块」，整块 HTML 以源码文本显示在页面上
    # （表现为：顶部品牌栏一片空白、右侧出现 <div class=...> 字样）。
    st.markdown(
        f"""
<div class="co-brand-bar">
<div class="title">Campus Officer · 校园事务官</div>
<div class="sub">面向本校本科生的教务事务智能体 · 选课冲突裁决 → 奖助学金材料 → 学习规划 一条事务链走通</div>
<div class="tags">{tag_html}</div>
</div>
""",
        unsafe_allow_html=True,
    )


def _status_cards(items: list[dict]) -> None:
    """渲染顶部状态卡片栏（HTML 同样必须顶格，原因见 _render_brand_bar）。"""
    cards = "".join(
        f"""
<div class="co-status-card">
<div class="label">{c["label"]}</div>
<div class="value"><span class="co-dot {c.get("dot","info")}"></span>{c["value"]}</div>
</div>
"""
        for c in items
    )
    st.markdown(f'<div class="co-status-row">{cards}</div>', unsafe_allow_html=True)


def _section_title(title: str) -> None:
    """渲染分区小标题。"""
    st.markdown(f'<div class="co-section">{title}</div>', unsafe_allow_html=True)


def _summarize_title(text: str, max_len: int = 16) -> str:
    """从一段话里生成会话标题（截断 + 去换行），模拟对话式 AI 的标题总结。"""
    text = text.strip().replace("\n", " ")
    if not text:
        return "新会话"
    return text[:max_len] + ("…" if len(text) > max_len else "")


def _inline_md_html(text: str) -> str:
    """把一行 Markdown 行内标记转成安全 HTML。

    - `**x**` 转成加粗（用于强调关键结论）
    - 其余字符先做 HTML 转义，避免内容里的 `<` `>` 破坏布局
    """
    out = html.escape(text)
    out = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out)
    out = out.replace("`", "")
    return out


def _memory_to_html(markdown_text: str) -> str:
    """把长期记忆 Markdown 渲染成侧栏可读的键值卡。

    **为什么不用 st.text()**：`st.text` 渲染成等宽 `<pre>`，既保留 `##`/`**`
    这些原始符号，又默认不折行。侧栏只有 ~300px 宽，中文长句会被横向截断，
    看上去就是"乱码"。这里显式解析成「小节标题 + 键值行」，并允许自动换行。
    """
    rows: list[str] = []
    for raw_line in markdown_text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue

        # 小节标题：## 学生画像 / # 标题
        if line.startswith("#"):
            rows.append(
                f'<div class="co-mem-h">{_inline_md_html(line.lstrip("#").strip())}</div>'
            )
            continue

        # 引用块：> 说明文字（弱化显示）
        if line.startswith(">"):
            rows.append(f'<div class="co-mem-p"><em>{_inline_md_html(line.lstrip("> ").strip())}</em></div>')
            continue

        # 列表项：拆成「键 / 值」两列，键弱化、值醒目
        if line.lstrip().startswith("- "):
            item = line.lstrip()[2:].strip()
            sep = "：" if "：" in item else (":" if ":" in item else "")
            # 键通常是 2~12 个汉字（如「每周期可支配课外时间」），
            # 超过这个长度说明整句都是正文，按段落渲染
            if sep and item.index(sep) <= 12:
                key, val = item.split(sep, 1)
                rows.append(
                    f'<div class="co-mem-row"><span class="k">{_inline_md_html(key.strip())}</span>'
                    f'<span class="v">{_inline_md_html(val.strip())}</span></div>'
                )
            else:
                rows.append(f'<div class="co-mem-p">{_inline_md_html(item)}</div>')
            continue

        rows.append(f'<div class="co-mem-p">{_inline_md_html(line.strip())}</div>')

    body = "".join(rows) or '<div class="co-mem-p"><em>（暂无内容）</em></div>'
    return f'<div class="co-mem">{body}</div>'


def _render_tool_manifest(tools: list[dict]) -> None:
    """渲染工具清单。

    **为什么不用 st.dataframe**：三列表格放进窄侧栏会被压扁、文字互相覆盖，
    看起来像乱码。改为纵向列表，每条一行工具名 + 类型徽章 + 作用说明，
    可自然换行。
    """
    blocks: list[str] = []
    for t in tools:
        kind = str(t.get("kind", ""))
        badge_cls = "badge mcp" if "MCP" in kind.upper() else "badge"
        blocks.append(
            f'<div class="co-tool">'
            f'<div class="n">{html.escape(str(t.get("name", "")))}'
            f'<span class="{badge_cls}">{html.escape(kind)}</span></div>'
            f'<div class="d">{html.escape(str(t.get("purpose", "")))}</div>'
            f'</div>'
        )
    st.markdown("".join(blocks), unsafe_allow_html=True)


def _render_announcement_card(a: dict) -> None:
    """渲染一条公告卡片。

    url 存在时，卡片标题与底部都会渲染成**可直接点击跳转官网原文**的链接：
    - 标题整行可点（最符合用户直觉）
    - 底部保留显式的「查看原文 ↗」入口，避免用户不知道标题能点
    """
    cat = a.get("category", "其他")
    cat_icon = {"政策": "📜", "活动": "🎪", "通知": "📢", "讲座": "🎤", "新闻": "📰"}.get(cat, "📌")
    title = a.get("title", "")
    edate = a.get("event_date", "")
    content = a.get("content", "")
    source = a.get("source", "")
    url = (a.get("url") or "").strip()

    # 标题：有链接则整行可点击，并加 hover 下划线样式
    if url:
        title_html = (
            f'<a href="{url}" target="_blank" rel="noopener noreferrer" '
            f'style="font-weight:600;color:var(--c-ink);text-decoration:none" '
            f'onmouseover="this.style.color=\'#1d4ed8\';this.style.textDecoration=\'underline\'" '
            f'onmouseout="this.style.color=\'#0f172a\';this.style.textDecoration=\'none\'">'
            f'{cat_icon} {title}</a>'
        )
    else:
        title_html = f'<div style="font-weight:600">{cat_icon} {title}</div>'

    parts = [
        f'<div style="font-size:12px;margin:7px 0;padding:8px 10px;'
        f'border:1px solid var(--c-line);border-radius:6px;background:#fff">',
        title_html,
    ]
    if edate:
        parts.append(
            f'<div style="color:var(--c-brand);font-weight:600;margin-top:3px">📅 {edate}</div>'
        )
    if content:
        parts.append(
            f'<div style="color:var(--c-ink2);margin-top:3px;line-height:1.5">{content[:100]}</div>'
        )
    foot = []
    if source:
        foot.append(source)
    # 显式的「查看原文」入口：新标签页打开，不打断当前应用
    if url:
        foot.append(
            f'<a href="{url}" target="_blank" rel="noopener noreferrer" '
            f'style="color:var(--c-brand);text-decoration:none;font-weight:600">'
            f'查看原文 ↗</a>'
        )
    if foot:
        parts.append(
            f'<div style="color:var(--c-mute);font-size:10px;margin-top:4px">'
            + " · ".join(foot)
            + "</div>"
        )
    parts.append("</div>")
    st.markdown("".join(parts), unsafe_allow_html=True)


# ===========================================================================
# 初始化
# ===========================================================================

if "store" not in st.session_state:
    st.session_state.store = SessionStore()
if "memory" not in st.session_state:
    st.session_state.memory = LongTermMemory()
if "session_id" not in st.session_state:
    # 不自动创建会话：恢复最近一次会话；库为空则留空，等首次发消息或点「新建」时再建。
    sessions = st.session_state.store.list_sessions()
    st.session_state.session_id = sessions[0].id if sessions else None
if "tool_events" not in st.session_state:
    st.session_state.tool_events = []
if "pending_op" not in st.session_state:
    st.session_state.pending_op = None

store: SessionStore = st.session_state.store
memory: LongTermMemory = st.session_state.memory
provider = get_provider()
status = provider.status()

# ===========================================================================
# 注入视觉系统
# ===========================================================================

_inject_css()

# ===========================================================================
# 顶部品牌栏 + 状态卡片
# ===========================================================================

try:
    backend_ok = get_client().health()
except Exception:
    backend_ok = False

# 用一次 summary 拿数据
try:
    summary = get_client().summary() if backend_ok else {"total_courses": 0, "total_enrolled": 0, "enrolled_count": 0, "full_courses": 0}
except Exception:
    summary = {"total_courses": 0, "total_enrolled": 0, "enrolled_count": 0, "full_courses": 0}

tags = ["路径 A · 技术栈完整", "8 工具 + 1 MCP", "2 子代理", "三层降级"]
_brand_bar(tags)

status_cards = [
    {
        "label": "模型接入",
        "value": status.display,
        "dot": "ok" if status.is_real_model else "warn",
    },
    {
        "label": "教务后端",
        "value": "运行中" if backend_ok else "未连接",
        "dot": "ok" if backend_ok else "err",
    },
    {
        "label": "课程库",
        "value": f"{summary.get('total_courses', 0)} 门" if backend_ok else "—",
        "dot": "info",
    },
    {
        "label": "满员课程",
        "value": f"{summary.get('full_courses', 0)} 门" if backend_ok else "—",
        "dot": "info",
    },
    {
        "label": "已选学分",
        "value": f"{summary.get('total_enrolled', 0)}" if backend_ok else "—",
        "dot": "info",
    },
]
_status_cards(status_cards)

if not status.is_real_model:
    st.info(
        "当前未检测到可用大模型（无 DASHSCOPE_API_KEY 或网络不通），已自动切换到"
        "规则降级引擎。**工具调用与业务数据仍为真实**，只是语言回复为模板化。"
        "在 `.env` 中配置 Key 后重启可获得自然语言交互。"
    )
if not backend_ok:
    st.error(
        "教务业务后端未连接。请在项目目录运行 `python backend/main.py` "
        "（或 `start.bat`）启动后端后重试。"
    )

# ===========================================================================
# 侧边栏
# ===========================================================================

# 提前取一次数据（供顶部学生卡片与各标签页复用，避免重复请求）
try:
    prof = get_client().get_current_profile()
except Exception:
    prof = None

# 长期记忆里的「学生画像」跟随当前档案刷新：
# 否则学生改了自己的学号/专业，侧栏长期记忆仍显示最初那份演示档案（学号 20260001），
# 看起来就像"改了没生效"。内容无变化时不写盘。
if prof:
    try:
        st.session_state.memory.sync_profile(prof)
    except Exception:
        pass
try:
    announcements = get_client().list_announcements().get("announcements", [])
except Exception:
    announcements = []

with st.sidebar:
    # ── 学生身份卡片（学校 / 学院 / 学号 / 年级专业）──
    if prof is None:
        st.caption("后端未连接，无法读取档案。")
    else:
        school = prof.get("school", "杉达学院")
        dept = prof.get("department", "")
        sid = prof.get("student_id", "")
        name = prof.get("name", "示例同学")
        grade = prof.get("grade", "")
        st.markdown(
            f'<div class="co-identity">'
            f'<div class="eyebrow">学生身份</div>'
            f'<div class="name">{name}</div>'
            f'<div class="row"><span class="k">学校</span><span class="v">{school}</span></div>'
            f'<div class="row"><span class="k">专业</span><span class="v">{dept}</span></div>'
            f'<div class="row"><span class="k">年级</span><span class="v">{grade}</span></div>'
            f'<div class="row"><span class="k">学号</span><span class="v mono">{sid}</span></div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        earned = prof.get("credit_earned", 0)
        required = prof.get("credit_required", 165)
        rank = prof.get("class_rank_ratio")
        pct = min(1.0, earned / required) if required else 0.0
        st.progress(pct, text=f"已修 {earned:.0f} / {required:.0f} 学分")

        # 关键指标：三列等宽网格，替代原来 " · " 拼接的一行，
        # 层次更清晰、对齐更整齐（已移除无实际用途的"周可用时长"）
        gpa_val = f"{prof.get('gpa', 0)}"
        rank_val = f"前 {int(rank * 100)}%" if rank is not None else "—"
        diff_val = "已认定" if prof.get("family_difficulty") else "未认定"
        diff_cls = "ok" if prof.get("family_difficulty") else "muted"
        st.markdown(
            f'<div class="co-metric-grid">'
            f'<div class="co-metric"><div class="k">GPA</div>'
            f'<div class="v">{gpa_val}</div></div>'
            f'<div class="co-metric"><div class="k">专业排名</div>'
            f'<div class="v">{rank_val}</div></div>'
            f'<div class="co-metric"><div class="k">困难认定</div>'
            f'<div class="v {diff_cls}" style="font-size:13px">{diff_val}</div></div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        # 绑定学校 / 编辑档案
        if st.session_state.get("editing_profile", False):
            with st.form("profile_form"):
                st.caption("基本身份")
                new_school = st.text_input("学校", value=school)
                new_name = st.text_input("姓名", value=name)
                new_sid = st.text_input("学号", value=sid)
                new_grade = st.text_input("年级", value=grade)
                new_dept = st.text_input("学院/专业", value=dept)

                # ---- 学业与认定：直接影响奖助学金资格判定，必须可编辑 ----
                st.caption("学业与认定（影响奖助学金资格初筛）")
                c_gpa, c_rank = st.columns(2)
                new_gpa = c_gpa.number_input(
                    "GPA", min_value=0.0, max_value=5.0,
                    value=float(prof.get("gpa") or 0.0), step=0.1, format="%.2f",
                )
                # 排名以百分比呈现（档案存 0.08 表示前 8%）；0 表示未核定
                _rank_now = prof.get("class_rank_ratio")
                new_rank = c_rank.number_input(
                    "专业排名（%，0=未核定）", min_value=0.0, max_value=100.0,
                    value=float(_rank_now * 100) if _rank_now is not None else 0.0,
                    step=1.0, format="%.0f",
                )
                c_earned, c_required = st.columns(2)
                new_earned = c_earned.number_input(
                    "已修学分", min_value=0.0, max_value=400.0,
                    value=float(prof.get("credit_earned") or 0.0), step=1.0,
                )
                new_required = c_required.number_input(
                    "要求学分", min_value=1.0, max_value=400.0,
                    value=float(prof.get("credit_required") or 165.0), step=1.0,
                )
                c_diff, c_pol = st.columns(2)
                new_diff = c_diff.checkbox(
                    "家庭经济困难认定", value=bool(prof.get("family_difficulty")),
                    help="勾选表示已通过学校家庭经济困难认定，国家励志奖学金/助学金需要此项",
                )
                new_pol = c_pol.checkbox(
                    "政审合格", value=bool(prof.get("political_review")),
                    help="国家奖学金、励志奖学金等需要政审合格",
                )

                save_c, cancel_c = st.columns(2)
                if save_c.form_submit_button("💾 保存", type="primary"):
                    prof["school"] = new_school.strip()
                    prof["name"] = new_name.strip()
                    prof["student_id"] = new_sid.strip()
                    prof["grade"] = new_grade.strip()
                    prof["department"] = new_dept.strip()
                    prof["gpa"] = float(new_gpa)
                    # 0 视为"未核定"，写 None（资格判定时该维度不作判定）
                    prof["class_rank_ratio"] = (
                        round(float(new_rank) / 100.0, 4) if new_rank > 0 else None
                    )
                    prof["credit_earned"] = float(new_earned)
                    prof["credit_required"] = float(new_required)
                    prof["family_difficulty"] = bool(new_diff)
                    prof["political_review"] = bool(new_pol)
                    if not prof["student_id"]:
                        st.toast("学号不能为空")
                    else:
                        try:
                            get_client().save_profile(prof)
                            st.toast("档案已更新")
                            st.session_state.editing_profile = False
                            st.rerun()
                        except Exception as exc:
                            st.toast(f"保存失败：{exc}")
                if cancel_c.form_submit_button("❌ 取消"):
                    st.session_state.editing_profile = False
                    st.rerun()
        else:
            if st.button("✏️ 绑定学校 / 编辑", key="profile_edit_btn", use_container_width=True):
                st.session_state.editing_profile = True
                st.rerun()


# ── 三个标签页：日程 / 学习方案 / 会话 ──
tab_dl, tab_course, tab_sess = st.tabs(["⏰ 日程", "📚 学习方案", "🗂 会话"])

with tab_dl:
    # ── 同步学校信息源 ──
    sync_col1, sync_col2 = st.columns([3, 1])
    with sync_col1:
        st.markdown(
            '<div style="font-size:11px;color:var(--c-mute)">'
            '同步杉达学院官网（news.sandau.edu.cn）的最新通知 / 讲座 / 活动</div>',
            unsafe_allow_html=True,
        )
    with sync_col2:
        if st.button("🔄 同步官网", use_container_width=True, key="sandau_sync_btn"):
            with st.spinner("正在抓取官网信息…"):
                try:
                    result = get_client().sync_sandau()
                    st.toast(f"同步完成：新增 {result.get('added', 0)} 条")
                    st.rerun()
                except Exception as exc:
                    st.toast(f"同步失败：{exc}")

    # ── 学校公告（新政策 / 活动 / 通知）──
    if not announcements:
        st.caption("暂无公告。点上方「🔄 同步官网」拉取杉达学院最新信息，或下方「➕ 发布公告」手动录入。")
    else:
        # 活动按日期分组展示，政策/通知直接列出
        dated = [a for a in announcements if a.get("event_date")]
        undated = [a for a in announcements if not a.get("event_date")]
        for a in dated[:12]:
            _render_announcement_card(a)
        for a in undated[:12]:
            _render_announcement_card(a)

    # ── 录入公告 ──
    with st.expander("➕ 发布公告", expanded=False):
        with st.form("ann_form"):
            ann_title = st.text_input("标题")
            ann_cat = st.selectbox("类别", ["政策", "活动", "通知", "其他"])
            ann_date = st.text_input("活动日期（活动类填写，格式 YYYY-MM-DD）", value="")
            ann_content = st.text_area("正文", height=90)
            ann_source = st.text_input("来源（如 学校官网 / 教务处）")
            ann_url = st.text_input("原文链接（可选，填写后卡片可直接点击跳转）")
            if st.form_submit_button("发布", type="primary"):
                if ann_title.strip():
                    try:
                        get_client().add_announcement(
                            title=ann_title.strip(),
                            category=ann_cat,
                            content=ann_content,
                            source=ann_source,
                            event_date=ann_date.strip(),
                            url=ann_url.strip(),
                        )
                        st.toast("公告已发布")
                        st.rerun()
                    except Exception as exc:
                        st.toast(f"发布失败：{exc}")
                else:
                    st.toast("标题不能为空")

with tab_course:
    # ── 学习方案：基于学生档案的「专业」定制，点击卡片立即在本页生成 ──
    from core.learning_plans import (
        TOPIC_GROUPS,
        generate_learning_plan,
        generate_major_plan,
    )
    from core.major_profiles import grade_stage, match_major

    # 读档案拿专业 / 年级 / 兴趣（后端不可用时降级为通用方案）
    try:
        _profile = get_client().get_current_profile()
    except Exception:
        _profile = {}
    _major = _profile.get("department", "") or ""
    _grade = _profile.get("grade", "") or ""
    _interests = _profile.get("interests", []) or []
    _stage = grade_stage(_grade)
    _mprof = match_major(_major)

    # 专业定制横幅：把"为什么这套方案是给你的"讲清楚
    if _major:
        _matched = _mprof.name != "通用"
        st.markdown(
            f'<div class="co-major-banner">'
            f'<div class="eyebrow">已按你的档案定制</div>'
            f'<div class="title">'
            f'{_major}{f" · {_grade}" if _grade else ""}{f" · 当前{_stage}" if _stage else ""}</div>'
            f'<div class="desc">'
            f'{"兴趣方向：" + "、".join(_interests) if _interests else ""}'
            f'{"　｜　已匹配「" + _mprof.name + "」专业画像，方案按该专业的核心课与关键能力生成" if _matched else "　｜　该专业暂无专用画像，已按通用模板生成"}'
            f'</div>',
            unsafe_allow_html=True,
        )

    # ── 第一组：专业学习方案（最突出） ──
    st.markdown(
        '<div class="co-section">🎯 你的专业学习方案</div>',
        unsafe_allow_html=True,
    )
    _major_active = st.session_state.get("learning_topic") == "__major__"
    # 卡片标题用**学生自己填写的专业名**；若命中的画像名与填写值不同，
    # 在副标题里注明"按哪个画像生成"，避免出现"我写大数据却显示计算机"的误解。
    _display_major = _major or _mprof.name
    _pick_sub = "按专业核心课与关键能力生成，从当前学年排到毕业"
    if _major and _mprof.name != "通用" and _mprof.name != _major:
        _pick_sub = f"已匹配「{_mprof.name}」专业画像 · " + _pick_sub
    mc1, mc2 = st.columns([3, 1], vertical_alignment="center")
    with mc1:
        st.markdown(
            f'<div class="co-plan-pick{" active" if _major_active else ""}">'
            f'<div class="t">🎓 {_display_major} · 核心能力强化</div>'
            f'<div class="s">{_pick_sub}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    with mc2:
        if st.button("生成专业方案", key="plan___major__", use_container_width=True):
            st.session_state["learning_topic"] = "__major__"
            st.rerun()

    # ── 第二组：升学与考证方案（结合专业增强） ──
    st.markdown(
        '<div class="co-section">📌 升学与考证方案</div>',
        unsafe_allow_html=True,
    )
    st.caption(
        f"以下方案已叠加「{_display_major}」的专业建议（本专业该考什么、该抓什么），"
        "并附知识库中的报名与考试时间节点。"
        if _major else "以下为通用方案，填写专业后可获得针对性建议。"
    )

    # 按主题分组渲染：技术认证一组、升学与通用一组，组内每行 3 张。
    # 挤在一行会让「计算机等级考试」「阿里云 ACP」这类长标签被压成竖排单字；
    # 全部平铺 11 张又会让用户找不到重点，所以先分组再分行。
    PER_ROW = 3
    for group_name, plan_cards in TOPIC_GROUPS:
        st.markdown(
            f'<div style="font-size:var(--fs-sm);font-weight:600;'
            f'color:var(--c-ink-2);margin:var(--sp2) 0 var(--sp1)">{group_name}</div>',
            unsafe_allow_html=True,
        )
        for start in range(0, len(plan_cards), PER_ROW):
            row_cards = plan_cards[start:start + PER_ROW]
            cols = st.columns(PER_ROW)
            for (name, icon), col in zip(row_cards, cols):
                active = st.session_state.get("learning_topic") == name
                with col:
                    st.markdown(
                        f'<div class="co-plan-pick{" active" if active else ""}" '
                        f'style="text-align:center;padding:var(--sp3) var(--sp2)">'
                        f'<div style="font-size:22px;line-height:1.2">{icon}</div>'
                        f'<div style="font-size:var(--fs-base);font-weight:600;'
                        f'color:var(--c-ink);margin-top:var(--sp1);'
                        f'white-space:nowrap">{name}</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                    if st.button("生成方案", key=f"plan_{name}", use_container_width=True):
                        st.session_state["learning_topic"] = name
                        st.rerun()

    # ---- 点击后：立即生成并渲染完整方案 ----
    topic = st.session_state.get("learning_topic")
    if topic == "__major__":
        plan = generate_major_plan(_major, _grade)
    elif topic:
        plan = generate_learning_plan(topic, _major, _grade)
    else:
        plan = None

    if plan:

        # 方案头部
        st.markdown(
            f'<div class="co-plan-head">'
            f'<div class="eyebrow">已生成学习方案</div>'
            f'<div class="title">{plan.topic}</div>'
            f'<div class="desc">{plan.headline}</div>'
            f'<div class="meta">总周期：{plan.total_duration}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        # 专业针对性提示（仅当方案带专业画像时出现）
        if plan.major_name and plan.major_name != "通用":
            _advice = (
                f'<div style="margin-top:6px">{plan.major_advice}</div>'
                if plan.major_advice else ""
            )
            # 命中的画像与填写值不同时，标明用的是哪套画像，避免"改了专业没变"的误解
            _profile_hint = (
                f"　·　按「{plan.profile_name}」专业画像"
                if plan.profile_name and plan.profile_name not in ("通用", plan.major_name)
                else ""
            )
            if plan.profile_name == "通用":
                # 没有专用画像时如实说明，不假装"已定制"
                st.markdown(
                    f'<div class="co-note">'
                    f'<b>🎓 暂未内置「{plan.major_name}」专用画像</b>'
                    f'　·　已按通用专业模板生成，核心课与证书建议以学校培养方案为准'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    f'<div class="co-note">'
                    f'<b>🎓 已针对「{plan.major_name}」定制</b>'
                    f'{_profile_hint}'
                    f'{"　·　当前" + plan.current_stage if plan.current_stage else ""}'
                    f'{_advice}'
                    f'</div>',
                    unsafe_allow_html=True,
                )

        # 三列：分阶段步骤 | 内容安排与时间 | 关键节点与资源
        c_steps, c_time, c_res = st.columns([1.5, 1.2, 1])

        with c_steps:
            # 专业方案：核心课与能力清单放最前，这是"针对性"的核心
            if plan.major_focus:
                st.markdown("**🎯 本专业核心课与关键能力**")
                with st.container(border=True):
                    for item in plan.major_focus[:6]:
                        st.markdown(f"- {item}")
            st.markdown("**📚 分阶段学习步骤**")
            for ph in plan.phases:
                with st.container(border=True):
                    st.markdown(f"**{ph.title}**")
                    st.caption(f"周期：{ph.duration}")
                    for a in ph.actions:
                        st.markdown(f"- {a}")

        with c_time:
            st.markdown("**⏱ 每周时间投入**")
            for ph in plan.phases:
                with st.container(border=True):
                    st.markdown(f"**{ph.title}**")
                    st.markdown(ph.weekly_hours or "—")
            if plan.tips:
                st.markdown("**💡 备考要点**")
                for t in plan.tips[:4]:
                    st.markdown(f"- {t}")

        with c_res:
            if plan.key_dates:
                st.markdown("**📅 关键时间节点**")
                for d in plan.key_dates:
                    st.markdown(f"- {d}")
                st.caption("以上节点检索自知识库条款，具体以官方最新通知为准。")
            st.markdown("**🔗 资源信息**")
            for r in plan.resources:
                st.markdown(f"**{r['name']}**")
                st.caption(f"{r['note']} · {r['source']}")
            if plan.doc_refs:
                st.markdown("**📋 依据条款**")
                for ref in plan.doc_refs:
                    st.caption(ref)

        # 一键转入对话（保留原有能力：继续向智能体追问细节）
        st.markdown("---")
        c_ask1, c_ask2 = st.columns([3, 1])
        question_map = {
            "考研": "帮我制定考研的学习方案和时间规划",
            "实习": "帮我规划一份实习方案和时间安排",
            "四六级": "四六级怎么备考，给我个学习计划",
            "计算机等级考试": "计算机二级怎么备考",
            "教师资格证": "教师资格证怎么备考",
            "HCIA 认证": "我想考 HCIA 证书，给我一个学习方案和时间规划",
            "软考": "我想考软考，给我一个学习方案，并说明该报哪个科目",
            "CDA": "我想考 CDA 数据分析师，给我一个学习方案",
            "阿里云 ACP": "我想考阿里云 ACP 认证，给我一个学习方案",
            "普通话": "普通话水平测试怎么备考，给我一个训练计划",
            "雅思托福": "我想考雅思/托福，给我一个备考方案",
            "__major__": f"{_major}专业该怎么学，给我个学习方案",
        }
        c_ask1.caption("需要更个性化的安排？把方案转入对话，智能体可结合你的课表与时间预算继续细化。")
        if c_ask2.button("转入对话 ›", key="to_chat", use_container_width=True):
            st.session_state["pending_input"] = question_map.get(
                topic, f"帮我制定{plan.topic}学习方案"
            )
            st.rerun()
    else:
        st.info("👆 点击上方任意方案卡片，即刻生成完整学习方案（分阶段步骤 / 时间投入 / 关键节点 / 资源）。")

with tab_sess:
    col_a, col_b = st.columns(2)
    if col_a.button("➕ 新建", use_container_width=True):
        cur = st.session_state.session_id
        # 当前会话若仍是空的（未开始），直接复用它，不重复新建。
        # 否则连续点"新建"会堆出一串空会话。
        cur_is_empty = any(
            s.id == cur and s.message_count == 0 for s in store.list_sessions()
        )
        if cur_is_empty:
            st.toast("当前已是新会话，直接输入即可")
        else:
            st.session_state.session_id = store.create_session("新会话")
            st.session_state.tool_events = []
        st.rerun()

    sessions = store.list_sessions()
    # 显示**全部**会话。
    # 之前用「有消息 or 是当前会话」过滤，导致新建的空会话一切走就消失，
    # 用户会以为"新建的会话丢了"。空会话明确标注「未开始」即可。
    if not sessions:
        st.caption("暂无对话，输入问题即可开始。")
    for s in sessions:
        is_current = s.id == st.session_state.session_id
        marker = "● " if is_current else "○ "
        if s.message_count > 0:
            label = f"{marker}{s.title}"
            hint = f"{s.message_count} 条 · {s.updated_at[5:16]}"
        else:
            label = f"{marker}新会话"
            hint = "未开始 · 等待提问"
        # 每行：左侧切换按钮 + 右侧删除按钮
        row_a, row_b = st.columns([4, 1], vertical_alignment="center")
        with row_a:
            if st.button(label, key=f"ses_{s.id}", use_container_width=True):
                st.session_state.session_id = s.id
                st.session_state.tool_events = []
                st.rerun()
            st.caption(hint)
        if row_b.button("🗑", key=f"del_{s.id}", help="删除该会话"):
            store.delete_session(s.id)
            # 若删除的是当前会话，切到剩余最近一条（或无会话）
            remaining = store.list_sessions()
            if s.id == st.session_state.session_id:
                st.session_state.session_id = remaining[0].id if remaining else None
            st.session_state.tool_events = []
            st.rerun()

with st.sidebar:
    _section_title("智能体能力")

    with st.expander("🧠 长期记忆（AGENTS.md）"):
        # 编辑态开关
        editing = st.session_state.get("editing_memory", False)
        if not editing:
            # 用键值卡渲染，而不是 st.text()：后者不折行，窄侧栏里中文会横向截断
            st.markdown(_memory_to_html(memory.summary()), unsafe_allow_html=True)
            st.caption("跨会话保留的学生画像与已办事项，由系统自动维护。")
            if st.button("✏️ 编辑长期记忆", key="mem_edit_btn", use_container_width=True):
                st.session_state.editing_memory = True
                st.rerun()
        else:
            full = st.text_area(
                "编辑长期记忆（Markdown）",
                value=memory.read(),
                height=280,
                key="mem_editor",
            )
            c1, c2 = st.columns(2)
            if c1.button("💾 保存", key="mem_save", type="primary", use_container_width=True):
                memory.write(full)
                st.session_state.editing_memory = False
                st.toast("长期记忆已更新")
                st.rerun()
            if c2.button("❌ 取消", key="mem_cancel", use_container_width=True):
                st.session_state.editing_memory = False
                st.rerun()

    with st.expander("📋 工具与能力清单"):
        from tools.registry import TOOL_MANIFEST

        # 纵向列表：表格放进窄侧栏会被压扁、文字互相覆盖
        _render_tool_manifest(list(TOOL_MANIFEST))
        st.caption(f"共 {len(TOOL_MANIFEST)} 个能力（本地工具 + MCP 工具），均为真实调用。")

    st.markdown(
        '<div class="co-compliance">'
        '⚠️ 本系统数据为教学演示用虚构样例，涉及政策结论一律以'
        '<b>学校官方最新文件为准</b>。'
        '</div>',
        unsafe_allow_html=True,
    )

# ===========================================================================
# 主区：演示话术 + 聊天
# ===========================================================================

# 演示话术卡片
_section_title("推荐演示话术")
demos = [
    ("选课组合", "下学期能选哪些课？避开周三下午，挑 3 门最值得上的，别超 20 学分",
     "4 工具 + 回溯求解"),
    ("资格初筛", "我 GPA 3.6，能申请哪些奖助学金？要交什么材料？什么时候截止？",
     "逐项比对 + 文号溯源"),
    ("冲突检测", "《人工智能导论》和《数据结构与算法》冲突吗？",
     "闭区间求交，可复现"),
    ("学习规划", "帮我排一份每周学习计划",
     "时间预算四象限"),
    ("产出物", "帮我把选课方案整理成一份清单文件",
     "生成 Markdown 文件"),
]
demo_cols = st.columns(len(demos))
for idx, ((label, text, hint), col) in enumerate(zip(demos, demo_cols), 1):
    with col:
        st.markdown(
            f'<div class="co-demo-card">'
            f'<div class="n">话术 {idx}</div>'
            f'<div class="q" style="font-weight:600;color:var(--c-ink)">{label}</div>'
            f'<div class="h">{hint}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        if col.button("填入", key=f"demo_btn_{label}", use_container_width=True):
            st.session_state["pending_input"] = text
            st.rerun()

_section_title("对话")


# ===========================================================================
# 渲染辅助函数（三段式）—— 必须在历史回显之前定义
# ===========================================================================

def _render_plan(plan: dict | None) -> None:
    """渲染 🧭 规划段。"""
    if not plan:
        return
    steps = plan.get("steps", [])
    source_label = (
        "大模型自主拆解" if plan.get("source") == "model"
        else "规则映射（无 Key 时仍可规划）"
    )
    rows = "".join(
        f'<div class="co-step">{i}. <b>{s.get("title", "")}</b>'
        + (f'<span class="co-tool-pill">{s["tool"]}</span>' if s.get("tool") else "")
        + f'<div class="co-meta">{s.get("action", "")}</div></div>'
        for i, s in enumerate(steps, 1)
    )
    st.markdown(
        f'<div class="co-plan">'
        f'<div class="hd">🧭 规划 · {source_label}</div>'
        f'<div class="co-step"><b>目标</b>：{plan.get("intent", "")}</div>'
        f'{rows}'
        f'</div>',
        unsafe_allow_html=True,
    )


def _render_exec(events: list[dict]) -> None:
    """渲染 🔧 执行段。"""
    if not events:
        return
    rows = ""
    for ev in events:
        args = json.dumps(ev.get("args", {}), ensure_ascii=False)
        icon = "✅" if ev.get("ok", True) else "❌"
        rows += (
            f'<div class="co-step"><span class="co-tool-pill">{ev.get("tool","")}</span>'
            f' {icon} {ev.get("elapsed_ms",0)}ms</div>'
            f'<div class="co-meta">入参：{args[:140]}</div>'
            f'<div class="co-meta">返回：{ev.get("summary","")[:240]}</div>'
        )
    st.markdown(
        f'<div class="co-exec"><div class="hd">🔧 执行 · 工具调用轨迹</div>{rows}</div>',
        unsafe_allow_html=True,
    )


def _extract_artifact_names(text: str) -> list[str]:
    """从回复文本里抽取 `outputs/<文件名>` 形式的产出物文件名。"""
    import re as _re

    names: list[str] = []
    for m in _re.finditer(r"outputs/([A-Za-z0-9_\-一-鿿][^\s`\)\>，。；]*?\.(?:md|ics|json))", text):
        name = m.group(1).strip()
        if name and name not in names:
            names.append(name)
    return names


def _render_artifacts(names: list[str], writer) -> None:
    """在结果下方渲染产出物下载按钮。"""
    if not names:
        return
    st.markdown('<div class="co-meta" style="margin-top:8px">📦 本轮产出物：</div>', unsafe_allow_html=True)
    for name in names:
        path = writer.output_dir / name
        if not path.is_file():
            continue
        st.download_button(
            label=f"📄 {name}",
            data=path.read_bytes(),
            file_name=name,
            key=f"inline_dl_{name}_{hash(name)}",
            use_container_width=False,
        )



# 聊天历史
messages = (
    store.get_messages(st.session_state.session_id)
    if st.session_state.session_id is not None
    else []
)
for m in messages:
    with st.chat_message("user" if m["role"] == "user" else "assistant"):
        if m["role"] == "assistant" and m.get("tool_calls"):
            try:
                payload = json.loads(m["tool_calls"])
            except (json.JSONDecodeError, TypeError):
                payload = {}
            events = payload.get("events", []) if isinstance(payload, dict) else payload
            plan = payload.get("plan") if isinstance(payload, dict) else None
            _render_plan(plan)
            _render_exec(events)
        st.markdown(m["content"])

# 待确认操作
if st.session_state.pending_op:
    op = st.session_state.pending_op
    st.warning(f"⚠️ {op.get('message', '该操作需要确认')}")
    c1, c2 = st.columns(2)
    if c1.button("✅ 确认执行", key="confirm_yes", type="primary",
                 use_container_width=True):
        st.session_state.pending_op = None
        st.session_state["confirmed_op"] = op["op_id"]
        st.rerun()
    if c2.button("❌ 取消", key="confirm_no", use_container_width=True):
        st.session_state.pending_op = None
        st.rerun()

# 输入框
user_input = st.chat_input("输入你的问题，例如：帮我选 3 门不撞课的课程…")
# 无条件消费 pending_input，避免残留导致"点任何东西都重复弹出演示话术"
pending_input = st.session_state.pop("pending_input", None)
if not user_input:
    user_input = pending_input


# ===========================================================================
# 处理输入
# ===========================================================================

if user_input:
    confirmed_op = st.session_state.pop("confirmed_op", None)

    # 尚未有会话时惰性创建一个（标题即首条消息的总结）
    if st.session_state.session_id is None:
        st.session_state.session_id = store.create_session(
            _summarize_title(user_input)
        )

    # 点「新建」创建的会话标题是占位的"新会话"，
    # 发出第一条消息后用内容自动命名，便于在列表里辨认。
    _cur = next(
        (x for x in store.list_sessions() if x.id == st.session_state.session_id),
        None,
    )
    if _cur and _cur.title == "新会话" and _cur.message_count == 0:
        store.rename_session(st.session_state.session_id, _summarize_title(user_input))

    store.add_message(st.session_state.session_id, "user", user_input)
    with st.chat_message("user"):
        st.markdown(user_input)

    tool_events: list[dict] = []
    exec_lines: list[str] = []
    plan_data: dict | None = None
    level_display = status.display
    confirm_event: dict | None = None
    final_text = ""

    graph = build_graph(memory_summary=memory.summary())

    try:
        for event in graph.run(user_input, history=messages, confirmed_op=confirmed_op):
            etype = event.get("type")

            if etype == "level":
                level_display = event.get("display", "")
            elif etype == "plan":
                plan_data = event.get("plan", {})
            elif etype == "tool_start":
                args = json.dumps(event.get("args", {}), ensure_ascii=False)
                exec_lines.append(
                    f"🔧 **调用工具** `{event.get('tool')}`　`{args[:120]}`"
                )
                tool_events.append(
                    {"tool": event.get("tool"), "args": event.get("args", {}),
                     "ok": True, "elapsed_ms": 0, "summary": "", "phase": "start"}
                )
            elif etype == "tool_end":
                ok = event.get("ok", True)
                icon = "✅" if ok else "❌"
                exec_lines.append(
                    f"{icon} `{event.get('tool')}` 返回（{event.get('elapsed_ms', 0)}ms）：\n\n"
                    f"> {event.get('summary', '')[:260]}"
                )
                for rec in reversed(tool_events):
                    if rec.get("phase") == "start" and rec.get("tool") == event.get("tool"):
                        rec.update({"ok": ok, "elapsed_ms": event.get("elapsed_ms", 0),
                                    "summary": event.get("summary", ""), "phase": "end"})
                        break
            elif etype == "confirm":
                confirm_event = event
                st.session_state.pending_op = event
            elif etype == "done":
                final_text = event.get("content", final_text)
                level_display = event.get("level", level_display)
            elif etype == "error":
                final_text = (
                    f"⚠️ 处理过程中出现异常：`{event.get('message', '')}`\n\n"
                    "这通常意味着后端服务未启动或网络中断。"
                    "请确认已运行 `python backend/main.py`，然后重试。"
                )
    except Exception as exc:  # noqa: BLE001
        final_text = (
            f"⚠️ 处理过程中出现异常：`{type(exc).__name__}: {exc}`\n\n"
            "这通常意味着后端服务未启动或网络中断。"
            "请确认已运行 `python backend/main.py`，然后重试。"
        )

    # 三段式渲染：规划 → 执行 → 结果
    with st.chat_message("assistant"):
        _render_plan(plan_data)
        _render_exec(tool_events)
        st.markdown(
            f'<div class="co-result">', unsafe_allow_html=True
        )
        if confirm_event:
            st.warning(confirm_event.get("message", ""))
        st.markdown(final_text)
        st.markdown(
            f'<div class="co-meta">接入层级：{level_display}'
            f' · 工具调用 {len(tool_events)} 次</div>',
            unsafe_allow_html=True,
        )
        st.markdown('</div>', unsafe_allow_html=True)
        # 若本轮生成了产出物，直接把下载按钮挂在结果下方
        from core.artifacts import ArtifactWriter

        writer = ArtifactWriter()
        all_mentions = final_text + "\n" + "\n".join(
            e.get("summary", "") for e in tool_events
        )
        _render_artifacts(_extract_artifact_names(all_mentions), writer)

    store.add_message(
        st.session_state.session_id, "assistant", final_text,
        {"events": tool_events, "plan": plan_data},
    )

    if tool_events:
        names = "、".join(dict.fromkeys(e["tool"] for e in tool_events))
        memory.append_record(
            f"- {time.strftime('%Y-%m-%d %H:%M')} 调用工具 {names}；用户诉求：{user_input[:40]}"
        )

    st.rerun()
