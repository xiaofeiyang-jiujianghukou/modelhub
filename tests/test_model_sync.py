"""
模型同步服务测试（mock HTTP 层，无真实网络）
"""

import pytest

from src.db.models import Model, Provider
from src.services import model_sync
from src.services.model_sync import sync_provider_models
from src.providers.provider_registry import get_spec

from sqlalchemy import select


async def _make_provider(db_session, name: str = "deepseek", creds: str = '{"api_key":"sk-test"}') -> Provider:
    spec = get_spec(name)
    provider = Provider(
        name=name,
        base_url=spec.default_base_url,
        auth_type=spec.auth_type,
        credentials_enc=creds,
        timeout_ms=30000,
    )
    db_session.add(provider)
    await db_session.commit()
    return provider


@pytest.mark.asyncio
async def test_openai_parser_and_exclude(db_session, monkeypatch):
    """标准 OpenAI 结构 + 非 chat 模型过滤（openai 供应商）"""
    async def fake_get(url, headers, timeout, params=None):
        assert "/models" in url
        assert headers["Authorization"] == "Bearer sk-test"
        return {"object": "list", "data": [
            {"id": "gpt-5", "object": "model", "owned_by": "system"},
            {"id": "gpt-5-mini", "owned_by": "system"},
            {"id": "text-embedding-3-large", "owned_by": "openai"},
            {"id": "gpt-image-1", "owned_by": "system"},
            {"id": "whisper-1", "owned_by": "openai"},
        ]}
    monkeypatch.setattr(model_sync, "_http_get", fake_get)

    provider = await _make_provider(db_session, "openai")
    spec = get_spec("openai")
    result = await sync_provider_models(db_session, provider, spec)

    assert result.status == "success"
    assert result.added == 2
    ids = {m for m in result.model_ids}
    assert ids == {"gpt-5", "gpt-5-mini"}

    # 入库校验
    gpt5 = (await db_session.execute(select(Model).where(Model.model == "gpt-5").limit(1))).scalars().first()
    assert gpt5.price_source == "default"
    assert gpt5.synced_from == "openai"
    assert (await db_session.execute(select(Model).where(Model.model == "text-embedding-3-large").limit(1))).scalars().first() is None


@pytest.mark.asyncio
async def test_ark_parser_filter_and_typing(db_session, monkeypatch):
    """方舟 parser：过滤退役 + 排除不支持 + 家族去重（doubao-seed 只留最新）+ model_type 分类"""
    async def fake_get(url, headers, timeout, params=None):
        assert "/models" in url
        return {"object": "list", "data": [
            # 可用 LLM（方舟 Agent Plan 已接入 GLM/deepseek 等）
            {"id": "doubao-seed-2-1-pro-260628", "status": None, "created": 100},
            {"id": "doubao-seed-2-1-turbo-260628", "status": None, "created": 200},  # doubao-seed 家族最新
            {"id": "deepseek-v4-pro-ga-260813", "status": None, "created": 300},
            {"id": "glm-5-2-260617", "status": None, "created": 400},
            # 图像 / 视频
            {"id": "doubao-seedream-4-0-250828", "status": None, "created": 500},
            {"id": "doubao-seedance-2-5-260628", "status": None, "created": 600},
            # 应排除：embedding / 3D / router
            {"id": "doubao-embedding-vision-250615", "status": None, "created": 700},
            {"id": "hyper3d-gen2-260112", "status": None, "created": 800},
            {"id": "doubao-smart-router-250928", "status": None, "created": 900},
            # 应跳过：退役模型
            {"id": "doubao-pro-32k-241215", "status": "Shutdown", "created": 1000},
            {"id": "doubao-1-5-pro-32k-250115", "status": "Retiring", "created": 1100},
        ]}
    monkeypatch.setattr(model_sync, "_http_get", fake_get)

    provider = await _make_provider(db_session, "ark")
    spec = get_spec("ark")
    result = await sync_provider_models(db_session, provider, spec)

    assert result.status == "success"
    assert result.added == 5, result.model_ids  # 5 个家族代表
    assert set(result.model_ids) == {
        "doubao-seed-2-1-turbo", "deepseek-v4-pro", "glm-5-2", "doubao-seedream-4-0", "doubao-seedance-2-5",
    }
    # 排除/退役未入库
    assert "doubao-embedding-vision-250615" not in result.model_ids
    assert "doubao-pro-32k-241215" not in result.model_ids

    # model_type 分类（clean id）
    dream = (await db_session.execute(select(Model).where(Model.model == "doubao-seedream-4-0").limit(1))).scalars().first()
    dance = (await db_session.execute(select(Model).where(Model.model == "doubao-seedance-2-5").limit(1))).scalars().first()
    llm = (await db_session.execute(select(Model).where(Model.model == "glm-5-2").limit(1))).scalars().first()
    assert dream is not None and dream.model_type == "image"
    assert dance is not None and dance.model_type == "video"
    assert llm is not None and llm.model_type == "llm"


