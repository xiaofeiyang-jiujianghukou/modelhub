"""供应商适配器包"""

from typing import Optional

from src.providers.openai_provider import OpenAIProvider
from src.providers.anthropic_provider import AnthropicProvider
from src.providers.gemini_provider import GeminiProvider
from src.providers.mock_provider import MockProvider


# 供应商名称到适配器类的映射（与 provider_registry 的 adapter 字段对应）
_ADAPTER_CLASSES = {
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
    "mock": MockProvider,
}


def get_provider(name: str, base_url: str = "", api_key: str = "", timeout_seconds: float = 30.0) -> Optional["BaseProvider"]:
    """
    按供应商名称获取 Provider 实例（兼容旧签名，不再无参实例化导致 TypeError）

    Args:
        name: 供应商名称，如 "openai", "anthropic", "gemini"
        base_url/api_key/timeout_seconds: 缺省时按注册表默认值构造

    Returns:
        Provider 实例，如果未找到则返回 None
    """
    from src.providers.provider_registry import get_spec
    spec = get_spec(name)
    if not spec:
        return None
    base_url = base_url or spec.default_base_url
    cls = _ADAPTER_CLASSES.get(spec.adapter, OpenAIProvider)
    return cls(base_url=base_url, api_key=api_key, timeout_seconds=timeout_seconds)


def build_provider(provider) -> Optional["BaseProvider"]:
    """
    根据 Provider ORM 对象构建适配器实例（路由引擎/健康检查/连通测试共用）
    凭证从 credentials_enc 解密（AES-256-GCM，兼容 legacy 明文）
    """
    from src.providers.provider_registry import get_spec
    from src.services.crypto import decrypt_credentials
    spec = get_spec(provider.name)
    adapter = spec.adapter if spec else ("mock" if provider.name.lower() == "mock" else "openai")
    creds = decrypt_credentials(provider.credentials_enc)
    api_key = creds.get("api_key", "")
    timeout = provider.timeout_ms / 1000.0
    cls = _ADAPTER_CLASSES.get(adapter, OpenAIProvider)
    return cls(base_url=provider.base_url, api_key=api_key, timeout_seconds=timeout)


__all__ = ["get_provider", "build_provider", "OpenAIProvider", "AnthropicProvider", "GeminiProvider"]
