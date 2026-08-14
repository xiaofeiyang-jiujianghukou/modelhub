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
from src.db.models import Provider
from src.services.crypto import decrypt_credentials
from src.services.model_key import format_model_key, parse_model_key


def _load_codex_instructions() -> str:
    """加载官方 Codex agent 指令（config/codex_instructions.txt，源自直连 catalog）。

    关键行为约束（缺失会导致网关接的模型行为与官方不一致、审批变多）：
    - 禁止链式 shell 命令（`&&`/`;`），改用 multi_tool_use.parallel 并行
    - 用 apply_patch 编辑文件，不用 cat/echo 写文件
    - 危险命令（git reset --hard 等）才需审批
    一句话 fallback 缺这些约束 → 模型生成链式命令 → Codex 判 unknown → 每步审批。
    """
    p = Path(__file__).resolve().parent.parent.parent / "config" / "codex_instructions.txt"
    try:
        return p.read_text(encoding="utf-8").strip()
    except Exception:
        return "You are a helpful coding assistant. Follow the user's instructions carefully."

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
    # 恰好 1 个档位：0.147 的模型选择器在 supported_reasoning_efforts.len()==1 时才选完即关
    # 元素结构为 ReasoningEffortPreset{effort, description(必填)}——
    # 旧格式 {effort, options} 缺 description 会报 "missing field description"（黑盒+源码确认）
    "supported_reasoning_levels": [{"effort": "medium", "description": "Medium"}],
    "shell_type": "shell_command",
    "visibility": "list",
    "supported_in_api": True,
    "priority": 0,
    "upgrade": None,
    "support_verbosity": False,
    "default_verbosity": None,
    # 必须 freeform：null 时 Codex 不给该模型内置 apply_patch 编辑工具，
    # 模型只能用 Bash 写文件，untrusted 策略下每个 Bash 都要授权（"婆婆妈妈"）。
    # 直连 DeepSeek（cc-switch）catalog 就是 freeform，行为一致。
    "apply_patch_tool_type": "freeform",
    "truncation_policy": {"mode": "bytes", "limit": 10000},
    "supports_image_detail_original": False,
    "context_window": 131072,
    "max_context_window": 131072,
    "experimental_supported_tools": [],
    "supports_parallel_tool_calls": True,
    "model_messages": {
        # 与 Codex 官方契约对齐（直连 catalog 同款结构）：approvals 等缺省 null，
        # 避免 Codex 因缺少字段对模型走最保守的逐命令审批
        "approvals": None,
        "auto_review": None,
        "collaboration_modes": None,
        # 官方完整 agent 指令（含「禁止链式命令、用并行、用 apply_patch」等约束）
        "instructions_template": _load_codex_instructions(),
    },
}


# 厂商名（synced_from 供应商 key → 显示名）
VENDOR_LABELS = {
    "ark": "方舟",
    "deepseek": "DeepSeek",
    "glm": "智谱",
    "bailian": "百炼",
    "hunyuan": "混元",
    "moonshot": "Kimi",
    "minimax": "MiniMax",
    "openai": "OpenAI",
    "anthropic": "Claude",
    "grok": "Grok",
    "gemini": "Gemini",
}


# 模型 ID 前缀 → 厂商（按模型原生归属推断，比 synced_from 稳定——
# 多供应商镜像同一模型时 synced_from 会被后同步者覆盖，如百炼镜像的 deepseek-v4-pro）
MODEL_ID_VENDOR_PREFIXES = [
    ("ark-", "方舟"),
    ("doubao-", "方舟"),
    ("deepseek-", "DeepSeek"),
    ("glm-", "智谱"),
    ("qwen", "百炼"),
    ("kimi-", "Kimi"),
    ("moonshot-", "Kimi"),
    ("minimax-", "MiniMax"),
    ("abab", "MiniMax"),
    ("gpt-", "OpenAI"),
    ("o1", "OpenAI"),
    ("o3", "OpenAI"),
    ("o4", "OpenAI"),
    ("claude-", "Claude"),
    ("grok-", "Grok"),
    ("gemini-", "Gemini"),
    ("hunyuan-", "混元"),
]


def vendor_of(slug: str, synced_from: str | None) -> str:
    """模型厂商中文名（synced_from 即厂商 key，直接映射）"""
    return VENDOR_LABELS.get(synced_from or "", synced_from or "未知")


def _describe(slug: str, synced_from: str | None) -> str:
    """模型描述统一格式：[厂商][模型名]（slug 为「厂商key/模型名」时只取模型名部分）"""
    vendor = VENDOR_LABELS.get(synced_from or "", synced_from or "未知")
    mid = parse_model_key(slug)[0]
    return f"[{vendor}][{mid}]"


def configured_catalog_path() -> Optional[Path]:
    """配置的 Codex 目录路径（.env CODEX_CATALOG_PATH），未配置返回 None"""
    path = settings.codex_catalog_path
    if not path:
        return None
    return Path(path).expanduser()


async def load_visible_models(db: AsyncSession) -> list[dict]:
    """可见模型（与 /v1/models 同一规则）：供应商 活跃 + 已配置 Key + 同步成功"""
    providers = (await db.execute(select(Provider))).scalars().all()
    usable_names = {
        p.name for p in providers
        if p.is_active
        and p.last_sync_status == "success"
        and bool(decrypt_credentials(p.credentials_enc).get("api_key"))
    }
    if not usable_names:
        return []

    from src.db.models import Model
    result = await db.execute(
        select(Model).where(Model.is_active == True)  # noqa: E712
    )
    models = [m for m in result.scalars().all() if m.vendor in usable_names]

    # 每条 = 厂商key/模型名（唯一键，Codex 选中即精确路由到对应厂商）
    return [
        {"id": format_model_key(m.model, m.vendor), "display_name": m.display_name or m.model,
         "context_window": m.context_window, "synced_from": m.vendor}
        for m in models
    ]


def render_catalog(gateway_models: list[dict], existing: dict) -> dict:
    """合并渲染 Codex 目录（保留已有条目的自定义字段）"""
    existing_models = {m.get("slug"): m for m in existing.get("models", [])}
    gateway_ids = {m["id"] for m in gateway_models}
    ctx_map = {m["id"]: m["context_window"] for m in gateway_models}
    name_map = {m["id"]: m["display_name"] for m in gateway_models}
    vendor_map = {m["id"]: m.get("synced_from") for m in gateway_models}

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
        # 描述统一 [厂商][模型名] 格式
        entry["description"] = _describe(mid, vendor_map.get(mid))
        # 行为相关字段强制用模板值（0.147 选择器在 reasoning 档位 len==1 时才选完即关）
        entry["default_reasoning_level"] = _TEMPLATE["default_reasoning_level"]
        entry["supported_reasoning_levels"] = _TEMPLATE["supported_reasoning_levels"]
        # 授权/工具行为相关字段也必须强制用模板值：
        # apply_patch_tool_type 为 null 时 Codex 不给模型内置编辑工具 → 退化成 Bash 写文件，
        # untrusted 策略下每个 Bash 都要授权（"婆婆妈妈"）；model_messages 缺 approvals 等键同理。
        # setdefault 不会覆盖已有条目（旧值 null 会残留），必须强制覆盖。
        entry["apply_patch_tool_type"] = _TEMPLATE["apply_patch_tool_type"]
        entry["model_messages"] = _TEMPLATE["model_messages"]
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
