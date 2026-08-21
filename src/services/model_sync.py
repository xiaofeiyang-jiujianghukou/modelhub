"""
模型同步服务：从上游供应商拉取模型列表并入库（幂等 upsert）

- 4 种上游响应解析器：openai（标准/兼容）/ grok（OpenAI 超集带官方价）/ anthropic（游标分页）/ gemini（原生结构 + 兼容兜底）
- model_source='static' 的供应商直接使用注册表内置清单
- 拉取/入库失败不抛未捕获异常，写入 provider.last_sync_status/last_sync_error
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import httpx
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db.models import Model, ModelReference, Provider
from src.providers.base import make_upstream_client
from src.providers.provider_registry import (
    ProviderSpec, _excluded, get_spec,
)
from src.services.crypto import decrypt_credentials
from src.services import model_reference


@dataclass
class SyncedModel:
    """上游拉取到的模型（统一格式）"""
    id: str
    display_name: str = ""
    owned_by: str = ""
    model_type: str = "llm"
    input_price: Optional[float] = None
    output_price: Optional[float] = None
    price_currency: str = "USD"
    context_window: Optional[int] = None
    price_source: str = "default"
    upstream_model: Optional[str] = None   # 上游真实模型名（static 清单与网关 id 可能不同）
    created: Optional[int] = None          # 上游 Unix 时间戳（判断新旧版本用）


class SyncResult(BaseModel):
    """同步结果（API 返回结构）"""
    status: str = "success"          # success | error
    added: int = 0
    updated: int = 0
    skipped: int = 0
    removed: int = 0                 # keep_latest_only 移除的旧版本数
    errors: list[str] = []
    model_ids: list[str] = []


# ── 上游拉取 ──────────────────────────────────────────────────────────────────

async def _http_get(url: str, headers: dict, timeout: float, params: Optional[dict] = None) -> dict:
    async with make_upstream_client(timeout=timeout) as client:
        resp = await client.get(url, headers=headers, params=params)
        if resp.status_code == 401 or resp.status_code == 403:
            raise ValueError(f"API Key 无效或无权访问（HTTP {resp.status_code}）")
        if resp.status_code != 200:
            raise ValueError(f"上游请求失败 HTTP {resp.status_code}: {resp.text[:200]}")
        return resp.json()


async def _fetch_openai_style(spec: ProviderSpec, base_url: str, api_key: str, timeout: float) -> list[SyncedModel]:
    """标准 OpenAI 结构 + Moonshot 能力字段"""
    data = await _http_get(
        f"{base_url.rstrip('/')}/models",
        {"Authorization": f"Bearer {api_key}"},
        timeout,
    )
    models: list[SyncedModel] = []
    for item in data.get("data", []):
        mid = item.get("id", "")
        if not mid or _excluded(mid, spec.exclude_patterns):
            continue
        models.append(SyncedModel(
            id=mid,
            display_name=item.get("display_name", "") or item.get("displayName", ""),
            owned_by=item.get("owned_by", ""),
            context_window=item.get("context_length") or item.get("context_window"),
            price_source="default",
        ))
    return models


def _ark_base_name(model_id: str) -> str:
    """去掉方舟模型 id 的日期/版本后缀，得到基础名（同组识别）

    deepseek-v4-pro-ga-260813  → deepseek-v4-pro
    doubao-seed-2-0-lite-260428 → doubao-seed-2-0-lite
    doubao-seedream-4-0-20260415 → doubao-seedream-4-0
    glm-5-2-260617              → glm-5-2（无同组）
    """
    import re as _re
    return _re.sub(r'-(?:ga-)?\d{6,8}$', '', model_id)


def _ark_family(model_id: str) -> str:
    """方舟模型家族（系列）识别：每家族只保留最新代表时用

    doubao-seedream-5-0-pro-260628 → seedream（图像）
    doubao-seedance-2-5-260628     → seedance（视频）
    doubao-seed-2-1-pro-260628     → doubao-seed（LLM 主力线）
    qwen3-32b-20250429             → qwen
    glm-5-2-260617                 → glm
    deepseek-v4-pro-ga-260813      → deepseek
    """
    low = model_id.lower()
    if low.startswith("doubao-seedream"):
        return "seedream"
    if low.startswith("doubao-seedance"):
        return "seedance"
    if low.startswith("doubao-seed"):
        return "doubao-seed"
    for fam in ("qwen", "glm", "deepseek", "kimi", "minimax"):
        if low.startswith(fam):
            return fam
    return "other"


def _series_key(model_id: str) -> str:
    """系列识别（series_latest 用）：deepseek 按 pro/flash 分线，其余复用 _ark_family；other 不参与系列去重"""
    low = model_id.lower()
    fam = _ark_family(low)
    if fam == "deepseek":
        if "pro" in low:
            return "deepseek-pro"
        if "flash" in low:
            return "deepseek-flash"
    return fam


def _version_rank(model_id: str) -> tuple:
    """模型 ID 的版本数值序列（无日期命名的新旧比较）：
    glm-5.2 → (5,2) > glm-5.1 → (5,1) > glm-5 → (5,)
    minimax-m2.7 → (2,7) > minimax-m2.5 → (2,5)；deepseek-v4-flash-202605 → (4,202605)
    """
    import re as _re
    parts: list[int] = []
    for tok in _re.findall(r'\d+(?:\.\d+)*', model_id):
        parts.extend(int(x) for x in tok.split('.'))
    return tuple(parts)


# 方舟家族保留特例：family → 保留的基础名集合（按能力高到低保留多个）
# 未列出的家族默认只保留 created 最新一个代表
# 注意：仅保留 Coding Plan 套餐内模型（实测），旗舰/按量模型（evolving/2-1-pro/qwen 全系）不保留
_ARK_KEEP_BASES: dict[str, set[str]] = {
    "deepseek": {"deepseek-v4-pro", "deepseek-v4-flash"},        # 主力留俩：pro(强)+flash(快)
    "doubao-seed": {"doubao-seed-2-1-turbo", "doubao-seed-2-0-pro"},  # 套餐内最强俩：2-1-turbo>2-0-pro
}

# 方舟管理的全部家族：同步时这些家族下未保留的模型一律清理（含被 exclude 排除后残留的旧模型）
# kimi/minimax 等方舟 /models 不返回的家族不在内，保留不动
_ARK_MANAGED_FAMILIES = {"seedream", "seedance", "doubao-seed", "qwen", "glm", "deepseek"}


async def _fetch_ark(spec: ProviderSpec, base_url: str, api_key: str, timeout: float) -> list[SyncedModel]:
    """方舟：OpenAI 风格 /models，但带 status 生命周期字段（Shutdown/Retiring/空）

    - 跳过 status=Shutdown/Retiring（已退役下线，拉进来也无法调用）
    - 跳过 exclude_patterns（embedding/3D/smart-router 等网关无调用协议的类型）
    - model_type 按 id 识别：seedream→image / seedance→video / 其余→llm
      （方舟 Agent Plan 已接入 GLM/deepseek/qwen 等，统一按 llm 收编）
    - created 携带上游时间戳，供 keep_latest_only 同组去重
    """
    data = await _http_get(
        f"{base_url.rstrip('/')}/models",
        {"Authorization": f"Bearer {api_key}"},
        timeout,
    )
    models: list[SyncedModel] = []
    for item in data.get("data", []):
        mid = item.get("id", "")
        if not mid or _excluded(mid, spec.exclude_patterns):
            continue
        status = (item.get("status") or "").lower()
        if status in ("shutdown", "retiring"):
            continue
        low = mid.lower()
        if "seedance" in low:
            mtype = "video"
        elif "seedream" in low:
            mtype = "image"
        else:
            mtype = "llm"
        models.append(SyncedModel(
            id=mid,
            display_name=item.get("display_name", "") or item.get("name", ""),
            owned_by="ark",
            model_type=mtype,
            created=item.get("created"),
            price_source="default",
        ))
    return models


async def _fetch_grok(spec: ProviderSpec, base_url: str, api_key: str, timeout: float) -> list[SyncedModel]:
    """xAI：OpenAI 超集，自带官方价格字段"""
    data = await _http_get(
        f"{base_url.rstrip('/')}/models",
        {"Authorization": f"Bearer {api_key}"},
        timeout,
    )
    models: list[SyncedModel] = []
    for item in data.get("data", []):
        mid = item.get("id", "")
        if not mid or _excluded(mid, spec.exclude_patterns):
            continue
        # 官方价格字段（每 1M tokens），缺失时回退默认价并标 default
        in_price = item.get("prompt_text_token_price")
        out_price = item.get("completion_text_token_price")
        price_source = "official" if in_price is not None else "default"
        models.append(SyncedModel(
            id=mid,
            display_name=item.get("display_name", ""),
            owned_by=item.get("owned_by", "xai"),
            context_window=item.get("context_length"),
            input_price=in_price,
            output_price=out_price,
            price_source=price_source,
        ))
    return models


async def _fetch_anthropic(spec: ProviderSpec, base_url: str, api_key: str, timeout: float) -> list[SyncedModel]:
    """Anthropic：非 OpenAI 结构，after_id 游标分页"""
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    models: list[SyncedModel] = []
    after_id: Optional[str] = None
    while True:
        url = f"{base_url.rstrip('/')}/v1/models"
        params = {"limit": 100}
        if after_id:
            params["after_id"] = after_id
        data = await _http_get(url, headers, timeout, params=params)
        for item in data.get("data", []):
            mid = item.get("id", "")
            if not mid or _excluded(mid, spec.exclude_patterns):
                continue
            models.append(SyncedModel(
                id=mid,
                display_name=item.get("display_name", ""),
                owned_by="anthropic",
                context_window=item.get("max_input_tokens") or None,
                price_source="default",
            ))
        if not data.get("has_more") or not data.get("last_id"):
            break
        after_id = data["last_id"]
        if len(models) > 500:   # 安全上限
            break
    return models


async def _fetch_gemini(spec: ProviderSpec, base_url: str, api_key: str, timeout: float) -> list[SyncedModel]:
    """Gemini 原生端点（models[].name 带 models/ 前缀）；4xx 降级 OpenAI 兼容端点"""
    models: list[SyncedModel] = []
    page_token: Optional[str] = None
    try:
        while True:
            params = {"pageSize": 100}
            if page_token:
                params["pageToken"] = page_token
            data = await _http_get(
                f"{base_url.rstrip('/')}/v1beta/models",
                {"Content-Type": "application/json"},
                timeout,
                params={**params, "key": api_key},
            )
            for item in data.get("models", []):
                name = item.get("name", "")
                mid = name.removeprefix("models/") if name else ""
                if not mid or _excluded(mid, spec.exclude_patterns):
                    continue
                # 只收支持文本生成的模型
                methods = item.get("supportedGenerationMethods", [])
                if methods and "generateContent" not in methods:
                    continue
                models.append(SyncedModel(
                    id=mid,
                    display_name=item.get("displayName", ""),
                    owned_by="google",
                    context_window=item.get("inputTokenLimit") or None,
                    price_source="default",
                ))
            if not data.get("nextPageToken"):
                break
            page_token = data["nextPageToken"]
            if len(models) > 500:
                break
        return models
    except ValueError as e:
        # 原生端点 4xx → 尝试 OpenAI 兼容端点
        if "HTTP 401" in str(e) or "HTTP 403" in str(e) or "HTTP 404" in str(e):
            logger.warning(f"gemini native models endpoint failed ({e}), fallback to openai-compat")
            data = await _http_get(
                f"{base_url.rstrip('/')}/v1beta/openai/models",
                {"Authorization": f"Bearer {api_key}"},
                timeout,
            )
            for item in data.get("data", []):
                mid = item.get("id", "")
                if not mid or _excluded(mid, spec.exclude_patterns):
                    continue
                models.append(SyncedModel(
                    id=mid,
                    display_name=item.get("display_name", ""),
                    owned_by="google",
                    price_source="default",
                ))
            return models
        raise


async def fetch_models(spec: ProviderSpec, base_url: str, api_key: str, timeout: float) -> list[SyncedModel]:
    """按注册表配置分发到对应解析器"""
    parser = spec.models_parser
    if parser == "ark":
        return await _fetch_ark(spec, base_url, api_key, timeout)
    if parser == "anthropic":
        return await _fetch_anthropic(spec, base_url, api_key, timeout)
    if parser == "gemini":
        return await _fetch_gemini(spec, base_url, api_key, timeout)
    if parser == "grok":
        return await _fetch_grok(spec, base_url, api_key, timeout)
    return await _fetch_openai_style(spec, base_url, api_key, timeout)


# ── 入库同步 ──────────────────────────────────────────────────────────────────

async def sync_provider_models(db: AsyncSession, provider: Provider, spec: ProviderSpec, prune: bool = False) -> SyncResult:
    """拉取（或读取静态清单）并 upsert 模型 + 路由通道；更新同步状态。prune 暂不启用"""
    result = SyncResult()
    now = datetime.now(timezone.utc)
    try:
        # 1. 获取待同步模型（无 Key 的供应商一律失败——未配置不可用）
        creds = decrypt_credentials(provider.credentials_enc)
        api_key = creds.get("api_key", "")
        if not api_key:
            raise ValueError("未配置 API Key，请先填写订阅密钥")
        if spec.model_source == "static":
            # static 供应商：模型清单 + 价格 + 上下文从 model_references 表查（界面/种子维护）
            refs = await model_reference.static_models_for(db, spec.key)
            synced = [
                SyncedModel(
                    id=r.model_id, display_name=r.display_name, owned_by=spec.key,
                    input_price=float(r.input_price) if r.input_price is not None else None,
                    output_price=float(r.output_price) if r.output_price is not None else None,
                    price_currency=r.price_currency,
                    context_window=r.context_window, price_source=r.price_source,
                    upstream_model=r.upstream_model or r.model_id,
                )
                for r in refs
            ]
        else:
            synced = await fetch_models(
                spec, provider.base_url, api_key,
                timeout=max(provider.timeout_ms / 1000.0, settings.upstream_timeout_seconds),
            )
            # 模型 ID 归一化：上游命名 → 网关统一 ID（同名模型合并，保留原始上游名用于路由）
            id_map = dict(spec.model_id_map)
            for sm in synced:
                mapped = id_map.get(sm.id)
                if mapped:
                    sm.upstream_model = sm.id
                    sm.id = mapped
            # keep_latest_only：每家族保留最新代表（created 最新）；
            #     有保留特例的家族（_ARK_KEEP_BASES）按基础名各留一个最新版（如 deepseek 的 pro+flash）
            #     保留后网关 id 去掉日期后缀（upstream 保留原始带日期 id 供路由）
            if spec.keep_latest_only:
                best: dict[tuple, SyncedModel] = {}
                for sm in synced:
                    fam = _ark_family(sm.id)
                    base = _ark_base_name(sm.id)
                    keep = _ARK_KEEP_BASES.get(fam)
                    if keep is not None:
                        if base not in keep:
                            continue  # 该家族指定保留的基础名之外丢弃（如 qwen 只留 qwen3-32b）
                        key = (fam, base)
                    else:
                        key = (fam,)  # 默认每家族只留 created 最新一个
                    cur = best.get(key)
                    if cur is None or (sm.created or -1) > (cur.created or -1):
                        best[key] = sm
                for sm in best.values():
                    sm.upstream_model = sm.id      # 原始带日期 id（路由用）
                    sm.id = _ark_base_name(sm.id)  # 网关 id 去掉日期后缀（如 doubao-seedream-5-0-pro）
                synced = list(best.values())
            # series_latest：同一系列只保留最新版本（用于无日期后缀的版本号命名，
            #     如 lkeap 的 glm-5.1/5.2、minimax-m2.5/m2.7）——按 _series_key 分组，
            #     组内取 _version_rank 最大者；deepseek 分 pro/flash 两线各留最新；
            #     other 系列（如 tc-code-latest）不参与去重原样保留
            if spec.series_latest:
                best_series: dict[str, SyncedModel] = {}
                for sm in synced:
                    series = _series_key(sm.id)
                    if series == "other":
                        continue
                    cur = best_series.get(series)
                    if cur is None or (_version_rank(sm.id), sm.created or -1) > (_version_rank(cur.id), cur.created or -1):
                        best_series[series] = sm
                keep_ids = {sm.id for sm in best_series.values()}
                synced = [sm for sm in synced if _series_key(sm.id) == "other" or sm.id in keep_ids]
            # api 拉取：价格/上下文优先用上游返回；上游缺失时用 model_references 表兜底
            # （兜底必须在去重改名之后执行，否则带日期 id 查不到 clean 名参考）
            for sm in synced:
                ref = await model_reference.reference_for(db, sm.id)
                ref_ip = float(ref.input_price) if ref and ref.input_price is not None else None
                ref_op = float(ref.output_price) if ref and ref.output_price is not None else None
                if (sm.input_price is None or sm.output_price is None):
                    if ref_ip is not None and ref_op is not None:
                        sm.input_price, sm.output_price = ref_ip, ref_op
                        sm.price_currency = ref.price_currency or "USD"
                        sm.price_source = ref.price_source or "official"
                    else:
                        # 官方未定价：不填网关默认价（避免误导），保持 None，price_source 标记 default
                        sm.price_source = "default"
                # 上下文：上游未返回时用表内参考值
                if sm.context_window is None and ref and ref.context_window is not None:
                    sm.context_window = ref.context_window

        # 1.9 批内去重：model_id_map 可能把多个上游 ID 归一到同一网关 ID（如
        #     glm-5-1 与 glm-5.1 -> glm-5.1），pending 对象未 flush 时同批 INSERT
        #     会撞复合主键；保留价格/上下文信息更全的一条
        def _richness(s: SyncedModel) -> int:
            return (2 if s.input_price is not None else 0) + \
                   (2 if s.output_price is not None else 0) + \
                   (1 if s.context_window is not None else 0)

        _dedup: dict[str, SyncedModel] = {}
        for sm in synced:
            cur = _dedup.get(sm.id)
            if cur is None:
                _dedup[sm.id] = sm
            else:
                rs, rc = _richness(sm), _richness(cur)
                # 信息量优先；同信息量保留最新版本（上游时间戳新者）
                if rs > rc or (rs == rc and (sm.created or -1) > (cur.created or -1)):
                    _dedup[sm.id] = sm
        synced = list(_dedup.values())

        # 2. 事务内 upsert（模型名 + 厂商 复合唯一）
        for sm in synced:
            upstream = sm.upstream_model or sm.id
            model = await db.scalar(
                select(Model).where(
                    Model.model == sm.id,
                    Model.vendor == spec.key,
                )
            )
            if model:
                if sm.display_name:
                    model.display_name = sm.display_name
                if sm.context_window is not None:
                    model.context_window = sm.context_window
                # 价格：一律取同步来源价（上游/官方参考兜底）；无价不覆盖（保持空/原值）
                if sm.input_price is not None:
                    model.input_price = sm.input_price
                    model.price_currency = sm.price_currency
                    model.price_source = sm.price_source
                if sm.output_price is not None:
                    model.output_price = sm.output_price
                    model.price_currency = sm.price_currency
                    model.price_source = sm.price_source
                if not model.price_source:
                    model.price_source = sm.price_source
                model.upstream_model = upstream
                model.synced_from = spec.key
                model.last_synced_at = now
                model.is_active = True
                result.updated += 1
            else:
                db.add(Model(
                    model=sm.id,
                    vendor=spec.key,
                    display_name=sm.display_name or sm.id,
                    owned_by=sm.owned_by or spec.key,
                    model_type=sm.model_type,
                    input_price=sm.input_price,
                    output_price=sm.output_price,
                    price_currency=sm.price_currency,
                    context_window=sm.context_window,
                    price_source=sm.price_source,
                    upstream_model=upstream,
                    synced_from=spec.key,
                    last_synced_at=now,
                    route_strategy="weighted_random",
                    is_active=True,
                ))
                result.added += 1
            result.model_ids.append(sm.id)

        # 2.5 keep_latest_only / series_latest：清理管理家族内未保留的模型（含旧版本残留）
        #     kimi/minimax 等非管理家族模型保留不动
        if spec.keep_latest_only or spec.series_latest:
            retained_ids = {sm.id for sm in synced}
            db_models = (await db.execute(
                select(Model).where(Model.vendor == spec.key)
            )).scalars().all()
            for m in db_models:
                if m.model in retained_ids:
                    continue
                if _ark_family(m.model) in _ARK_MANAGED_FAMILIES:
                    await db.delete(m)
                    result.removed += 1

        # 3. 更新 provider 同步状态
        provider.last_synced_at = now
        provider.last_sync_status = "success"
        provider.last_sync_error = None
        await db.commit()
        result.status = "success"
        logger.info(f"sync {spec.key}: +{result.added} ~{result.updated} models")
        # 模型清单变化 → 自动刷新 Codex 模型目录（Codex 启动时读取最新）
        from src.services.codex_catalog import maybe_sync_background
        maybe_sync_background()
    except Exception as e:
        await db.rollback()
        provider.last_sync_status = "error"
        provider.last_sync_error = str(e)[:500]
        await db.commit()
        result.status = "error"
        result.errors = [str(e)]
        logger.error(f"sync {spec.key} failed: {e}")
    return result


async def run_sync_background(provider_id: str) -> SyncResult:
    """后台同步：独立会话执行（避免与请求生命周期绑定）"""
    from src.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        provider = await db.get(Provider, provider_id)
        if not provider:
            return SyncResult(status="error", errors=["Provider not found"])
        spec = get_spec(provider.name)
        if not spec:
            return SyncResult(status="error", errors=[f"Unknown provider: {provider.name}"])
        return await sync_provider_models(db, provider, spec)


def spawn_sync_task(provider_id: str) -> None:
    """在当前事件循环中启动后台同步任务（创建后未引用的任务由事件循环持有）"""
    asyncio.create_task(run_sync_background(provider_id))
