"""
数据库迁移与 .env Key 迁移测试
"""

import pytest
import pytest_asyncio
from sqlalchemy import select

from src.models import ModelCatalog, Provider, RouteChannel, User
from src.services.crypto import decrypt_credentials, encrypt_credentials


@pytest.mark.asyncio
class TestDbMigration:
    async def test_run_migrations_idempotent(self, test_engine):
        """guarded ALTER 迁移：列已存在时幂等返回空"""
        from scripts.migrate_db import run_db_migrations, needs_migration

        assert await needs_migration(engine=test_engine) is False
        applied = await run_db_migrations(engine=test_engine)
        assert applied == []


@pytest.mark.asyncio
class TestInitDbNoOverwrite:
    async def test_seed_does_not_overwrite_existing(self, db_session, monkeypatch):
        """init_db 重跑不覆盖已存在的供应商配置（UI 修改的 Key 保留）"""
        # 屏蔽真实 .env 写入
        monkeypatch.setattr("scripts.generate_encryption_key.ensure_encryption_key", lambda: "test-key")

        from scripts.init_db import seed
        await seed(reset=False)

        # 种子创建了 static 供应商（glm 等）
        glm = await db_session.scalar(select(Provider).where(Provider.name == "glm"))
        assert glm is not None
        original_enc = glm.credentials_enc

        # 模拟 UI 修改 Key（加密写入新 key）
        ui_enc = encrypt_credentials({"api_key": "sk-ui-updated-999"})
        glm.credentials_enc = ui_enc
        await db_session.commit()

        # 重跑 seed → 不应覆盖
        await seed(reset=False)
        db_session.expire_all()
        refreshed = await db_session.scalar(select(Provider).where(Provider.name == "glm"))
        assert refreshed.credentials_enc == ui_enc
        assert decrypt_credentials(refreshed.credentials_enc)["api_key"] == "sk-ui-updated-999"

        # 模型也被种子建出
        m = await db_session.get(ModelCatalog, "glm-4-flash")
        assert m is not None
        assert m.price_source == "official"


@pytest.mark.asyncio
class TestMigrateProviders:
    async def test_placeholder_env_skipped(self, db_session, monkeypatch):
        """.env 占位值跳过迁移"""
        monkeypatch.setenv("ARK_API_KEY", "sk-xxx-placeholder")
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-xxx-placeholder")
        monkeypatch.setenv("GLM_API_KEY", "sk-xxx-placeholder")
        monkeypatch.setattr("scripts.generate_encryption_key.ensure_encryption_key", lambda: "test-key")

        from scripts.migrate_providers import migrate
        stats = await migrate()

        assert stats["created"] == []
        assert stats["updated"] == []
        assert len(stats["skipped_placeholder"]) == 3

    async def test_migrate_env_key_to_db(self, db_session, monkeypatch):
        """.env 真实 Key 迁移：不存在则创建，存在无 Key 则补齐"""
        monkeypatch.setenv("ARK_API_KEY", "ark-real-key-12345678")
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-real-deepseek-12345678")
        monkeypatch.setenv("GLM_API_KEY", "sk-real-glm-12345678")

        from scripts.migrate_providers import migrate
        stats = await migrate()

        assert set(stats["created"]) == {"ark-plan", "deepseek", "glm"}
        for name in ["ark-plan", "deepseek", "glm"]:
            p = await db_session.scalar(select(Provider).where(Provider.name == name))
            assert p is not None
            assert p.credentials_enc.startswith("gcm:v1:")
            assert len(decrypt_credentials(p.credentials_enc)["api_key"]) > 10

        # 已有 Key → 跳过
        stats2 = await migrate()
        assert stats2["skipped_existing"] == ["ark-plan", "deepseek", "glm"]

        # --overwrite 覆盖
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-new-deepseek-99999999")
        stats3 = await migrate(overwrite=True)
        assert "deepseek" in stats3["updated"]
        p = await db_session.scalar(select(Provider).where(Provider.name == "deepseek"))
        assert decrypt_credentials(p.credentials_enc)["api_key"] == "sk-new-deepseek-99999999"

    async def test_reencrypt_legacy(self, db_session):
        """legacy 明文凭证重加密为 gcm:v1: 格式"""
        from src.models import Provider as P

        legacy = P(name="openai", base_url="https://api.openai.com/v1",
                   auth_type="bearer", credentials_enc='{"api_key":"sk-legacy-plain-123"}')
        db_session.add(legacy)
        await db_session.commit()

        from scripts.migrate_providers import reencrypt_all
        n = await reencrypt_all()
        assert n >= 1
        db_session.expire_all()
        p = await db_session.scalar(select(P).where(P.name == "openai"))
        assert p.credentials_enc.startswith("gcm:v1:")
        assert "sk-legacy-plain-123" not in p.credentials_enc
        assert decrypt_credentials(p.credentials_enc)["api_key"] == "sk-legacy-plain-123"

    async def test_first_user_promoted_to_admin(self, db_session, monkeypatch):
        """库中无 admin 时迁移自动提升最早注册用户"""
        user = User(email="oldest@test.com", password_hash="x", display_name="Oldest")
        db_session.add(user)
        await db_session.commit()

        monkeypatch.setenv("ARK_API_KEY", "sk-xxx-placeholder")
        from scripts.migrate_providers import migrate
        await migrate()

        uid = user.id
        db_session.expire_all()
        refreshed = await db_session.scalar(select(User).where(User.id == uid))
        assert refreshed.is_admin is True
