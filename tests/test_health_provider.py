"""
build_provider 工厂测试：修复非 mock 供应商无参实例化 TypeError（health.py 曾永远 degraded）
"""

import pytest

from src.db.models import Provider
from src.providers import build_provider, get_provider
from src.providers.openai_provider import OpenAIProvider
from src.providers.anthropic_provider import AnthropicProvider
from src.providers.gemini_provider import GeminiProvider
from src.providers.mock_provider import MockProvider


@pytest.mark.asyncio
async def test_build_provider_non_mock_no_typeerror(db_session):
    """非 mock provider（legacy 空凭证 '{}'）能正常构建实例，不再 TypeError"""
    for name, expected_cls in [
        ("openai", OpenAIProvider),
        ("anthropic", AnthropicProvider),
        ("gemini", GeminiProvider),
        ("deepseek", OpenAIProvider),
        ("grok", OpenAIProvider),
        ("mock", MockProvider),
    ]:
        p = Provider(
            name=name,
            base_url="https://example.test",
            auth_type="bearer",
            credentials_enc="{}",
            timeout_ms=30000,
        )
        db_session.add(p)
        await db_session.flush()

        adapter = build_provider(p)
        assert isinstance(adapter, expected_cls), f"{name} → {expected_cls.__name__}"
        # 凭证为空时 api_key 为空字符串，不抛异常
        assert adapter.api_key == ""

        await db_session.rollback()


@pytest.mark.asyncio
async def test_build_provider_decrypts_credentials(db_session, monkeypatch):
    """构建时从 credentials_enc 解密出 api_key（加密格式）"""
    from src.services.crypto import encrypt_credentials

    p = Provider(
        name="openai",
        base_url="https://api.openai.com/v1",
        auth_type="bearer",
        credentials_enc=encrypt_credentials({"api_key": "sk-secret-key"}),
        timeout_ms=30000,
    )
    db_session.add(p)
    await db_session.flush()

    adapter = build_provider(p)
    assert adapter.api_key == "sk-secret-key"
    assert adapter.base_url == "https://api.openai.com/v1"
    assert adapter.timeout_seconds == 30.0


@pytest.mark.asyncio
async def test_health_check_marks_degraded_not_crash(db_session, monkeypatch):
    """health_check 失败 → 通道标记 degraded，不抛未捕获异常（原 bug：非 mock 直接 TypeError 被吞成 degraded）"""
    from src.providers.openai_provider import OpenAIProvider

    p = Provider(
        name="openai",
        base_url="https://api.openai.com/v1",
        auth_type="bearer",
        credentials_enc='{"api_key":"sk-x"}',
        timeout_ms=30000,
    )
    db_session.add(p)
    await db_session.flush()

    # 构造成功（原 bug 在这里就会 TypeError）
    adapter = build_provider(p)
    assert isinstance(adapter, OpenAIProvider)

    # health_check 失败 → 返回 False 而非抛异常
    async def fake_health(self):
        return False
    monkeypatch.setattr(OpenAIProvider, "health_check", fake_health)
    assert await adapter.health_check() is False


@pytest.mark.asyncio
async def test_get_provider_new_signature(db_session):
    """get_provider 兼容新签名：无 base_url 时用注册表默认值，不再无参实例化"""
    from src.providers.provider_registry import get_spec

    spec = get_spec("openai")
    adapter = get_provider("openai")
    assert adapter is not None
    assert adapter.base_url == spec.default_base_url
    assert adapter.api_key == ""

    adapter2 = get_provider("not-in-registry")
    assert adapter2 is None
