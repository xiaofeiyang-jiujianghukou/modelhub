"""
测试配置和共享 fixtures
使用内存 SQLite + Mock 供应商，不依赖真实外部服务
"""

import asyncio
import hashlib
import secrets
import uuid
from typing import AsyncGenerator, Generator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.db.models import (
    ApiKey, Balance, Base, Model, Provider, User,
)
from src.middleware.auth import _generate_api_key, _hash_api_key, _key_prefix

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def test_engine():
    """创建测试数据库引擎（function 级：每次测试独立内存库，隔离性好）"""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session_factory(test_engine):
    """数据库会话工厂"""
    factory = async_sessionmaker(test_engine, expire_on_commit=False, autoflush=False)
    yield factory


@pytest.fixture(autouse=True)
def _patch_global_db(test_engine, monkeypatch):
    """
    关键：将全局数据库 session 工厂替换为测试内存库
    这样 AuthMiddleware / get_db 依赖等所有走 src.database 的代码都使用测试库
    """
    from src import database as db_module
    factory = async_sessionmaker(test_engine, expire_on_commit=False, autoflush=False)
    monkeypatch.setattr(db_module, "AsyncSessionLocal", factory)
    monkeypatch.setattr(db_module, "engine", test_engine)
    yield


@pytest.fixture(autouse=True)
def _real_encryption_key(monkeypatch):
    """全局：使用真实随机加密密钥，隔离 .env 占位值（凭证加密相关代码必需）"""
    import base64
    import os
    from src.config import settings
    monkeypatch.setattr(settings, "credentials_encryption_key", base64.b64encode(os.urandom(32)).decode())
    yield


@pytest.fixture(autouse=True)
def _disable_codex_catalog_sync(monkeypatch):
    """全局：禁用 Codex 目录后台同步——测试库数据不得污染真实 ~/.codex 目录"""
    from src.config import settings
    monkeypatch.setattr(settings, "codex_catalog_path", None)
    yield


@pytest_asyncio.fixture
async def db_session(db_session_factory) -> AsyncGenerator[AsyncSession, None]:
    """数据库会话"""
    async with db_session_factory() as session:
        yield session


# ── 种子数据 ─────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture(autouse=True)
async def _seed_model_references(db_session_factory):
    """seed model_references 表（static 供应商清单 + 官方参考价/上下文），所有测试可用"""
    from src.services import model_reference
    async with db_session_factory() as db:
        await model_reference.seed_from_json(db)
        await db.commit()
    yield


@pytest_asyncio.fixture
async def seed_user(db_session) -> User:
    """创建测试用户（带余额和 API Key）"""
    user = User(
        email="test@test.com",
        password_hash="pbkdf2:sha256:260000:salt:deadbeef",
        display_name="Tester",
    )
    db_session.add(user)
    await db_session.flush()
    db_session.add(Balance(user_id=user.id, amount_usd=100.0))

    raw_key = _generate_api_key()
    api_key = ApiKey(
        user_id=user.id,
        name="test-key",
        key_prefix=_key_prefix(raw_key),
        key_hash=_hash_api_key(raw_key),
    )
    db_session.add(api_key)
    await db_session.commit()

    # 将明文 key 附加到 user 对象上供测试使用
    user._test_raw_key = raw_key  # type: ignore
    user._test_api_key = api_key  # type: ignore
    return user


@pytest_asyncio.fixture
async def seed_model(db_session) -> Model:
    """创建测试模型 + Mock 路由通道"""
    model = Model(
        model="test-model",
        vendor="mock",
        display_name="Test Model",
        owned_by="mock",
        model_type="llm",
        input_price=2.0,
        output_price=8.0,
        context_window=8192,
        upstream_model="test-model",
        route_strategy="priority",
    )
    db_session.add(model)

    provider = Provider(
        name="mock",
        base_url="https://mock.internal",
        auth_type="bearer",
        credentials_enc="{}",
    )
    db_session.add(provider)
    await db_session.commit()
    return model


# ── 认证头 ───────────────────────────────────────────────────────────────────

@pytest.fixture
def auth_headers(seed_user) -> dict:
    """带有效 API Key 的请求头"""
    return {"Authorization": f"Bearer {seed_user._test_raw_key}"}
