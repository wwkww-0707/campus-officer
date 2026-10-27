"""全局配置：环境变量读取与路径管理。

设计要点：
1. 所有敏感信息（API Key）只从环境变量 / .env 读取，永不写入代码或仓库；
2. 路径统一以项目根目录为锚点，保证在任意工作目录下启动都能正确解析；
3. 配置对象为不可变数据类，避免运行中被意外改写。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

# 项目根目录 = core/config.py 所在目录的上一级
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 启动时加载 .env（若存在）。load_dotenv 不会覆盖已存在的系统环境变量。
load_dotenv(PROJECT_ROOT / ".env")


def _env_bool(key: str, default: bool = False) -> bool:
    """读取布尔型环境变量，接受 1/true/yes/on 等常见写法。"""
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(key: str, default: int) -> int:
    """读取整型环境变量，非法值回退到默认值。"""
    raw = os.getenv(key)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """运行期配置快照。"""

    # --- 模型服务 ---
    llm_provider: str = "mock"
    bailian_api_key: str = ""
    bailian_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    bailian_model: str = "qwen-plus"
    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_model: str = "qwen2.5:7b"
    ollama_api_key: str = ""

    # --- 后端 ---
    campus_api_base_url: str = "http://127.0.0.1:8811"

    # --- 路径 ---
    project_root: Path = field(default=PROJECT_ROOT)
    knowledge_dir: Path = field(default=PROJECT_ROOT / "knowledge" / "policies")
    output_dir: Path = field(default=PROJECT_ROOT / "outputs")
    session_db: Path = field(default=PROJECT_ROOT / "data" / "sessions.db")
    skills_dir: Path = field(default=PROJECT_ROOT / "skills")
    agents_memory_file: Path = field(default=PROJECT_ROOT / "AGENTS.md")

    # --- 运行参数 ---
    app_port: int = 8501
    demo_mode: bool = True
    max_tool_rounds: int = 8

    # --- 派生属性 ---
    @property
    def has_real_model(self) -> bool:
        """当前配置是否具备调用真实模型的条件。"""
        if self.llm_provider == "bailian":
            return bool(self.bailian_api_key.strip())
        if self.llm_provider == "ollama":
            return True
        return False

    @property
    def provider_display_name(self) -> str:
        """给界面用的可读名称。"""
        return {
            "bailian": f"阿里百炼 · {self.bailian_model}",
            "ollama": f"本地 Ollama · {self.ollama_model}",
            "mock": "规则降级引擎（无需 Key）",
        }.get(self.llm_provider, self.llm_provider)

    def ensure_dirs(self) -> None:
        """确保运行所需目录存在。"""
        for path in (self.output_dir, self.session_db.parent, self.knowledge_dir):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """读取并缓存全局配置（单例）。"""
    settings = Settings(
        llm_provider=os.getenv("LLM_PROVIDER", "mock").strip().lower() or "mock",
        bailian_api_key=os.getenv("DASHSCOPE_API_KEY", "").strip(),
        bailian_base_url=os.getenv(
            "DASHSCOPE_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ).strip(),
        bailian_model=os.getenv("DASHSCOPE_MODEL", "qwen-plus").strip(),
        ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1").strip(),
        ollama_model=os.getenv("OLLAMA_MODEL", "qwen2.5:7b").strip(),
        ollama_api_key=os.getenv("OLLAMA_API_KEY", "").strip(),
        campus_api_base_url=os.getenv(
            "CAMPUS_API_BASE_URL", "http://127.0.0.1:8811"
        ).strip(),
        knowledge_dir=PROJECT_ROOT / os.getenv("KNOWLEDGE_DIR", "knowledge/policies"),
        output_dir=PROJECT_ROOT / os.getenv("OUTPUT_DIR", "outputs"),
        session_db=PROJECT_ROOT / os.getenv("SESSION_DB", "data/sessions.db"),
        # 长期记忆文件路径：测试时指向临时文件，避免污染生产 AGENTS.md
        agents_memory_file=PROJECT_ROOT / os.getenv("AGENTS_MEMORY", "AGENTS.md"),
        skills_dir=PROJECT_ROOT / "skills",
        app_port=_env_int("APP_PORT", 8501),
        demo_mode=_env_bool("DEMO_MODE", True),
        max_tool_rounds=_env_int("MAX_TOOL_ROUNDS", 8),
    )
    settings.ensure_dirs()
    return settings
