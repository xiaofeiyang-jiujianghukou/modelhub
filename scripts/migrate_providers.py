"""
将 .env 中的供应商 API Key 迁移进数据库（统一管理后 .env 只保留非敏感配置）

用法:
    python scripts/migrate_providers.py              # 迁移（占位值跳过；已有 key 跳过）
    python scripts/migrate_providers.py --overwrite  # 强制覆盖已存在的 key
    python scripts/migrate_providers.py --reencrypt  # 全表 legacy 明文凭证重加密为 gcm:v1:

规则:
- .env 中占位值（空 / 长度 <8 / 含 xxx）→ 跳过并记录
- provider 存在但无 key → 补齐加密凭证
- provider 存在且有 key → 跳过（除非 --overwrite）
- 迁移后若库中无任何管理员，自动将最早注册用户提升为 admin
"""

import asyncio
import os
import sys
from pathlib import Path

# 确保项目根目录在 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

from sqlalchemy import func, select  # noqa: E402

from src.models import Provider, User  # noqa: E402
from src.services.crypto import decrypt_credentials, encrypt_credentials  # noqa: E402

# .env 变量名 → 数据库供应商名
ENV_KEY_MAP = {
    "ARK_API_KEY": "ark-plan",
    "DEEPSEEK_API_KEY": "deepseek",
    "GLM_API_KEY": "glm",
}


def _is_placeholder(key: str) -> bool:
    return not key or len(key) < 8 or "xxx" in key.lower()


async def migrate(overwrite: bool = False) -> dict:
    """执行 .env → DB 迁移，返回统计"""
    # 运行时获取（scripts 为全局单例，测试环境动态替换 db 模块属性）
    from src import database as db_module
    AsyncSessionLocal = db_module.AsyncSessionLocal

    stats = {"created": [], "updated": [], "skipped_placeholder": [], "skipped_existing": []}
    async with AsyncSessionLocal() as db:
        for env_name, provider_name in ENV_KEY_MAP.items():
            # env 变量名大小写不敏感（.env 中可能 ARK_API_KEY 或 ark_api_key）
            api_key = ""
            for k, v in os.environ.items():
                if k.upper() == env_name:
                    api_key = v
                    break

            if _is_placeholder(api_key):
                stats["skipped_placeholder"].append(f"{provider_name} (env {env_name} 为占位值)")
                continue

            provider = await db.scalar(select(Provider).where(Provider.name == provider_name))
            if not provider:
                from src.providers.provider_registry import get_spec
                spec = get_spec(provider_name)
                if not spec:
                    stats["skipped_placeholder"].append(f"{provider_name} (不在注册表)")
                    continue
                provider = Provider(
                    name=spec.key,
                    base_url=spec.default_base_url,
                    auth_type=spec.auth_type,
                    credentials_enc=encrypt_credentials({"api_key": api_key}),
                    timeout_ms=60000,
                    is_active=True,
                )
                db.add(provider)
                stats["created"].append(provider_name)
                print(f"  ➕ {provider_name}: 创建并写入 Key")
                continue

            has_key = bool(decrypt_credentials(provider.credentials_enc).get("api_key"))
            if has_key and not overwrite:
                stats["skipped_existing"].append(provider_name)
                print(f"  ↻ {provider_name}: 已有 Key，跳过（--overwrite 可覆盖）")
                continue

            provider.credentials_enc = encrypt_credentials({"api_key": api_key})
            stats["updated"].append(provider_name)
            print(f"  🔄 {provider_name}: Key 已迁移（{'覆盖' if has_key else '补齐'}）")

        await db.commit()

        # 管理员兜底：库中无任何 admin 时提升最早注册用户
        admin_count = await db.scalar(select(func.count()).select_from(User).where(User.is_admin == True))  # noqa: E712
        if not admin_count:
            first_user = await db.scalar(select(User).order_by(User.created_at).limit(1))
            if first_user:
                first_user.is_admin = True
                await db.commit()
                print(f"\n👑 已将最早注册用户 {first_user.email} 提升为管理员")
            else:
                print("\n⚠️ 库中无用户（首个注册用户将自动成为管理员）")
    return stats


async def reencrypt_all() -> int:
    """全表 legacy 明文凭证重加密为 gcm:v1: 格式"""
    from src import database as db_module
    AsyncSessionLocal = db_module.AsyncSessionLocal

    count = 0
    async with AsyncSessionLocal() as db:
        providers = (await db.execute(select(Provider))).scalars().all()
        for p in providers:
            raw = p.credentials_enc
            if raw.startswith("gcm:v1:"):
                continue
            creds = decrypt_credentials(raw)   # legacy 明文解析
            p.credentials_enc = encrypt_credentials(creds)
            count += 1
        await db.commit()
    return count


if __name__ == "__main__":
    overwrite = "--overwrite" in sys.argv
    reencrypt = "--reencrypt" in sys.argv

    if reencrypt:
        n = asyncio.run(reencrypt_all())
        print(f"✅ 已重加密 {n} 条 legacy 明文凭证")
    else:
        print("开始迁移 .env → DB：")
        stats = asyncio.run(migrate(overwrite=overwrite))
        print(f"\n🎉 迁移完成：新增 {len(stats['created'])}，补齐 {len(stats['updated'])}，"
              f"占位跳过 {len(stats['skipped_placeholder'])}，已有跳过 {len(stats['skipped_existing'])}")
