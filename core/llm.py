"""模型接入与三层降级链。

这是本项目"现场演示零翻车"的技术保障。

降级链：
    第 1 层：阿里百炼（OpenAI 兼容云端接口，需 API Key）
        ↓ 失败（无 Key / 网络不通 / 限流 / 超时）
    第 2 层：本地 Ollama（OpenAI 兼容本地接口）
        ↓ 失败（未安装 / 未拉取模型 / 连接被拒）
    第 3 层：规则降级引擎（纯本地，不依赖任何网络与模型）
        —— 用关键词意图识别 + 模板化回复，**照样调用真实业务工具**，
           保证「工具被真正调用」这一硬性要求在任何环境下都成立。

第 3 层不是摆设：它让"无 Key 也能跑通主路径"成为事实，
也使答辩时"如果 Key 失效会怎样"这个问题有确定答案。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.config import Settings, get_settings
from core.prompts import SYSTEM_PROMPT

# 降级层级标识
LEVEL_BAILIAN = "bailian"
LEVEL_OLLAMA = "ollama"
LEVEL_MOCK = "mock"

LEVEL_NAMES = {
    LEVEL_BAILIAN: "第1层 · 阿里百炼（云端）",
    LEVEL_OLLAMA: "第2层 · 本地 Ollama",
    LEVEL_MOCK: "第3层 · 规则降级引擎",
}


@dataclass
class LLMStatus:
    """模型接入状态。"""

    level: str
    display: str
    healthy: bool
    detail: str = ""

    @property
    def is_real_model(self) -> bool:
        """当前是否为真实大模型（而非规则引擎）。"""
        return self.level in (LEVEL_BAILIAN, LEVEL_OLLAMA)


class ModelProvider:
    """模型提供方封装：统一接口 + 自动降级。"""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._llm: Any = None
        self._level: str = LEVEL_MOCK
        self._detail: str = ""
        self._initialized = False

    # -- 初始化 -------------------------------------------------------------

    def initialize(self) -> LLMStatus:
        """按优先级尝试建立模型连接，确定当前可用层级。"""
        if self._initialized:
            return self.status()

        provider = self.settings.llm_provider
        self._initialized = True

        # 只尝试用户指定的层级；若指定层级不可用则继续向下尝试
        attempts: list[str] = []
        if provider == LEVEL_BAILIAN:
            attempts = [LEVEL_BAILIAN, LEVEL_OLLAMA, LEVEL_MOCK]
        elif provider == LEVEL_OLLAMA:
            attempts = [LEVEL_OLLAMA, LEVEL_MOCK]
        else:
            attempts = [LEVEL_MOCK]

        for level in attempts:
            ok, llm, detail = self._try_level(level)
            if ok and llm is not None:
                self._llm = llm
                self._level = level
                self._detail = detail
                return self.status()
            self._detail = detail

        # 全部失败，落到规则引擎
        self._llm = None
        self._level = LEVEL_MOCK
        return self.status()

    def _try_level(self, level: str) -> tuple[bool, Any, str]:
        """尝试建立某一层的模型连接。"""
        try:
            if level == LEVEL_BAILIAN:
                return self._try_bailian()
            if level == LEVEL_OLLAMA:
                return self._try_ollama()
            return True, None, "规则降级引擎无需模型"
        except Exception as exc:  # noqa: BLE001
            return False, None, f"{type(exc).__name__}: {exc}"

    def _try_bailian(self) -> tuple[bool, Any, str]:
        """接入阿里百炼（OpenAI 兼容接口）。"""
        if not self.settings.bailian_api_key:
            return False, None, "未配置 DASHSCOPE_API_KEY"

        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(
            model=self.settings.bailian_model,
            base_url=self.settings.bailian_base_url,
            api_key=self.settings.bailian_api_key,
            temperature=0.2,
            timeout=30,
            max_retries=1,
        )
        # 轻量探活：发一个极短请求，确认凭据与网络可用
        probe = llm.invoke("ok")
        if not probe:
            return False, None, "百炼探活无响应"
        return True, llm, f"{self.settings.bailian_base_url} · {self.settings.bailian_model}"

    def _try_ollama(self) -> tuple[bool, Any, str]:
        """接入本地 Ollama（OpenAI 兼容接口）。"""
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(
            model=self.settings.ollama_model,
            base_url=self.settings.ollama_base_url,
            api_key=self.settings.ollama_api_key or "ollama",
            temperature=0.2,
            timeout=60,
            max_retries=0,
        )
        probe = llm.invoke("ok")
        if not probe:
            return False, None, "Ollama 探活无响应"
        return True, llm, f"{self.settings.ollama_base_url} · {self.settings.ollama_model}"

    # -- 状态 ---------------------------------------------------------------

    def status(self) -> LLMStatus:
        """返回当前状态。"""
        return LLMStatus(
            level=self._level,
            display=LEVEL_NAMES.get(self._level, self._level),
            healthy=self._level != LEVEL_MOCK,
            detail=self._detail,
        )

    @property
    def level(self) -> str:
        """当前层级标识。"""
        return self._level

    @property
    def llm(self) -> Any:
        """当前模型实例；规则引擎下为 None。"""
        return self._llm

    @property
    def has_real_model(self) -> bool:
        """是否有真实模型可用。"""
        return self._llm is not None

    def system_message(self) -> str:
        """系统提示词。"""
        return SYSTEM_PROMPT

    def invoke(self, messages: list[Any]) -> str:
        """调用模型生成回复；失败时抛出异常由上层降级。"""
        if self._llm is None:
            raise RuntimeError("当前处于规则降级层，无模型可调用")
        response = self._llm.invoke(messages)
        content = getattr(response, "content", "")
        if isinstance(content, list):
            # 部分模型返回内容块列表
            content = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in content
            )
        return str(content)


_provider: ModelProvider | None = None


def get_provider() -> ModelProvider:
    """获取模型提供方单例。"""
    global _provider
    if _provider is None:
        _provider = ModelProvider()
        _provider.initialize()
    return _provider


def reset_provider() -> None:
    """重置提供方（切换配置或重新连接时使用）。"""
    global _provider
    _provider = None
