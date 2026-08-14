"""
GET /v1/models 模型列表接口
返回当前所有可用模型及其定价信息
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from src.config import settings
from src.database import get_db
from src.db.models import Model
from src.providers.provider_registry import get_spec
from src.services.codex_catalog import VENDOR_LABELS
from src.services.model_key import format_model_key, parse_model_key


def _convert_price(value, from_currency: str, to_currency: Optional[str] = None):
    """价格换算：存储为「原始币种+金额」，跨币种显示时实时换算（同币种直接返回，避免精度丢失）"""
    if value is None:
        return None
    v = float(value)
    fc = (from_currency or "USD").upper()
    tc = (to_currency or "USD").upper()
    if fc == tc:
        return v
    if tc == "CNY" and fc == "USD":
        return round(v * settings.usd_to_cny_rate, 4)
    if tc == "USD" and fc == "CNY":
        return round(v / settings.usd_to_cny_rate, 4)
    return v

router = APIRouter(tags=["Models"])


def _model_to_item(model: Model, currency: Optional[str] = None) -> dict:
    """将模型转换为 OpenAI 格式条目（含 Codex 兼容元数据）；currency='CNY' 时价格按汇率换算"""
    item = {
        "id": format_model_key(model.model, model.vendor),
        "object": "model",
        "created": int(model.created_at.timestamp()),
        "owned_by": model.owned_by or "unknown",
        "meta": {
            "type": model.model_type,
            "supports_streaming": model.supports_streaming,
            "price_source": model.price_source or "default",
            "synced_from": model.synced_from,
            "vendor": VENDOR_LABELS.get(model.vendor, model.vendor),
            "vendor_key": model.vendor,
        },
    }

    # Codex / OpenAI 客户端需要顶层字段获取元数据
    if model.context_window:
        item["context_window"] = model.context_window
        item["meta"]["context_window"] = model.context_window

    # LLM 定价
    if model.model_type == "llm":
        item["meta"]["input_price_per_1m_tokens"] = _convert_price(model.input_price, model.price_currency, currency)
        item["meta"]["output_price_per_1m_tokens"] = _convert_price(model.output_price, model.price_currency, currency)

    # 图像定价
    elif model.model_type == "image":
        item["meta"]["price_per_image"] = _convert_price(model.unit_price, model.price_currency, currency)

    # 视频定价
    elif model.model_type == "video":
        item["meta"]["price_per_second"] = _convert_price(model.unit_price, model.price_currency, currency)

    return item


@router.get("/models")
async def list_models(
    http_req: Request,
    search: Optional[str] = None,
    provider: Optional[str] = None,
    sort: Optional[str] = None,
    limit: Optional[int] = None,
    offset: int = 0,
    currency: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """
    获取所有可用模型列表

    兼容 OpenAI API 格式，额外添加 meta 字段包含定价信息
    可见性规则：模型至少有一条通道，其供应商满足 活跃 + 已配置 Key + 同步成功
    带 anthropic-version 头（Claude Code）时返回 Anthropic 协议格式（/model 下拉数据源）

    OpenAI 格式额外支持（控制台模型面板用，不影响兼容客户端）：
    - search: 模糊匹配模型 ID 或显示名
    - provider: 按厂商 key 过滤（如 ark/deepseek/glm/bailian）
    - sort: 排序字段（id/context_window/input_price/output_price，前缀 - 表示降序）
    - limit/offset: 分页
    """
    # 可用供应商：活跃 + 已配置 Key（解密判断）+ 同步成功
    from src.db.models import Provider
    from src.services.crypto import decrypt_credentials as _decrypt
    providers = (await db.execute(select(Provider))).scalars().all()
    usable_provider_names = {
        p.name for p in providers
        if p.is_active
        and p.last_sync_status == "success"
        and bool(_decrypt(p.credentials_enc).get("api_key"))
    }

    # 查询所有活跃模型（每条 = 模型名 + 厂商），只保留「厂商可用」的记录
    result = await db.execute(
        select(Model)
        .where(Model.is_active == True)
        .order_by(Model.model_type, Model.model, Model.vendor)
    )
    models = [m for m in result.scalars().all() if m.vendor in usable_provider_names]

    # Claude Code 场景（带 anthropic-version 头）→ Anthropic 协议格式，始终返回全部
    # CLAUDE_CODE_USE_GATEWAY + ENABLE_GATEWAY_MODEL_DISCOVERY 时 Claude Code 启动拉取本接口，
    # 只列出 claude-/anthropic- 开头的 ID，因此包装为 claude-<网关模型ID>，
    # 请求时由 anthropic 路由剥壳映射回真实模型
    # 模型名可重复（多厂商），Claude Code 发现列表只需唯一模型名（厂商由路由时选择）
    if http_req.headers.get("anthropic-version"):
        return {
            "data": [
                {
                    "type": "model",
                    "id": f"claude-{format_model_key(m.model, m.vendor)}",
                    "display_name": format_model_key(m.model, m.vendor),
                    "created_at": m.created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
                for m in models
            ],
            "has_more": False,
            "first_id": None,
            "last_id": None,
        }

    # ── OpenAI 格式：过滤 / 排序 / 分页（控制台模型面板用）──
    filtered = models
    if provider:
        filtered = [m for m in filtered if m.vendor == provider]
    if search:
        s = search.lower()
        filtered = [m for m in filtered if s in (m.model or "").lower() or s in (m.display_name or "").lower()]
    if sort:
        desc = sort.startswith("-")
        key = sort.lstrip("-")
        sorters = {
            "id": lambda m: (m.model or "").lower(),
            "context_window": lambda m: m.context_window or 0,
            "input_price": lambda m: m.input_price or 0,
            "output_price": lambda m: m.output_price or 0,
        }
        if key in sorters:
            filtered = sorted(filtered, key=sorters[key], reverse=desc)

    total = len(filtered)
    if limit is not None:
        filtered = filtered[offset : offset + limit]

    # 可用厂商列表（下拉用）
    vendor_keys = {m.vendor for m in models}
    providers_meta = []
    for key in sorted(vendor_keys):
        spec = get_spec(key)
        providers_meta.append({"key": key, "display_name": spec.display_name if spec else key})

    data = []
    for model in filtered:
        item = _model_to_item(model, currency)
        data.append(item)

        # 别名作为独立条目（客户端可直接用别名请求，路由时解析到真实模型）
        if model.alias:
            alias_item = item.copy()
            alias_item["id"] = model.alias
            alias_item["alias_for"] = format_model_key(model.model, model.vendor)
            data.append(alias_item)

    return {
        "object": "list",
        "data": data,
        "total": total,
        "providers": providers_meta,
    }


@router.get("/models/{model_id}")
async def get_model(model_id: str, db: AsyncSession = Depends(get_db)):
    """
    获取单个模型元数据（OpenAI API 兼容，Codex 启动时调用）
    """
    mid, vendor = parse_model_key(model_id)
    if vendor:
        model = await Model.get_by_model_and_vendor(db, mid, vendor)
    else:
        model = await Model.get_by_alias(db, mid)
    if not model or not model.is_active:
        raise HTTPException(
            status_code=404,
            detail={"error": {"message": f"Model '{model_id}' does not exist", "type": "invalid_request_error", "code": "invalid_model"}},
        )
    return _model_to_item(model)
