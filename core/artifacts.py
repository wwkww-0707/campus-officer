"""产出物写入器（受权限隔离约束）。

安全设计——**Agent 只能写 outputs/ 目录**：
1. 所有文件名经 `sanitize_filename` 清洗，禁止路径分隔符与 `..`；
2. 写入前用 `Path.resolve()` 校验最终路径确实位于 OUTPUT_DIR 之内（防路径穿越）；
3. 每次写入登记到事务日志，供审计。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from core.config import get_settings

# 非法文件名字符：路径分隔符与 Windows 保留字符
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# Windows 保留设备名
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *{f"COM{i}" for i in range(1, 10)},
    *{f"LPT{i}" for i in range(1, 10)},
}


def sanitize_filename(name: str, default: str = "output") -> str:
    """清洗文件名，杜绝路径穿越。"""
    name = _ILLEGAL.sub("_", name).strip().strip(".")
    name = name.replace("..", "_")
    if not name:
        return default
    stem = name.split(".")[0]
    if stem.upper() in _RESERVED:
        name = f"_{name}"
    return name[:120]


class ArtifactWriter:
    """产出物写入器。"""

    def __init__(self, output_dir: Path | None = None) -> None:
        settings = get_settings()
        self.output_dir = (output_dir or settings.output_dir).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_safe(self, filename: str) -> Path:
        """解析并校验目标路径必须位于输出目录内。

        采用「显式拒绝 + 路径校验」双重防护：
        1. 文件名中出现路径分隔符、盘符或 `..` 时，**直接拒绝**而不是静默改写——
           静默改写会让调用方误以为写的是原文件，而 Agent 越权应当被明确拦下；
        2. 解析后的绝对路径必须仍位于输出目录之内（防符号链接绕过）。
        """
        raw = filename.strip()
        # 显式拒绝越权意图
        if ".." in raw or "/" in raw or "\\" in raw or ":" in raw:
            raise PermissionError(
                f"拒绝写入 {raw!r}：文件名包含路径分隔符或上级目录引用，"
                f"Agent 仅允许写入 {self.output_dir} 目录内的纯文件名"
            )

        safe_name = sanitize_filename(raw)
        target = (self.output_dir / safe_name).resolve()

        # 第二道校验：解析后必须仍在输出目录内
        if not target.is_relative_to(self.output_dir):
            raise PermissionError(
                f"拒绝写入 {target}：Agent 仅允许写入 {self.output_dir} 目录内"
            )
        return target

    def write_text(self, filename: str, content: str, kind: str = "markdown") -> Path:
        """写入文本文件，返回路径。"""
        target = self._resolve_safe(filename)
        target.write_text(content, encoding="utf-8")
        return target

    def write_json(self, filename: str, payload: Any) -> Path:
        """写入 JSON 文件。"""
        target = self._resolve_safe(filename)
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        return target

    def write_ics(self, filename: str, ics_content: str) -> Path:
        """写入日历文件。"""
        target = self._resolve_safe(filename)
        target.write_text(ics_content, encoding="utf-8")
        return target

    def list_artifacts(self) -> list[dict[str, Any]]:
        """列出已生成的产出物。"""
        items: list[dict[str, Any]] = []
        for path in sorted(self.output_dir.iterdir(), reverse=True):
            if not path.is_file() or path.name.startswith("."):
                continue
            stat = path.stat()
            items.append(
                {
                    "filename": path.name,
                    "size": stat.st_size,
                    "created_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                    "kind": path.suffix.lstrip("."),
                }
            )
        return items


def timestamp() -> str:
    """生成文件名用时间戳。"""
    return datetime.now().strftime("%Y%m%d_%H%M%S")
