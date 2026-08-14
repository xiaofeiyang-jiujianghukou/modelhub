"""
供应商管理 API 集成测试（httpx AsyncClient + ASGITransport）
- 首用户自动 admin（空库注册第一个用户）
- 非 admin 403
- CRUD + 凭证加密不泄露 + 级联删除
"""

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

from src.main import app
from src.db.models import Model, Provider
from src.services import model_sync


@pytest_asyncio.fixture
async def client():
    """async 客户端（ASGITransport 直接驱动 app，不跑 lifespan——表由 test_engine 建好）"""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _register(client: httpx.AsyncClient, email: str) -> dict:
    await client.post("/v1/auth/register", json={"email": email, "password": "password123", "display_name": email.split("@")[0]})
    resp = await client.post("/v1/auth/login", json={"email": email, "password": "password123"})
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
class TestAdminPermission:
    async def test_first_user_is_admin(self, client):
        """空库首用户注册后自动成为 admin，可访问供应商端点"""
        headers = await _register(client, "admin1@test.com")
        resp = await client.get("/v1/admin/providers", headers=headers)
        assert resp.status_code == 200

        me = await client.get("/v1/dashboard/me", headers=headers)
        assert me.json()["is_admin"] is True

    async def test_second_user_forbidden(self, client):
        """第二个注册的用户不是 admin → 403"""
        await _register(client, "admin1@test.com")
        headers2 = await _register(client, "user2@test.com")
        resp = await client.get("/v1/admin/providers", headers=headers2)
        assert resp.status_code == 403

        me = await client.get("/v1/dashboard/me", headers=headers2)
        assert me.json()["is_admin"] is False


