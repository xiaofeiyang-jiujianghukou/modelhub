"""
模枢 ModelHub - 数据库初始化与种子数据脚本

用法:
    python scripts/init_db.py           # 初始化表结构 + 缺省种子（ensure-exists，不覆盖 UI 修改）
    python scripts/init_db.py --reset   # 清空重建所有表 + 种子数据

说明:
- 供应商/模型数据单一数据源：src/providers/provider_registry.py（static 供应商清单）
- 语义为 ensure-exists：已存在的供应商/模型一律跳过，绝不覆盖界面/迁移写入的配置
- 首次运行自动生成 CREDENTIALS_ENCRYPTION_KEY（写入 .env，不入库）
"""

import asyncio
import os
import sys
from pathlib import Path

# 确保项目根目录在 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

from src.database import init_db  # noqa: E402
from src.db.models import Base, Provider, Model, User, Balance  # noqa: E402
from sqlalchemy import select, func  # noqa: E402

from src.providers.provider_registry import PROVIDER_REGISTRY  # noqa: E402
from src.services.crypto import encrypt_credentials  # noqa: E402
from src.services import model_reference  # noqa: E402
from scripts.generate_encryption_key import ensure_encryption_key  # noqa: E402
from src.config import settings  # noqa: E402
from src.middleware.auth import _hash_password  # noqa: E402

# .env 环境变量 → 供应商 key 映射（static 供应商种子时读取）
ENV_KEY_MAP = {
    "ARK_API_KEY": "ark",
    "DEEPSEEK_API_KEY": "deepseek",
    "GLM_API_KEY": "glm",
}


async def seed(reset: bool = False) -> None:
    """初始化表结构并写入缺省种子数据（ensure-exists）"""
    # 运行时获取（scripts 为全局单例，测试环境动态替换 db 模块属性）
    from src import database as db_module
    engine, AsyncSessionLocal = db_module.engine, db_module.AsyncSessionLocal

    if reset:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        print("🔄 已清空所有表")
    await init_db()
    print("✅ 表结构就绪")

    # 步骤 0: 加密密钥自愈（占位值时生成真实密钥写入 .env）
    ensure_encryption_key()

    async with AsyncSessionLocal() as db:
        # 步骤 1: 默认创建初始管理员（全新库无任何用户时）
        user_count = await db.scalar(select(func.count()).select_from(User))
        if user_count == 0:
            admin = User(
                email="admin@modelhub.com",
                password_hash=_hash_password("modelhub"),
                display_name="admin",
                is_admin=True,
            )
            db.add(admin)
            await db.flush()
            db.add(Balance(user_id=admin.id, amount_usd=settings.signup_bonus_usd))
            print("  ➕ 初始管理员: admin@modelhub.com / modelhub")

        # 步骤 2: 模型参考价/上下文种子（JSON → model_references 表，幂等）
        ref_count = await model_reference.seed_from_json(db)
        if ref_count:
            print(f"  🌱 参考数据种子导入：新增 {ref_count} 条")

        created_providers = 0
        created_models = 0
        for key, spec in PROVIDER_REGISTRY.items():
            if spec.model_source != "static":
                continue

            # ── 供应商 ensure-exists ──
            result = await db.execute(select(Provider).where(Provider.name == spec.key))
            existing = result.scalar_one_or_none()
            if existing:
                print(f"  ↻ {spec.key}: 已存在，跳过（不覆盖配置）")
                continue

            env_key = ENV_KEY_MAP.get(spec.key)
            api_key = os.getenv(env_key, "") if env_key else ""
            if not api_key or len(api_key) < 8 or "xxx" in api_key.lower():
                api_key = ""
            provider = Provider(
                name=spec.key,
                base_url=spec.default_base_url,
                auth_type=spec.auth_type,
                credentials_enc=encrypt_credentials({"api_key": api_key}),
                timeout_ms=60000,
                is_active=True,
            )
            db.add(provider)
            await db.flush()
            created_providers += 1
            key_status = "✅" if api_key else "⚠️ 无 Key"
            print(f"  ➕ {spec.key}: 已创建 ({key_status})")

            # ── 模型 + 路由 ensure-exists（清单来自 model_references 表）──
            for ref in await model_reference.static_models_for(db, spec.key):
                model = await db.get(Model, (ref.model_id, spec.key))
                if model:
                    continue
                model = Model(
                    model=ref.model_id,
                    vendor=spec.key,
                    display_name=ref.display_name,
                    owned_by=spec.key,
                    model_type="llm",
                    input_price=float(ref.input_price) if ref.input_price is not None else None,
                    output_price=float(ref.output_price) if ref.output_price is not None else None,
                    price_currency=ref.price_currency,
                    context_window=ref.context_window,
                    price_source=ref.price_source,
                    upstream_model=ref.upstream_model or ref.model_id,
                    synced_from=spec.key,
                    route_strategy="priority",
                    is_active=True,
                )
                db.add(model)
                created_models += 1
                print(f"    ➕ 模型: {ref.model_id} ({spec.key})")

        await db.commit()
        print(f"\n🎉 完成：新增 {created_providers} 个供应商，{created_models} 个模型（已存在项均跳过）")


if __name__ == "__main__":
    reset = "--reset" in sys.argv
    asyncio.run(seed(reset=reset))
