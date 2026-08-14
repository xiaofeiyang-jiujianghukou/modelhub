"""
供应商管理 API（管理员）

- 供应商 CRUD + 凭证加密存储（AES-256-GCM）
- 添加 Key 自动拉取模型（api 供应商后台同步 / static 供应商前台同步）
- 删除供应商级联清理其独占模型与路由通道
- 列表接口永不返回凭证明文，仅暴露 has_key 布尔
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db
from src.middleware.auth import get_admin_user
from src.models import ModelAlias, ModelCatalog, Provider, RouteChannel
from src.providers.provider_registry import get_spec, list_registry_entries
from src.services.crypto import decrypt_credentials, encrypt_credentials
from src.services.model_sync import SyncResult, run_sync_background, sync_provider_models

router = APIRouter(tags=["Admin"])


# ── 请求模型 ─────────────────────────────────────────────────────────────────

class CredentialsIn(BaseModel):
    api_key: str = ""
    api_secret: Optional[str] = None    # ak_sk 扩展位


class ProviderCreate(BaseModel):
    name: str
    base_url: Optional[str] = None      # 缺省取注册表默认值
    auth_type: Optional[str] = None
    credentials: CredentialsIn
    timeout_ms: int = 30000
    auto_sync: bool = True


class ProviderUpdate(BaseModel):
    base_url: Optional[str] = None
    auth_type: Optional[str] = None
    credentials: Optional[CredentialsIn] = None    # 留空=不改
    timeout_ms: Optional[int] = None
    is_active: Optional[bool] = None
    resync: bool = False                           # 改 Key 后是否重新拉取模型


def _has_key(provider: Provider) -> bool:
    return bool(decrypt_credentials(provider.credentials_enc).get("api_key"))


async def _model_count(db: AsyncSession, provider_id: str) -> int:
    return await db.scalar(
        select(func.count()).select_from(RouteChannel).where(RouteChannel.provider_id == provider_id)
    ) or 0


def _to_item(provider: Provider, model_count: int) -> dict:
    return {
        "id": provider.id,
        "name": provider.name,
        "base_url": provider.base_url,
        "auth_type": provider.auth_type,
        "has_key": _has_key(provider),
        "timeout_ms": provider.timeout_ms,
        "is_active": provider.is_active,
        "model_count": model_count,
        "last_synced_at": int(provider.last_synced_at.timestamp()) if provider.last_synced_at else None,
        "last_sync_status": provider.last_sync_status,
        "last_sync_error": provider.last_sync_error,
        "created_at": int(provider.created_at.timestamp()),
    }


# ── 端点 ─────────────────────────────────────────────────────────────────────

@router.get("/admin/providers/registry")
async def get_registry(_admin: dict = Depends(get_admin_user)):
    """供应商注册表元数据（前端"添加供应商"下拉）"""
    return {"object": "list", "data": list_registry_entries()}


@router.get("/admin/providers")
async def list_providers(
    admin: dict = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """供应商列表（凭证永不返回）"""
    rows = (await db.execute(select(Provider).order_by(Provider.created_at))).scalars().all()
    items = []
    for p in rows:
        items.append(_to_item(p, await _model_count(db, p.id)))
    return {"object": "list", "data": items}


@router.post("/admin/providers")
async def create_provider(
    req: ProviderCreate,
    admin: dict = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """创建供应商（加密存 Key，按需同步模型）"""
    spec = get_spec(req.name)
    if not spec:
        raise HTTPException(status_code=400, detail={"error": {"message": f"Unknown provider: {req.name}", "type": "invalid_request_error", "code": "unknown_provider"}})
    existing = await db.scalar(select(Provider).where(Provider.name == spec.key))
    if existing:
        raise HTTPException(status_code=409, detail={"error": {"message": f"Provider '{spec.key}' already exists", "type": "conflict", "code": "provider_exists"}})

    provider = Provider(
        name=spec.key,
        base_url=req.base_url or spec.default_base_url,
        auth_type=req.auth_type or spec.auth_type,
        credentials_enc=encrypt_credentials({"api_key": req.credentials.api_key}),
        timeout_ms=req.timeout_ms,
        is_active=True,
    )
    db.add(provider)
    await db.commit()
    await db.refresh(provider)
    logger.info(f"admin created provider {spec.key} (admin={admin.get('email')})")

    # 同步模型：static 前台立即返回；api 后台任务（避免阻塞网络拉取）
    sync_result: Optional[SyncResult] = None
    if req.auto_sync:
        if spec.model_source == "static":
            sync_result = await sync_provider_models(db, provider, spec)
        else:
            run_sync_background(provider.id)

    return {
        "id": provider.id,
        "name": provider.name,
        "base_url": provider.base_url,
        "auth_type": provider.auth_type,
        "is_active": provider.is_active,
        "sync": {
            "status": "done" if sync_result else "pending",
            "result": sync_result,
        },
    }


@router.put("/admin/providers/{provider_id}")
async def update_provider(
    provider_id: str,
    req: ProviderUpdate,
    admin: dict = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """部分更新；credentials 留空=不改；改 Key 后 resync=true 触发重新拉取"""
    provider = await db.get(Provider, provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail={"error": {"message": "Provider not found", "type": "not_found", "code": "provider_not_found"}})

    spec = get_spec(provider.name)
    if req.base_url is not None:
        provider.base_url = req.base_url
    if req.auth_type is not None:
        provider.auth_type = req.auth_type
    if req.timeout_ms is not None:
        provider.timeout_ms = req.timeout_ms
    if req.is_active is not None:
        provider.is_active = req.is_active

    resync = req.resync
    if req.credentials is not None and req.credentials.api_key:
        provider.credentials_enc = encrypt_credentials({"api_key": req.credentials.api_key})
        resync = True   # 换了 Key 必须重新拉取/验证

    await db.commit()
    await db.refresh(provider)

    sync_result: Optional[SyncResult] = None
    if resync and spec:
        if spec.model_source == "static":
            sync_result = await sync_provider_models(db, provider, spec)
        else:
            run_sync_background(provider.id)

    item = _to_item(provider, await _model_count(db, provider.id))
    item["sync"] = {"status": "done" if sync_result else ("pending" if resync else None), "result": sync_result}
    return item


@router.delete("/admin/providers/{provider_id}")
async def delete_provider(
    provider_id: str,
    admin: dict = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """删除供应商 + 级联清理其独占模型与路由通道（单事务）"""
    provider = await db.get(Provider, provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail={"error": {"message": "Provider not found", "type": "not_found", "code": "provider_not_found"}})

    # 1. 找"仅由此 provider 承载"的模型（删通道后即成孤儿）
    orphan_ids = (
        select(RouteChannel.model_id).where(
            RouteChannel.provider_id == provider_id,
            RouteChannel.model_id.notin_(
                select(RouteChannel.model_id).where(RouteChannel.provider_id != provider_id)
            ),
        ).distinct()
    )
    orphans = [r[0] for r in (await db.execute(orphan_ids)).all()]

    # 2. 删通道
    await db.execute(delete(RouteChannel).where(RouteChannel.provider_id == provider_id))
    # 3. 删孤儿模型的别名（PostgreSQL 外键会拦，SQLite 兜底一致）
    if orphans:
        await db.execute(delete(ModelAlias).where(ModelAlias.model_id.in_(orphans)))
        await db.execute(delete(ModelCatalog).where(ModelCatalog.id.in_(orphans)))
    # 4. 删 provider
    await db.execute(delete(Provider).where(Provider.id == provider_id))
    await db.commit()

    # 模型清单变化 → 自动刷新 Codex 模型目录
    from src.services.codex_catalog import maybe_sync_background
    maybe_sync_background()

    logger.info(f"admin deleted provider {provider.name} (admin={admin.get('email')}): {len(orphans)} orphan models")
    return {"message": "Provider deleted", "deleted": {"channels": len(orphans) + 0, "models": len(orphans)}}


@router.post("/admin/providers/{provider_id}/sync")
async def sync_provider(
    provider_id: str,
    admin: dict = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """手动触发同步（前台执行，返回完整结果）"""
    provider = await db.get(Provider, provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail={"error": {"message": "Provider not found", "type": "not_found", "code": "provider_not_found"}})
    spec = get_spec(provider.name)
    if not spec:
        raise HTTPException(status_code=400, detail={"error": {"message": f"Unknown provider: {provider.name}", "type": "invalid_request_error", "code": "unknown_provider"}})
    result = await sync_provider_models(db, provider, spec)
    return result


@router.post("/admin/providers/{provider_id}/test")
async def test_provider(
    provider_id: str,
    admin: dict = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """连通性测试（请求上游 /models 或健康端点）"""
    from src.providers import build_provider
    provider = await db.get(Provider, provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail={"error": {"message": "Provider not found", "type": "not_found", "code": "provider_not_found"}})
    adapter = build_provider(provider)
    if not adapter:
        raise HTTPException(status_code=400, detail={"error": {"message": "Cannot build provider adapter", "type": "invalid_request_error", "code": "adapter_error"}})
    try:
        ok = await adapter.health_check()
        return {"ok": ok, "message": "连接正常" if ok else "连接失败（上游不可达或 Key 无效）"}
    except Exception as e:
        return {"ok": False, "message": f"连接失败: {e}"}
