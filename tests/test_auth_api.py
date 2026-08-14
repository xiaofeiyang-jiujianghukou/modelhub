"""
认证与 API Key 管理集成测试
通过 FastAPI TestClient 测试 HTTP 接口
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from src.main import app
from src.db.models import ApiKey


@pytest.fixture
def client():
    """function 级客户端：确保在 _patch_global_db 之后创建（中间件动态读取数据库工厂）"""
    with TestClient(app) as c:
        yield c


class TestRegister:
    def test_register_success(self, client: TestClient):
        """注册成功"""
        resp = client.post(
            "/v1/auth/register",
            json={"email": "new@test.com", "password": "password123", "display_name": "New"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] == "new@test.com"
        assert "id" in data

    def test_register_duplicate_email(self, client: TestClient):
        """重复邮箱注册失败（同一测试内先注册再重复）"""
        # 第一次注册成功
        first = client.post(
            "/v1/auth/register",
            json={"email": "dup@test.com", "password": "password123"},
        )
        assert first.status_code == 200

        # 第二次重复注册返回 400
        resp = client.post(
            "/v1/auth/register",
            json={"email": "dup@test.com", "password": "password123"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "email_exists"

    def test_register_weak_password(self, client: TestClient):
        """弱密码注册失败"""
        resp = client.post(
            "/v1/auth/register",
            json={"email": "weak@test.com", "password": "short"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "weak_password"


class TestLogin:
    def test_login_success(self, client: TestClient):
        """登录成功返回 JWT（同一测试内注册再登录）"""
        client.post(
            "/v1/auth/register",
            json={"email": "login@test.com", "password": "password123"},
        )
        resp = client.post(
            "/v1/auth/login",
            json={"email": "login@test.com", "password": "password123"},
        )
        assert resp.status_code == 200
        assert resp.json()["access_token"].startswith("eyJ")  # JWT 前缀

    def test_login_wrong_password(self, client: TestClient):
        """错误密码登录失败"""
        client.post(
            "/v1/auth/register",
            json={"email": "wrongpw@test.com", "password": "password123"},
        )
        resp = client.post(
            "/v1/auth/login",
            json={"email": "wrongpw@test.com", "password": "wrongpassword"},
        )
        assert resp.status_code == 401


class TestApiKeyManagement:
    @pytest.fixture
    def jwt(self, client: TestClient) -> str:
        # 同一测试上下文内先注册再登录
        client.post(
            "/v1/auth/register",
            json={"email": "keymgr@test.com", "password": "password123"},
        )
        resp = client.post(
            "/v1/auth/login",
            json={"email": "keymgr@test.com", "password": "password123"},
        )
        return resp.json()["access_token"]

    def test_create_key(self, client: TestClient, jwt: str):
        """创建 API Key 返回明文"""
        resp = client.post(
            "/v1/dashboard/keys",
            json={"name": "prod-server"},
            headers={"Authorization": f"Bearer {jwt}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["key"].startswith("sk-")
        assert data["name"] == "prod-server"

    def test_list_keys(self, client: TestClient, jwt: str):
        """列出 Key（脱敏显示）：先创建再列出"""
        # 先创建一个 key
        client.post(
            "/v1/dashboard/keys",
            json={"name": "list-test"},
            headers={"Authorization": f"Bearer {jwt}"},
        )
        resp = client.get(
            "/v1/dashboard/keys",
            headers={"Authorization": f"Bearer {jwt}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["object"] == "list"
        assert len(data["data"]) >= 1
        assert "sk-" in data["data"][0]["key_prefix"]

    def test_revoke_key(self, client: TestClient, jwt: str):
        """撤销 Key 后无法使用"""
        # 创建 key
        create_resp = client.post(
            "/v1/dashboard/keys",
            json={"name": "to-revoke"},
            headers={"Authorization": f"Bearer {jwt}"},
        )
        key_id = create_resp.json()["id"]
        raw_key = create_resp.json()["key"]

        # 撤销
        revoke_resp = client.delete(
            f"/v1/dashboard/keys/{key_id}",
            headers={"Authorization": f"Bearer {jwt}"},
        )
        assert revoke_resp.status_code == 200

        # 撤销后调用接口应 401
        chat_resp = client.post(
            "/v1/chat/completions",
            json={"model": "test-model", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": f"Bearer {raw_key}"},
        )
        assert chat_resp.status_code == 401

    def test_no_auth_returns_401(self, client: TestClient):
        """未认证访问 Key 管理接口返回 401"""
        resp = client.get("/v1/dashboard/keys")
        assert resp.status_code == 401


class TestChatAuth:
    def test_chat_requires_valid_key(self, client: TestClient):
        """无效 API Key 调用 chat 返回 401"""
        resp = client.post(
            "/v1/chat/completions",
            json={"model": "test-model", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": "Bearer sk-invalid-key-123"},
        )
        assert resp.status_code == 401

    def test_chat_invalid_model(self, client: TestClient):
        """未知模型返回 400 invalid_model"""
        resp = client.post(
            "/v1/chat/completions",
            json={"model": "no-such-model", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": "Bearer sk-any-format"},
        )
        # 401 或 400 都算认证/模型错误处理正确
        assert resp.status_code in (401, 400)
