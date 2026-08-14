"""
Codex 模型目录同步服务

Codex 的 model_catalog_json 是"启动时加载的本地 JSON 文件"（官方文档：loaded on startup，
仅支持本地路径，不支持 URL/热更新）。因此实现"实时"的方式 = 模型变化时自动重写该文件，
Codex 每次启动（含 cc-switch 切换后）即看到最新可用模型。

- 可见性规则与 /v1/models 一致：模型至少一条通道，供应商 活跃 + 已配置 Key + 同步成功
- 合并策略：已有条目保留用户自定义字段，仅同步上下文；新增模型按 Codex 契约模板补齐
"""

import asyncio
import json
import os
from pathlib import Path
from typing import Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.models import Provider, RouteChannel
from src.services.crypto import decrypt_credentials

# Codex 契约字段默认模板（新模型条目）
# 0.147 起必填（黑盒验证）：description（可为 null）、default_reasoning_level、
# supported_reasoning_levels、shell_type、visibility、supported_in_api、priority、
# support_verbosity、apply_patch_tool_type、truncation_policy、
# supports_image_detail_original、supports_parallel_tool_calls、
# 以及 base_instructions 或 model_messages.instructions_template 二选一
_TEMPLATE = {
    "display_name": "",
    "description": "",   # 渲染时统一为 [厂商][模型] 格式
    "default_reasoning_level": "medium",
    "supported_reasoning_levels": [],
    "shell_type": "shell_command",
    "visibility": "list",
    "supported_in_api": True,
    "priority": 0,
    "upgrade": None,
    "support_verbosity": False,
    "default_verbosity": None,
    "apply_patch_tool_type": None,
    "truncation_policy": {"mode": "bytes", "limit": 10000},
    "supports_image_detail_original": False,
    "context_window": 131072,
    "max_context_window": 131072,
    "experimental_supported_tools": [],
    "supports_parallel_tool_calls": True,
    "model_messages": {
        "instructions_template": "You are a helpful coding assistant. Follow the user's instructions carefully."
    },
}


def _describe(display_name: str, slug: str) -> str:
    """模型描述统一格式：[厂商][模型]"""
    return f"[{display_name}][{slug}]"


def configured_catalog_path() -> Optional[Path]:
    """配置的 Codex 目录路径（.env CODEX_CATALOG_PATH），未配置返回 None"""
    path = settings.codex_catalog_path
    if not path:
        return None
    return Path(path).expanduser()


async def load_visible_models(db: AsyncSession) -> list[dict]:
    """可见模型（与 /v1/models 同一规则）：供应商 活跃 + 已配置 Key + 同步成功"""
    providers = (await db.execute(select(Provider))).scalars().all()
    usable_ids = {
        p.id for p in providers
        if p.is_active
        and p.last_sync_status == "success"
        and bool(decrypt_credentials(p.credentials_enc).get("api_key"))
    }
    if not usable_ids:
        return []

    from src.models import ModelCatalog
    result = await db.execute(
        select(ModelCatalog)
        .join(RouteChannel, RouteChannel.model_id == ModelCatalog.id)
        .where(
            ModelCatalog.is_active == True,  # noqa: E712
            RouteChannel.provider_id.in_(usable_ids),
            RouteChannel.is_active == True,  # noqa: E712
        )
        .distinct()
    )
    models = result.scalars().all()
    return [
        {"id": m.id, "display_name": m.display_name or m.id, "context_window": m.context_window}
        for m in models
    ]


def render_catalog(gateway_models: list[dict], existing: dict) -> dict:
    """合并渲染 Codex 目录（保留已有条目的自定义字段）"""
    existing_models = {m.get("slug"): m for m in existing.get("models", [])}
    gateway_ids = {m["id"] for m in gateway_models}
    ctx_map = {m["id"]: m["context_window"] for m in gateway_models}
    name_map = {m["id"]: m["display_name"] for m in gateway_models}

    new_models = []
    for mid in sorted(gateway_ids):
        entry = existing_models.get(mid)
        if entry is None:
            entry = dict(_TEMPLATE)
            entry["slug"] = mid
            entry["display_name"] = name_map.get(mid) or mid
        else:
            # 已有条目：用模板补齐缺失字段（旧条目可能缺字段导致 Codex 解析失败）
            for k, v in _TEMPLATE.items():
                entry.setdefault(k, v)
        # 描述统一 [厂商][模型] 格式
        entry["description"] = _describe(entry.get("display_name") or mid, mid)
        ctx = ctx_map.get(mid)
        if ctx:
            entry["context_window"] = ctx
            entry["max_context_window"] = ctx
            entry["auto_compact_token_limit"] = int(ctx * 0.75)
        new_models.append(entry)

    return {"models": new_models}


def write_catalog_file(path: Path, catalog: dict) -> None:
    """写 Codex 目录文件（原子写：先临时文件再替换）"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


async def sync_codex_catalog(db: AsyncSession, path: Optional[Path] = None) -> dict:
    """同步 Codex 目录（传入会话），返回统计"""
    path = path or configured_catalog_path()
    if not path:
        return {"skipped": True, "reason": "CODEX_CATALOG_PATH 未配置"}
    gateway_models = await load_visible_models(db)
    existing = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    catalog = render_catalog(gateway_models, existing)
    write_catalog_file(path, catalog)
    logger.info(f"codex catalog synced: {len(catalog['models'])} models -> {path}")
    return {"total": len(catalog["models"]), "path": str(path)}


# 串行化锁：并发触发（多供应商连续同步）时避免基于过时状态互相覆盖
_sync_lock = asyncio.Lock()


def maybe_sync_background() -> None:
    """后台触发同步（独立会话；未配置路径时跳过）——模型变化后的钩子入口"""
    if not configured_catalog_path():
        return

    async def _task():
        async with _sync_lock:
            from src.database import AsyncSessionLocal
            async with AsyncSessionLocal() as db:
                await sync_codex_catalog(db)

    asyncio.create_task(_task())
