"""
build_provider 工厂测试：修复非 mock 供应商无参实例化 TypeError（health.py 曾永远 degraded）
"""

import httpx
import pytest

from src.db.models import Provider
from src.providers import build_provider, get_provider
from src.providers.openai_provider import OpenAIProvider
from src.providers.anthropic_provider import AnthropicProvider
from src.providers.gemini_provider import GeminiProvider
from src.providers.mock_provider import MockProvider


def _mock_anthropic_http(monkeypatch, status_code, body=None):
    """让 AnthropicProvider 的 HTTP 请求走 MockTransport（不再打真实上游）"""
    real_async_client = httpx.AsyncClient  # 先保存真实类，避免被替换后递归
    transport = httpx.MockTransport(lambda req: httpx.Response(status_code, json=body or {}))

    def _factory(*a, **k):
        k.pop("proxy", None)  # 测试隔离：不打真实上游代理
        return real_async_client(transport=transport, *a, **k)

    monkeypatch.setattr("src.providers.anthropic_provider.httpx.AsyncClient", _factory)


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
async def test_anthropic_health_check_rejects_no_key(monkeypatch):
    """Anthropic 未配置 key → health_check 直接 False，且不发任何网络请求（不再默认 True 假阳性）"""
    calls = []
    real_async_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda req: calls.append(req) or httpx.Response(401))

    def _factory(*a, **k):
        k.pop("proxy", None)  # 测试隔离：不打真实上游代理
        return real_async_client(transport=transport, *a, **k)

    monkeypatch.setattr("src.providers.anthropic_provider.httpx.AsyncClient", _factory)
    adapter = AnthropicProvider("https://api.anthropic.com", api_key="")
    assert await adapter.health_check() is False
    assert calls == []  # 无 key 直接拦截，不跑网络


@pytest.mark.asyncio
async def test_openai_health_check_rejects_no_key(monkeypatch):
    """OpenAI 未配置 key → health_check 直接 False，不发网络请求（不用等 60s 超时）"""
    calls = []
    real_async_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda req: calls.append(req) or httpx.Response(401))

    def _factory(*a, **k):
        k.pop("proxy", None)  # 测试隔离：不打真实上游代理
        return real_async_client(transport=transport, *a, **k)

    monkeypatch.setattr("src.providers.openai_provider.httpx.AsyncClient", _factory)
    adapter = OpenAIProvider("https://api.openai.com/v1", api_key="")
    assert await adapter.health_check() is False
    assert calls == []


@pytest.mark.asyncio
async def test_anthropic_health_check_ok_with_key(monkeypatch):
    """有 key 且上游 2xx → health_check True"""
    _mock_anthropic_http(monkeypatch, 200, {"data": []})
    adapter = AnthropicProvider("https://api.anthropic.com", api_key="sk-valid")
    assert await adapter.health_check() is True


def test_anthropic_endpoint_v1_compat():
    """base_url 带/不带 /v1 时 _endpoint 不重复拼接（界面可配成 …/v1）"""
    assert AnthropicProvider("https://api.anthropic.com", "")._endpoint("/messages") == "https://api.anthropic.com/v1/messages"
    assert AnthropicProvider("https://api.anthropic.com/v1", "")._endpoint("/messages") == "https://api.anthropic.com/v1/messages"
    assert AnthropicProvider("https://api.anthropic.com/v1/", "")._endpoint("/models") == "https://api.anthropic.com/v1/models"


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
