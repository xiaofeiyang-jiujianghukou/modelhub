"""
API 兼容性集成测试
使用 httpx AsyncClient + ASGITransport，验证与 OpenAI API 规范的兼容性
"""

import pytest
import httpx

from src.main import app
from src.models import Balance


async def _setup_user(test_engine):
    """在测试库中创建用户 + Key + 模型"""
    from src.models import ApiKey, Balance, ModelCatalog, Provider, RouteChannel, User
    from src.middleware.auth import _generate_api_key, _hash_api_key, _key_prefix

    factory = __import__("sqlalchemy.ext.asyncio", fromlist=["async_sessionmaker"]).async_sessionmaker(
        test_engine, expire_on_commit=False, autoflush=False
    )
    async with factory() as db:
        user = User(email="compat@test.com", password_hash="x", display_name="Compat")
        db.add(user)
        await db.flush()
        db.add(Balance(user_id=user.id, amount_usd=50.0))

        raw = _generate_api_key()
        db.add(ApiKey(
            user_id=user.id, name="compat", key_prefix=_key_prefix(raw), key_hash=_hash_api_key(raw),
        ))

        model = ModelCatalog(id="compat-model", model_type="llm", input_price=1.0, output_price=3.0)
        db.add(model)
        provider = Provider(name="mock", base_url="https://mock", auth_type="bearer", credentials_enc="{}")
        db.add(provider)
        await db.flush()
        db.add(RouteChannel(model_id=model.id, provider_id=provider.id, upstream_model=model.id, weight=100, priority=100))
        await db.commit()
        return raw


@pytest.mark.asyncio
async def test_chat_basic_request(test_engine, _patch_global_db):
    """基本请求返回 OpenAI 兼容结构"""
    raw_key = await _setup_user(test_engine)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/v1/chat/completions",
            json={
                "model": "compat-model",
                "messages": [{"role": "user", "content": "Hello"}],
                "max_tokens": 50,
            },
            headers={"Authorization": f"Bearer {raw_key}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        # OpenAI 规范字段
        assert data["object"] == "chat.completion"
        assert data["choices"][0]["message"]["role"] == "assistant"
        assert data["choices"][0]["finish_reason"] == "stop"
        assert "usage" in data
        assert "prompt_tokens" in data["usage"]
        assert data["id"].startswith("chatcmpl-")


@pytest.mark.asyncio
async def test_chat_missing_model(test_engine, _patch_global_db):
    """缺少 model 返回 422"""
    raw_key = await _setup_user(test_engine)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "Hi"}]},
            headers={"Authorization": f"Bearer {raw_key}"},
        )
        assert resp.status_code == 422


@pytest.mark.asyncio
async def test_chat_missing_messages(test_engine, _patch_global_db):
    """缺少 messages 返回 422"""
    raw_key = await _setup_user(test_engine)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/v1/chat/completions",
            json={"model": "compat-model"},
            headers={"Authorization": f"Bearer {raw_key}"},
        )
        assert resp.status_code == 422


@pytest.mark.asyncio
async def test_chat_invalid_model(test_engine, _patch_global_db):
    """未知模型返回 400 invalid_model"""
    raw_key = await _setup_user(test_engine)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/v1/chat/completions",
            json={"model": "no-such-model", "messages": [{"role": "user", "content": "Hi"}]},
            headers={"Authorization": f"Bearer {raw_key}"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "invalid_model"


@pytest.mark.asyncio
async def test_models_requires_auth(test_engine, _patch_global_db):
    """未认证返回 401"""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/v1/models")
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_health_no_auth(test_engine, _patch_global_db):
    """健康检查无需认证"""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"


@pytest.mark.asyncio
async def test_root(test_engine, _patch_global_db):
    """根路径跳转到 Web 控制台"""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/")
        assert resp.status_code == 307
        assert resp.headers["location"] == "/dashboard"


@pytest.mark.asyncio
async def test_balance_endpoint(test_engine, _patch_global_db):
    """余额查询返回网关标准格式（JWT 认证）"""
    raw_key = await _setup_user(test_engine)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        # 注册并登录获取 JWT
        await ac.post(
            "/v1/auth/register",
            json={"email": "bal@test.com", "password": "password123"},
        )
        login_resp = await ac.post(
            "/v1/auth/login",
            json={"email": "bal@test.com", "password": "password123"},
        )
        jwt = login_resp.json()["access_token"]

        resp = await ac.get(
            "/v1/dashboard/balance",
            headers={"Authorization": f"Bearer {jwt}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "balance_usd" in data
        assert data["currency"] == "USD"
