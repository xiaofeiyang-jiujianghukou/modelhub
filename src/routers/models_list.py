"""
GET /v1/models 模型列表接口
返回当前所有可用模型及其定价信息
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from src.database import get_db
from src.models import ModelCatalog, ModelAlias

router = APIRouter(tags=["Models"])


def _model_to_item(model: ModelCatalog) -> dict:
    """将模型转换为 OpenAI 格式条目（含 Codex 兼容元数据）"""
    item = {
        "id": model.id,
        "object": "model",
        "created": int(model.created_at.timestamp()),
        "owned_by": model.owned_by or "unknown",
        "meta": {
            "type": model.model_type,
            "supports_streaming": model.supports_streaming,
            "price_source": model.price_source or "default",
            "synced_from": model.synced_from,
        },
    }

    # Codex / OpenAI 客户端需要顶层字段获取元数据
    if model.context_window:
        item["context_window"] = model.context_window
        item["meta"]["context_window"] = model.context_window

    # LLM 定价
    if model.model_type == "llm":
        item["meta"]["input_price_per_1m_tokens"] = float(model.input_price) if model.input_price else None
        item["meta"]["output_price_per_1m_tokens"] = float(model.output_price) if model.output_price else None

    # 图像定价
    elif model.model_type == "image":
        item["meta"]["price_per_image"] = float(model.unit_price) if model.unit_price else None

    # 视频定价
    elif model.model_type == "video":
        item["meta"]["price_per_second"] = float(model.unit_price) if model.unit_price else None

    return item


@router.get("/models")
async def list_models(db: AsyncSession = Depends(get_db)):
    """
    获取所有可用模型列表

    兼容 OpenAI API 格式，额外添加 meta 字段包含定价信息
    可见性规则：模型至少有一条通道，其供应商满足 活跃 + 已配置 Key + 同步成功
    """
    # 可用供应商：活跃 + 已配置 Key（解密判断）+ 同步成功
    from src.models import Provider, RouteChannel as _RC
    from src.services.crypto import decrypt_credentials as _decrypt
    providers = (await db.execute(select(Provider))).scalars().all()
    usable_provider_ids = {
        p.id for p in providers
        if p.is_active
        and p.last_sync_status == "success"
        and bool(_decrypt(p.credentials_enc).get("api_key"))
    }

    # 有可用通道的模型
    visible_model_ids = set(
        (await db.execute(
            select(_RC.model_id).where(
                _RC.provider_id.in_(usable_provider_ids),
                _RC.is_active == True,  # noqa: E712
            ).distinct()
        )).scalars().all()
    )

    # 查询所有活跃模型
    result = await db.execute(
        select(ModelCatalog)
        .where(ModelCatalog.is_active == True)
        .order_by(ModelCatalog.model_type, ModelCatalog.id)
    )
    models = [m for m in result.scalars().all() if m.id in visible_model_ids]

    # 查询所有别名
    alias_result = await db.execute(select(ModelAlias))
    aliases = alias_result.scalars().all()
    alias_map = {a.model_id: a.alias for a in aliases}

    data = []
    for model in models:
        item = _model_to_item(model)
        data.append(item)

        # 添加别名条目
        if model.id in alias_map:
            alias_item = item.copy()
            alias_item["id"] = alias_map[model.id]
            alias_item["alias_for"] = model.id
            data.append(alias_item)

    return {
        "object": "list",
        "data": data,
    }


@router.get("/models/{model_id}")
async def get_model(model_id: str, db: AsyncSession = Depends(get_db)):
    """
    获取单个模型元数据（OpenAI API 兼容，Codex 启动时调用）
    """
    # 直接查询或通过别名
    model = await ModelCatalog.get_by_id_or_alias(db, model_id)
    if not model or not model.is_active:
        raise HTTPException(
            status_code=404,
            detail={"error": {"message": f"Model '{model_id}' does not exist", "type": "invalid_request_error", "code": "invalid_model"}},
        )
    return _model_to_item(model)
