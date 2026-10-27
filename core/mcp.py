"""MCP 工具桥接：把 `mcp_server` 的异步 stdio 工具包装成同步可调用的 LangChain 工具。

背景与动机
----------
`mcp_server/server.py` 用官方 MCP Python SDK 实现了 3 个工具，但它们通过 stdio 暴露，
在客户端一侧是**纯异步**的（`StructuredTool` 不支持同步 `.invoke()`）。而本项目智能体
编排层（LangGraph 节点）是同步的，因此此前 MCP 工具**从未被真正挂载进智能体**——
这是与"LangGraph 声明不实"同类的"宣称有但没接线"问题，本模块补齐它。

实现要点
--------
1. **后台事件循环线程**：在守护线程里跑一个 `asyncio` 事件循环，持有**常驻 session**，
   主线程用 `run_coroutine_threadsafe(...).result()` 同步投递调用；
2. **常驻连接**：一次连接、多次调用，把每次新建 client 的进程启动 + jieba 加载开销
   （约 1.4s）摊薄到第 2 次起的约 0.002s；
3. **惰性 + 容错**：首次调用时才真正拉起 MCP 子进程；连接失败时返回可读的
   「不可用」提示，不影响本地工具与规则降级引擎照常工作。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import sys
import threading
import time
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 连接与调用超时（秒）。首次连接需启动子进程并加载 jieba，故放宽。
_CONNECT_TIMEOUT = 25.0
_CALL_TIMEOUT = 20.0


def _extract_text(result: Any) -> str:
    """从 MCP 工具调用结果中提取文本（content 为 `[{type:'text', text:...}]` 列表）。"""
    content = getattr(result, "content", [])
    parts: list[str] = []
    for item in content or []:
        text = getattr(item, "text", None)
        if text is not None:
            parts.append(str(text))
    return "\n".join(parts)


class _McpBridge:
    """常驻 MCP stdio 会话桥。

    在守护线程里跑一个常驻事件循环，由 `_serve()` 协程持有 stdio 会话并
    **在同一个 task 内**完成 enter/exit；主线程通过请求队列同步投递调用。
    """

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._queue: asyncio.Queue[tuple[str, dict[str, Any], concurrent.futures.Future]] = (
            asyncio.Queue()
        )
        self._ready = False
        self._failure = ""
        self._thread = threading.Thread(target=self._run, name="mcp-bridge", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        """后台线程入口：在同一事件循环里完成连接与常驻服务。"""
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._serve())

    async def _serve(self) -> None:
        """常驻服务：建立连接（enter）后循环处理队列里的调用。

        把 `stdio_client` / `ClientSession` 的 enter 与 exit 都放在本协程内，
        避免 anyio 的 cancel scope 跨 task 退出报错。
        """
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=sys.executable,
            args=["mcp_server/server.py"],
            cwd=str(_PROJECT_ROOT),
        )
        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self._ready = True
                    while True:
                        name, args, fut = await self._queue.get()
                        try:
                            result = await session.call_tool(name, args)
                            fut.set_result(result)
                        except Exception as exc:  # noqa: BLE001
                            fut.set_result(None)
        except Exception as exc:  # noqa: BLE001
            self._failure = f"{type(exc).__name__}: {exc}"
            self._ready = False

    def call_tool(self, name: str, args: dict[str, Any]) -> Any | None:
        """同步调用 MCP 工具；不可用/超时返回 None。"""
        deadline = time.monotonic() + _CONNECT_TIMEOUT
        while not self._ready and not self._failure and time.monotonic() < deadline:
            time.sleep(0.01)
        if not self._ready:
            return None
        loop = self._loop
        fut: concurrent.futures.Future = concurrent.futures.Future()
        try:
            asyncio.run_coroutine_threadsafe(self._queue.put((name, args, fut)), loop)
            return fut.result(timeout=_CALL_TIMEOUT)
        except Exception:  # noqa: BLE001
            return None


_bridge: _McpBridge | None = None


def _get_bridge() -> _McpBridge:
    """获取桥接单例（惰性：首次调用时才拉起 MCP 子进程）。"""
    global _bridge
    if _bridge is None:
        _bridge = _McpBridge()
    return _bridge


def _mcp_call(name: str, args: dict[str, Any]) -> str:
    """发起一次 MCP 工具调用，失败时返回可读提示。"""
    result = _get_bridge().call_tool(name, args)
    if result is None:
        return (
            "【工具失败】MCP 知识库服务暂不可用（子进程未启动或连接失败）。"
            "请以学校官方最新文件为准，或直接查询教务处/学生工作处正式通知。"
        )
    return _extract_text(result)


# ---------------------------------------------------------------------------
# 同步包装工具（挂载进智能体的 2 个 MCP 新能力工具）
#
# 注意：`search_policy` 与本地同名工具职责互补（本地版供规则引擎快速 BM25，
# MCP 版供外部客户端标准协议复用），为避免重名冲突，这里只挂载两个**本地没有的**
# 新能力工具：`get_deadlines` 与 `list_policy_docs`。
# ---------------------------------------------------------------------------


@tool
def get_deadlines(keyword: str = "") -> str:
    """【MCP】查询各类校务事项的截止时间（通过标准 MCP 协议访问校规知识库）。

    当用户询问"什么时候截止""报名期限""材料提交时间"等时间节点时调用。

    Args:
        keyword: 事项关键词（如"助学金""奖学金""选课"），留空返回全部。
    """
    return _mcp_call("get_deadlines", {"keyword": keyword})


@tool
def list_policy_docs(category: str = "") -> str:
    """【MCP】列出校规知识库中的制度文件目录（通过标准 MCP 协议访问）。

    当用户询问"有哪些政策文件""制度目录""关于奖助学金的文件有哪些"时调用。

    Args:
        category: 类别过滤（奖助学金/选课/其他），留空返回全部。
    """
    return _mcp_call("list_policy_docs", {"category": category})


def get_mcp_tools() -> list[Any]:
    """返回挂载进智能体的 MCP 工具（同步包装后的 LangChain 工具）。"""
    return [get_deadlines, list_policy_docs]
