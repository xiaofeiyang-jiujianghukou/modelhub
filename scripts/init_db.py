"""
模枢 ModelHub - 数据库初始化与种子数据脚本

用法:
    python scripts/init_db.py           # 初始化表结构 + 种子数据（从 .env 读 Key）
    python scripts/init_db.py --reset   # 清空重建所有表 + 种子数据

说明:
- API Key 从项目根目录 .env 读取（.env 不入库）
- 幂等：已存在的供应商/模型会更新 Key 而非重复创建
"""

import asyncio
import json
import os
import sys
from pathlib import Path

# 确保项目根目录在 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

from src.database import init_db, engine, AsyncSessionLocal  # noqa: E402
from src.models import Base, Provider, ModelCatalog, RouteChannel  # noqa: E402
from sqlalchemy import select  # noqa: E402


# ── 供应商定义 ───────────────────────────────────────────────────────────────

PROVIDERS = [
    {
        "name": "ark",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "auth_type": "bearer",
        "env_key": "ARK_API_KEY",
        "timeout_ms": 60000,
    },
    {
        "name": "ark-plan",
        "base_url": "https://ark.cn-beijing.volces.com/api/coding/v1",
        "auth_type": "bearer",
        "env_key": "ARK_API_KEY",
        "timeout_ms": 120000,
    },
    {
        "name": "deepseek",
        "base_url": "https://api.deepseek.com/v1",
        "auth_type": "bearer",
        "env_key": "DEEPSEEK_API_KEY",
        "timeout_ms": 60000,
    },
    {
        "name": "glm",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "auth_type": "bearer",
        "env_key": "GLM_API_KEY",
        "timeout_ms": 60000,
    },
]

# ── 模型定义：(模型ID, 显示名, 供应商, 上游模型名, 输入价/1M, 输出价/1M, 上下文) ──

MODELS = [
    # 方舟 Coding Plan 通道（消耗套餐额度）
    ("ark-doubao-seed-2-1-turbo", "Doubao Seed 2.1 Turbo (方舟)", "ark-plan", "doubao-seed-2-1-turbo-260628", 0.4, 1.6, 256000),
    ("ark-doubao-seed-2-0-lite", "Doubao Seed 2.0 Lite (方舟)", "ark-plan", "doubao-seed-2-0-lite-260428", 0.4, 1.0, 65536),
    ("ark-glm-5-2", "GLM-5.2 (方舟)", "ark-plan", "glm-5-2-260617", 2.0, 8.0, 131072),
    ("ark-deepseek-v4-pro", "DeepSeek V4 Pro (方舟)", "ark-plan", "deepseek-v4-pro-260425", 2.0, 8.0, 65536),
    ("ark-deepseek-v4-flash", "DeepSeek V4 Flash (方舟)", "ark-plan", "deepseek-v4-flash-260425", 1.0, 4.0, 65536),
    ("ark-minimax-m3", "MiniMax M3 (方舟)", "ark-plan", "minimax-m3", 2.0, 8.0, 131072),
    ("ark-kimi-k2-7-code", "Kimi K2.7 Code (方舟)", "ark-plan", "kimi-k2-7-code", 2.0, 8.0, 131072),
    ("ark-code-latest", "ARK Auto (方舟智能路由)", "ark-plan", "ark-code-latest", 2.0, 8.0, 131072),
    # 方舟普通推理通道（旗舰按量计费，Coding Plan 不支持）
    ("ark-doubao-seed-2-1-pro", "Doubao Seed 2.1 Pro (方舟)", "ark", "doubao-seed-2-1-pro-260628", 0.8, 3.0, 256000),
    ("ark-doubao-seed-evolving", "Doubao Seed Evolving (方舟)", "ark", "doubao-seed-evolving", 1.0, 3.0, 256000),
    # DeepSeek 官方
    ("ds-deepseek-v4-flash", "DeepSeek V4 Flash (官方)", "deepseek", "deepseek-v4-flash", 1.0, 4.0, 65536),
    ("ds-deepseek-v4-pro", "DeepSeek V4 Pro (官方)", "deepseek", "deepseek-v4-pro", 2.0, 8.0, 65536),
    # GLM 智谱
    ("glm-4-flash", "GLM-4 Flash (智谱)", "glm", "glm-4-flash", 0.1, 0.1, 131072),
    ("glm-5-2", "GLM-5.2 (智谱)", "glm", "glm-5.2", 5.0, 20.0, 131072),
    ("glm-5-1", "GLM-5.1 (智谱)", "glm", "glm-5.1", 5.0, 20.0, 131072),
]


async def seed(reset: bool = False) -> None:
    """初始化表结构并写入种子数据"""
    if reset:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        print("🔄 已清空所有表")
    await init_db()
    print("✅ 表结构就绪")

    async with AsyncSessionLocal() as db:
        # ── 供应商 ──
        provider_ids: dict[str, str] = {}
        for p in PROVIDERS:
            result = await db.execute(select(Provider).where(Provider.name == p["name"]))
            existing = result.scalar_one_or_none()
            api_key = os.getenv(p["env_key"], "")
            creds = json.dumps({"api_key": api_key})
            if existing:
                existing.base_url = p["base_url"]
                existing.credentials_enc = creds
                existing.timeout_ms = p["timeout_ms"]
                existing.is_active = True
                provider_ids[p["name"]] = existing.id
                key_status = "✅" if api_key else "⚠️ 无 Key"
                print(f"  ↻ {p['name']}: 已更新 ({key_status})")
            else:
                provider = Provider(
                    name=p["name"], base_url=p["base_url"], auth_type=p["auth_type"],
                    credentials_enc=creds, timeout_ms=p["timeout_ms"], is_active=True,
                )
                db.add(provider)
                await db.flush()
                provider_ids[p["name"]] = provider.id
                key_status = "✅" if api_key else "⚠️ 无 Key"
                print(f"  ➕ {p['name']}: 已创建 ({key_status})")

        # ── 模型 + 路由 ──
        for mid, display, pname, upstream, in_price, out_price, ctx in MODELS:
            model = await db.get(ModelCatalog, mid)
            if not model:
                model = ModelCatalog(
                    id=mid, display_name=display, owned_by="ark" if pname.startswith("ark") else pname,
                    model_type="llm", input_price=in_price, output_price=out_price,
                    context_window=ctx, route_strategy="priority", is_active=True,
                )
                db.add(model)
                await db.flush()
                print(f"  ➕ 模型: {mid}")

            result = await db.execute(select(RouteChannel).where(RouteChannel.model_id == mid))
            channel = result.scalar_one_or_none()
            if channel:
                channel.provider_id = provider_ids[pname]
                channel.upstream_model = upstream
                channel.is_active = True
            else:
                db.add(RouteChannel(
                    model_id=mid, provider_id=provider_ids[pname],
                    upstream_model=upstream, weight=100, priority=100, is_active=True,
                ))

        await db.commit()
        print(f"\n🎉 完成：{len(PROVIDERS)} 个供应商，{len(MODELS)} 个模型")


if __name__ == "__main__":
    reset = "--reset" in sys.argv
    asyncio.run(seed(reset=reset))