@pytest.mark.asyncio
async def test_ark_keep_latest_only_removes_old(db_session, monkeypatch):
    """方舟 keep_latest_only：每家族只留最新代表 + 去日期 + 排除非套餐(qwen) + 移除旧版本"""
    # 预置旧版（deepseek-v4-pro-260425 同家族旧版 + qwen3-32b 被 exclude 后的残留），都应被清理
    db_session.add(Model(
        model="deepseek-v4-pro-260425", vendor="ark", display_name="old",
        owned_by="ark", model_type="llm", price_source="default",
        synced_from="ark", is_active=True,
    ))
    db_session.add(Model(
        model="qwen3-32b", vendor="ark", display_name="qwen-residual",
        owned_by="ark", model_type="llm", price_source="default",
        synced_from="ark", is_active=True,
    ))
    await db_session.commit()

    async def fake_get(url, headers, timeout, params=None):
        return {"object": "list", "data": [
            {"id": "deepseek-v4-pro-260425", "status": None, "created": 1778837387},
            {"id": "deepseek-v4-pro-ga-260813", "status": None, "created": 1786682407},  # deepseek pro 最新
            {"id": "deepseek-v4-flash-ga-260731", "status": None, "created": 1785748879},  # deepseek flash 最新
            {"id": "doubao-seed-2-1-turbo-260628", "status": None, "created": 1781612321},  # 套餐内
            {"id": "doubao-seed-2-1-pro-260628", "status": None, "created": 1782000000},  # 旗舰，_ARK_KEEP_BASES 外丢弃
            {"id": "doubao-seedream-4-0-250828", "status": None, "created": 1757244120},
            {"id": "doubao-seedream-4-0-20260415", "status": None, "created": 1776349840},  # seedream 最新
            {"id": "qwen3-32b-20250429", "status": None, "created": 1780000000},  # 非套餐，exclude_patterns 过滤
            {"id": "qwen3-8b-20250429", "status": None, "created": 1770000000},
        ]}
    monkeypatch.setattr(model_sync, "_http_get", fake_get)

    provider = await _make_provider(db_session, "ark")
    spec = get_spec("ark")
    result = await sync_provider_models(db_session, provider, spec)

    assert result.status == "success"
    assert result.removed == 2  # deepseek-v4-pro-260425 旧版 + qwen3-32b 残留都被删
    assert set(result.model_ids) == {
        "deepseek-v4-pro", "deepseek-v4-flash", "doubao-seed-2-1-turbo", "doubao-seedream-4-0",
    }

    # 去日期：网关 id 是干净名，upstream 保留带日期原始 id
    pro = (await db_session.execute(select(Model).where(Model.model == "deepseek-v4-pro", Model.vendor == "ark").limit(1))).scalars().first()
    assert pro is not None
    assert pro.upstream_model == "deepseek-v4-pro-ga-260813"
    flash = (await db_session.execute(select(Model).where(Model.model == "deepseek-v4-flash", Model.vendor == "ark").limit(1))).scalars().first()
    assert flash is not None
    assert flash.upstream_model == "deepseek-v4-flash-ga-260731"
    # 旧版被删
    old = (await db_session.execute(select(Model).where(Model.model == "deepseek-v4-pro-260425", Model.vendor == "ark").limit(1))).scalars().first()
    assert old is None
    # doubao-seed 只留套餐内 2-1-turbo，旗舰 2-1-pro 丢弃
    turbo = (await db_session.execute(select(Model).where(Model.model == "doubao-seed-2-1-turbo", Model.vendor == "ark").limit(1))).scalars().first()
    pro2 = (await db_session.execute(select(Model).where(Model.model == "doubao-seed-2-1-pro", Model.vendor == "ark").limit(1))).scalars().first()
    assert turbo is not None
    assert pro2 is None
    # qwen 全系被 exclude 过滤
    qwen = (await db_session.execute(select(Model).where(Model.model == "qwen3-32b", Model.vendor == "ark").limit(1))).scalars().first()
    assert qwen is None