@pytest.mark.asyncio
class TestRegistry:
    async def test_registry_entries(self, client):
        headers = await _register(client, "admin@test.com")
        resp = await client.get("/v1/admin/providers/registry", headers=headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        keys = {d["key"] for d in data}
        # 11 家
        assert len(data) == 11
        for k in ["deepseek", "ark", "hunyuan", "bailian", "moonshot", "glm",
                  "minimax", "openai", "anthropic", "grok", "gemini"]:
            assert k in keys

    async def test_unknown_provider_rejected(self, client):
        headers = await _register(client, "admin@test.com")
        resp = await client.post("/v1/admin/providers", headers=headers, json={
            "name": "not-exist", "credentials": {"api_key": "sk-x"},
        })
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "unknown_provider"


@pytest.mark.asyncio
class TestProviderCrud:
    async def test_create_static_provider_and_sync(self, client, db_session):
        """创建 static 供应商（glm）→ 前台同步模型入库 → 凭证加密不泄露"""
        headers = await _register(client, "admin@test.com")
        resp = await client.post("/v1/admin/providers", headers=headers, json={
            "name": "glm", "credentials": {"api_key": "sk-glm-secret-123"},
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["sync"]["status"] == "done"
        assert data["sync"]["result"]["added"] == 3

        provider = data["id"]
        # 凭证加密存储（gcm:v1: 前缀），列表接口不返回明文
        p = await db_session.get(Provider, provider)
        assert p.credentials_enc.startswith("gcm:v1:")
        assert "sk-glm-secret-123" not in p.credentials_enc

        listing = (await client.get("/v1/admin/providers", headers=headers)).json()
        item = next(i for i in listing["data"] if i["id"] == provider)
        assert item["has_key"] is True
        assert item["model_count"] == 3
        assert "credentials" not in item

        # 模型入库
        m = (await db_session.execute(select(Model).where(Model.model == "glm-4-flash").limit(1))).scalars().first()
        assert m is not None
        assert m.price_source == "official"

    async def test_create_without_key_has_key_false(self, client, db_session):
        headers = await _register(client, "admin@test.com")
        resp = await client.post("/v1/admin/providers", headers=headers, json={
            "name": "glm", "credentials": {"api_key": ""},
        })
        assert resp.status_code == 200
        pid = resp.json()["id"]
        # 创建响应不含 has_key（结构为 id/name/base_url/sync），从列表确认
        listing = (await client.get("/v1/admin/providers", headers=headers)).json()
        item = next(i for i in listing["data"] if i["id"] == pid)
        assert item["has_key"] is False

    async def test_duplicate_provider_conflict(self, client):
        headers = await _register(client, "admin@test.com")
        payload = {"name": "glm", "credentials": {"api_key": "sk-x"}}
        assert (await client.post("/v1/admin/providers", headers=headers, json=payload)).status_code == 200
        assert (await client.post("/v1/admin/providers", headers=headers, json=payload)).status_code == 409

    async def test_update_credentials_and_resync(self, client, db_session):
        headers = await _register(client, "admin@test.com")
        pid = (await client.post("/v1/admin/providers", headers=headers,
                                 json={"name": "glm", "credentials": {"api_key": "sk-old"}})).json()["id"]
        # 更新 Key
        resp = await client.put(f"/v1/admin/providers/{pid}", headers=headers,
                                json={"credentials": {"api_key": "sk-new-456"}})
        assert resp.status_code == 200
        assert resp.json()["sync"]["status"] == "done"
        p = await db_session.get(Provider, pid)
        assert p.credentials_enc.startswith("gcm:v1:")
        assert "sk-new-456" not in p.credentials_enc
        # 未提供 credentials 时保留原密文
        resp2 = await client.put(f"/v1/admin/providers/{pid}", headers=headers, json={"timeout_ms": 60000})
        assert resp2.status_code == 200
        assert resp2.json()["sync"]["status"] is None
        assert (await db_session.get(Provider, pid)).credentials_enc == p.credentials_enc

    async def test_delete_cascades_orphan_models(self, client, db_session):
        """删除供应商 → 级联删其独占模型与通道；共有模型保留"""
        headers = await _register(client, "admin@test.com")
        pid = (await client.post("/v1/admin/providers", headers=headers,
                                 json={"name": "glm", "credentials": {"api_key": "sk-x"}})).json()["id"]
        assert (await db_session.execute(select(Model).where(Model.model == "glm-4-flash").limit(1))).scalars().first() is not None

        # 造一个共有模型：glm-5.2 额外挂到 mock provider（模拟多供应商共享）
        mock_provider = await db_session.scalar(select(Provider).where(Provider.name == "mock"))
        if not mock_provider:
            mock_provider = Provider(name="mock", base_url="https://mock.internal",
                                     auth_type="bearer", credentials_enc="{}")
            db_session.add(mock_provider)
            await db_session.flush()
        db_session.add(Model(model="glm-5.2", vendor="mock", model_type="llm", upstream_model="glm-5.2", weight=50, priority=50))
        await db_session.commit()

        resp = await client.delete(f"/v1/admin/providers/{pid}", headers=headers)
        assert resp.status_code == 200
        deleted = resp.json()["deleted"]

        # 删除供应商 = 删 vendor=glm 的全部 3 个模型记录；mock 的 glm-5.2（vendor=mock）保留
        assert (await db_session.execute(select(Model).where(Model.model == "glm-4-flash").limit(1))).scalars().first() is None
        assert (await db_session.execute(select(Model).where(Model.model == "glm-5.3").limit(1))).scalars().first() is None
        assert (await db_session.execute(select(Model).where(Model.model == "glm-5.2", Model.vendor == "mock").limit(1))).scalars().first() is not None
        assert await db_session.get(Provider, pid) is None
        assert deleted["models"] == 3

        # glm 厂商的模型记录也清理了
        chans = (await db_session.execute(select(Model).where(Model.vendor == "glm"))).scalars().all()
        assert len(chans) == 0

    async def test_delete_nonexistent_404(self, client):
        headers = await _register(client, "admin@test.com")
        resp = await client.delete("/v1/admin/providers/nope", headers=headers)
        assert resp.status_code == 404


@pytest.mark.asyncio
class TestProviderSyncAndTest:
    async def test_sync_endpoint_api_provider(self, client, monkeypatch):
        """api 供应商手动 sync 端点（mock 上游返回）"""
        async def fake_get(url, headers, timeout, params=None):
            return {"object": "list", "data": [
                {"id": "deepseek-v4-flash", "owned_by": "deepseek"},
                {"id": "deepseek-v4-pro", "owned_by": "deepseek"},
            ]}
        monkeypatch.setattr(model_sync, "_http_get", fake_get)

        headers = await _register(client, "admin@test.com")
        pid = (await client.post("/v1/admin/providers", headers=headers,
                                 json={"name": "deepseek", "credentials": {"api_key": "sk-ds"}, "auto_sync": False})).json()["id"]
        resp = await client.post(f"/v1/admin/providers/{pid}/sync", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        assert body["added"] == 2

    async def test_test_endpoint(self, client, monkeypatch):
        headers = await _register(client, "admin@test.com")
        pid = (await client.post("/v1/admin/providers", headers=headers,
                                 json={"name": "glm", "credentials": {"api_key": "sk-x"}})).json()["id"]
        from src.providers.openai_provider import OpenAIProvider

        async def fake_health(self):
            return True
        monkeypatch.setattr(OpenAIProvider, "health_check", fake_health)
        resp = await client.post(f"/v1/admin/providers/{pid}/test", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
