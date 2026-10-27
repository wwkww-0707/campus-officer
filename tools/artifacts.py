"""产出物写入工具（1 个）。

这是把 Agent 接到"文件产出"能力上的入口，也是路径 A 第 7 项"明确产出物"的落点。
"""

from __future__ import annotations

from pathlib import Path

from langchain_core.tools import tool

from core.artifacts import ArtifactWriter, sanitize_filename, timestamp

_writer: ArtifactWriter | None = None


def _get_writer() -> ArtifactWriter:
    """获取写入器单例。"""
    global _writer
    if _writer is None:
        _writer = ArtifactWriter()
    return _writer


@tool
def write_artifact(title: str, content: str, filename: str = "", kind: str = "markdown") -> str:
    """把整理好的内容保存为产出物文件（Markdown / ICS / JSON）。

    出于安全考虑，文件只能写入 outputs/ 目录，文件名会被自动清洗以防止路径穿越。

    Args:
        title: 文件标题，用于生成默认文件名。
        content: 文件正文内容。
        filename: 自定义文件名（不含路径），留空则根据标题自动生成。
        kind: 文件类型：markdown / ics / json。
    """
    try:
        writer = _get_writer()
        if not filename:
            suffix = {"markdown": ".md", "ics": ".ics", "json": ".json"}.get(kind, ".md")
            filename = f"{sanitize_filename(title)}_{timestamp()}{suffix}"
        else:
            filename = sanitize_filename(filename)

        if kind == "json":
            import json

            path = writer.write_json(filename, json.loads(content))
        elif kind == "ics":
            path = writer.write_ics(filename, content)
        else:
            path = writer.write_text(filename, content, kind=kind)

        rel = Path(path).name
        return f"产出物已保存：outputs/{rel}（{path.stat().st_size} 字节）"
    except PermissionError as exc:
        return f"【写入被拒绝】{exc}"
    except Exception as exc:  # noqa: BLE001
        return f"【写入失败】{exc}"