@pytest.mark.asyncio
async def test_grok_official_pricing(db_session, monkeypatch):
    """xAI 响应自带官方价格 → price_source=official"""
    async def fake_get(url, headers, timeout, params=None):
        return {"object": "list", "data": [
            {"id": "grok-4.6", "object": "model", "owned_by": "xai",
             "context_length": 131072, "prompt_text_token_price": 12500,
             "completion_text_token_price": 25000},
            {"id": "grok-4.5", "owned_by": "xai", "prompt_text_token_price": 5000,
             "completion_text_token_price": 15000},
            {"id": "grok-imagine-image", "owned_by": "xai", "image_price": 100},
            {"id": "grok-voice-1", "owned_by": "xai"},
        ]}
    monkeypatch.setattr(model_sync, "_http_get", fake_get)

    provider = await _make_provider(db_session, "grok")
    result = await sync_provider_models(db_session, provider, get_spec("grok"))

    assert result.added == 2
    m = (await db_session.execute(select(Model).where(Model.model == "grok-4.6").limit(1))).scalars().first()
    assert m.input_price == 12500
    assert m.output_price == 25000
    assert m.price_source == "official"
    assert m.context_window == 131072
    assert (await db_session.execute(select(Model).where(Model.model == "grok-imagine-image").limit(1))).scalars().first() is None
    assert (await db_session.execute(select(Model).where(Model.model == "grok-voice-1").limit(1))).scalars().first() is None


@pytest.mark.asyncio
async def test_anthropic_pagination(db_session, monkeypatch):
    """Anthropic 游标分页（两页）"""
    calls = {"n": 0}

    async def fake_get(url, headers, timeout, params=None):
        calls["n"] += 1
        assert headers["x-api-key"] == "sk-test"
        assert "anthropic-version" in headers
        if params and params.get("after_id"):
            return {"data": [
                {"type": "model", "id": "claude-opus-5", "display_name": "Claude Opus 5",
                 "max_input_tokens": 200000},
            ], "has_more": False, "last_id": "claude-opus-5"}
        return {"data": [
            {"type": "model", "id": "claude-sonnet-5", "display_name": "Claude Sonnet 5",
             "max_input_tokens": 200000},
        ], "has_more": True, "last_id": "claude-sonnet-5"}
    monkeypatch.setattr(model_sync, "_http_get", fake_get)

    provider = await _make_provider(db_session, "anthropic")
    result = await sync_provider_models(db_session, provider, get_spec("anthropic"))

    assert calls["n"] == 2
    assert result.added == 2
    m = (await db_session.execute(select(Model).where(Model.model == "claude-sonnet-5").limit(1))).scalars().first()
    assert m.display_name == "Claude Sonnet 5"
    assert m.context_window == 200000


