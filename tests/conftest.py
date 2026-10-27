"""pytest 全局配置：把测试产生的数据隔离到临时目录。

**为什么需要这个文件**
----------------------
`core.memory.SessionStore` 与 `LongTermMemory` 默认写入项目内的
`data/sessions.db` 和 `AGENTS.md`。如果测试直接跑，每跑一次
pytest 就会往生产库里塞十几条对话记录、往 AGENTS.md 追加记录——
之前"应用里对话记录特别多"很大一部分就是这么来的。

本文件在**任何测试模块导入之前**把三个路径重定向到临时目录：
- `SESSION_DB`       → 临时 session 库
- `OUTPUT_DIR`       → 临时产出物目录
- `AGENTS_MEMORY`    → 临时 AGENTS.md

`knowledge_dir` 保持不变——测试需要检索真实的知识库条款。

注意：`core.config.get_settings()` 带 `lru_cache`，因此必须在
它被首次调用之前设置环境变量。`conftest.py` 在测试收集阶段最先执行，
满足这个时序要求。
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from pathlib import Path

# 一次性临时根目录（进程退出时清理）
_TMP_ROOT = Path(tempfile.mkdtemp(prefix="campus_officer_test_"))
os.environ["SESSION_DB"] = str(_TMP_ROOT / "sessions.db")
os.environ["OUTPUT_DIR"] = str(_TMP_ROOT / "outputs")
os.environ["AGENTS_MEMORY"] = str(_TMP_ROOT / "AGENTS.md")
# 测试统一走规则降级层，避免测试依赖真实模型与网络
os.environ.setdefault("LLM_PROVIDER", "mock")

(_TMP_ROOT / "outputs").mkdir(parents=True, exist_ok=True)


@atexit.register
def _cleanup() -> None:
    """进程退出时删除临时目录。"""
    shutil.rmtree(_TMP_ROOT, ignore_errors=True)
