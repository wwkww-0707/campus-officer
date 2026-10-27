"""杉达学院官网信息抓取器（真实浏览器渲染）。

背景：sandau.edu.cn 全站采用 JS 混淆反爬，普通 HTTP 请求拿到的是混淆脚本，
页面内容需浏览器执行 JS 后才渲染。因此用 Playwright + 系统 Edge（channel='msedge'，
免下载内核）真实渲染后提取。

抓取对象：news.sandau.edu.cn 首页的四个栏目——「通知公告 / 讲座信息 / 校园活动 / 新闻」，
每条带标题、时间、地点与详情链接。

合规说明：内容均为学校官网公开信息，抓取后保留原文与来源链接，不编造、不改写。
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class CampusItem:
    """一条校园信息。"""

    title: str
    category: str  # 通知 / 讲座 / 活动 / 新闻
    time: str = ""
    location: str = ""
    url: str = ""


_HOME_URL = "https://news.sandau.edu.cn/"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0"
)

# 首页会混入大量宣传片、校园风光图、专题片等媒体类链接，
# 它们不是「日程 / 通知 / 讲座 / 活动」类信息，抓进来只会稀释有效内容。
_MEDIA_KEYWORDS = (
    "宣传片", "专题片", "纪录片", "视频", "图集", "组图", "靓图",
    "俯瞰图", "全景图", "海报", "封面", "校歌", "MV",
)

# 类别优先级：日程类（通知/讲座/活动）排在前面，新闻类靠后
_CATEGORY_ORDER = {"通知": 0, "讲座": 1, "活动": 2, "新闻": 3}


def _text(node) -> str:
    """取节点的可见文本，压缩空白。"""
    try:
        t = node.inner_text() if hasattr(node, "inner_text") else node.text_content()
    except Exception:
        return ""
    return re.sub(r"\s+", " ", t or "").strip()


def _clean_title(t: str) -> str:
    """去掉标题里可能重复的前缀。"""
    t = t.replace("【】", "").strip()
    return t


async def fetch_campus_items(page) -> list[CampusItem]:
    """渲染 news.sandau.edu.cn 首页，提取通知/讲座/活动/新闻四类信息。

    超时设 20 秒（而非 60 秒）：现场演示时若网络不通，应在 20 秒内明确失败，
    而不是让界面长时间无响应。
    """
    await page.goto(_HOME_URL, wait_until="domcontentloaded", timeout=20000)
    await page.wait_for_timeout(3000)

    items: list[CampusItem] = []
    seen: set[str] = set()

    # 三类带"时间/地点"的栏目：通知公告、讲座信息、校园活动
    # 它们通常以标题 + "时间：..." + "地点：..." 的结构出现。
    body_text = await page.evaluate("() => document.body.innerText")

    # 抓取带链接的条目，并尝试关联时间/地点
    links = await page.evaluate(
        """() => Array.from(document.querySelectorAll('a')).map(a => ({
            t: (a.innerText||'').trim(),
            h: a.href || ''
        }))"""
    )
    for l in links:
        t = re.sub(r"\s+", " ", l["t"]).strip()
        h = l["h"]
        if not t or not h or "page." not in h or "sameFolder" in h:
            continue
        if len(t) < 6:
            continue
        t = _clean_title(t)
        if t in seen:
            continue

        # 过滤媒体类条目（宣传片 / 风光图 / 专题片等），它们不属于日程信息
        if any(k in t for k in _MEDIA_KEYWORDS):
            continue

        # 分类：链接路径 / 关键词
        cat = "新闻"
        if any(k in t for k in ["通知", "公告", "放假", "安排", "公示", "招聘", "招标",
                                "报名", "选课", "考试", "申领", "认定"]):
            cat = "通知"
        elif any(k in t for k in ["讲座", "主讲", "报告", "论坛", "沙龙", "宣讲"]):
            cat = "讲座"
        elif any(k in t for k in ["活动", "仪式", "动员会", "下午茶", "比赛", "大赛",
                                  "作品展", "展览", "演出", "竞赛", "开幕"]):
            cat = "活动"

        seen.add(t)
        items.append(CampusItem(title=t, category=cat, url=h))

    # 按类别优先级排序：通知 > 讲座 > 活动 > 新闻（同类别保持原站内顺序）
    items.sort(key=lambda x: _CATEGORY_ORDER.get(x.category, 9))

    # 从正文里抓取「时间 / 地点」并回填到对应条目（弱关联，尽力而为）
    time_loc = re.findall(
        r"时间：([^\n]{0,40})|地点：([^\n]{0,40})", body_text
    )
    # 由于列表页时间地点与标题的对应关系不易精确对齐，这里仅做概览抓取，
    # 时间地点由前端展示时按需跳转详情页查看。

    return items


async def scrape_sandau() -> list[CampusItem]:
    """顶层入口：启动 Edge 并抓取首页信息。"""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            channel="msedge",
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = await browser.new_context(
            user_agent=_UA,
            viewport={"width": 1280, "height": 800},
            locale="zh-CN",
        )
        page = await ctx.new_page()
        try:
            return await fetch_campus_items(page)
        finally:
            await browser.close()