@pytest.mark.asyncio
async def test_gemini_native_parsing(db_session, monkeypatch):
    """Gemini 原生结构：models/ 前缀去除 + generateContent 过滤 + 分页"""
    async def fake_get(url, headers, timeout, params=None):
        assert params and params.get("key") == "sk-test"
        if params and params.get("pageToken"):
            return {"models": [
                {"name": "models/gemini-2.5-pro", "displayName": "Gemini 2.5 Pro",
                 "inputTokenLimit": 1048576, "supportedGenerationMethods": ["generateContent"]},
            ]}
        return {"models": [
            {"name": "models/gemini-2.5-flash", "displayName": "Gemini 2.5 Flash",
             "inputTokenLimit": 1048576, "supportedGenerationMethods": ["generateContent", "countTokens"]},
            {"name": "models/text-embedding-004", "displayName": "Embedding",
             "supportedGenerationMethods": ["embedContent"]},
            {"name": "models/veo-3", "displayName": "Veo", "supportedGenerationMethods": ["generateVideos"]},
        ], "nextPageToken": "page-2"}
    monkeypatch.setattr(model_sync, "_http_get", fake_get)

    provider = await _make_provider(db_session, "gemini")
    result = await sync_provider_models(db_session, provider, get_spec("gemini"))

    assert result.added == 2
    assert result.model_ids == ["gemini-2.5-flash", "gemini-2.5-pro"]
    m = (await db_session.execute(select(Model).where(Model.model == "gemini-2.5-flash").limit(1))).scalars().first()
    assert m.display_name == "Gemini 2.5 Flash"
    assert m.context_window == 1048576


@pytest.mark.asyncio
async def test_gemini_fallback_compat(db_session, monkeypatch):
    """Gemini 原生失败 → 降级 OpenAI 兼容端点"""
    calls = []

    async def fake_get(url, headers, timeout, params=None):
        calls.append(url)
        if "v1beta/models" in url and "openai" not in url:
            raise ValueError("上游请求失败 HTTP 404: not found")
        return {"object": "list", "data": [{"id": "gemini-2.5-flash", "owned_by": "google"}]}
    monkeypatch.setattr(model_sync, "_http_get", fake_get)

    provider = await _make_provider(db_session, "gemini")
    result = await sync_provider_models(db_session, provider, get_spec("gemini"))

    assert result.status == "success"
    assert any("v1beta/openai/models" in u for u in calls)
    assert (await db_session.execute(select(Model).where(Model.model == "gemini-2.5-flash").limit(1))).scalars().first() is not None


@pytest.mark.asyncio
async def test_static_sync_and_idempotent(db_session):
    """静态清单同步 + 幂等（二次同步不新增）"""
    provider = await _make_provider(db_session, "glm")
    spec = get_spec("glm")
    assert spec.model_source == "static"

    r1 = await sync_provider_models(db_session, provider, spec)
    assert r1.status == "success"
    assert r1.added == 3
    assert set(r1.model_ids) == {"glm-4-flash", "glm-5.2", "glm-5.3"}

    # 幂等：再跑一次全部 updated
    r2 = await sync_provider_models(db_session, provider, spec)
    assert r2.added == 0
    assert r2.updated == 3

    # 通道唯一
    channels = (await db_session.execute(select(Model).where(Model.vendor == provider.name))).scalars().all()
    assert len(channels) == 3

    # 静态清单价格标注（glm-4-flash 官方免费）
    m = (await db_session.execute(select(Model).where(Model.model == "glm-4-flash").limit(1))).scalars().first()
    assert m.price_source == "official"
    assert float(m.input_price) == 0.0


@pytest.mark.asyncio
async def test_no_api_key_error(db_session):
    """无 Key 时拉取失败并记录错误状态"""
    provider = await _make_provider(db_session, "deepseek", creds='{}')
    result = await sync_provider_models(db_session, provider, get_spec("deepseek"))

    assert result.status == "error"
    assert result.errors
    assert provider.last_sync_status == "error"
    assert provider.last_sync_error


@pytest.mark.asyncio
async def test_upstream_error_marks_provider(db_session, monkeypatch):
    """上游异常 → SyncResult error + provider.last_sync_error 记录"""
    async def fake_get(url, headers, timeout, params=None):
        raise ValueError("API Key 无效或无权访问（HTTP 401）")
    monkeypatch.setattr(model_sync, "_http_get", fake_get)

    provider = await _make_provider(db_session, "deepseek")
    result = await sync_provider_models(db_session, provider, get_spec("deepseek"))

    assert result.status == "error"
    assert "401" in result.errors[0]
    assert provider.last_sync_status == "error"
    assert "401" in provider.last_sync_error
