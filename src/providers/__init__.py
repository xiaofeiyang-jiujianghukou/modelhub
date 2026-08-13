"""供应商适配器包"""

from typing import Optional

from src.providers.openai_provider import OpenAIProvider
from src.providers.anthropic_provider import AnthropicProvider
from src.providers.gemini_provider import GeminiProvider
from src.providers.mock_provider import MockProvider


# 供应商名称到类的映射
# DeepSeek / GLM / 方舟均为 OpenAI 兼容 API，复用 OpenAIProvider
_PROVIDER_CLASSES = {
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
    "deepseek": OpenAIProvider,
    "glm": OpenAIProvider,
    "ark": OpenAIProvider,
    "mock": MockProvider,
}


def get_provider(name: str) -> Optional["BaseProvider"]:
    """
    根据供应商名称获取 Provider 实例

    Args:
        name: 供应商名称，如 "openai", "anthropic", "gemini"

    Returns:
        Provider 实例，如果未找到则返回 None
    """
    provider_class = _PROVIDER_CLASSES.get(name.lower())
    if provider_class:
        return provider_class()
    return None


__all__ = ["get_provider", "OpenAIProvider", "AnthropicProvider", "GeminiProvider"]
