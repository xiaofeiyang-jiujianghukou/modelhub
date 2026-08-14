"""
Codex 模型目录同步服务测试
"""

import json

import pytest

from src.db.models import Model, Provider
from src.services import codex_catalog
from src.services.crypto import encrypt_credentials


def _provider(name, has_key=True, sync="success", base_url="https://example.test"):
    return Provider(
        name=name, base_url=base_url, auth_type="bearer",
        credentials_enc=encrypt_credentials({"api_key": "sk-x"}) if has_key else "{}",
        timeout_ms=30000, is_active=True, last_sync_status=sync,
    )


@pytest.mark.asyncio
async def test_load_visible_models_rule(db_session):
    """可见性规则：只有 活跃+已配Key+同步成功 的供应商的模型才可见"""
    ok = _provider("openai")
    no_key = _provider("anthropic", has_key=False, sync="success")
    not_synced = _provider("deepseek", sync=None)
    db_session.add_all([ok, no_key, not_synced])
    await db_session.flush()

    # 三个模型分别挂不同供应商
    for mid, prov in [("m-ok", ok), ("m-nokey", no_key), ("m-nosync", not_synced)]:
        db_session.add(Model(model=mid, vendor=prov.name, display_name=mid, model_type="llm", is_active=True, upstream_model=mid))
    await db_session.commit()

    models = await codex_catalog.load_visible_models(db_session)
    ids = {m["id"] for m in models}
    assert ids == {"openai/m-ok"}
    assert "m-nokey" not in ids
    assert "m-nosync" not in ids


def test_render_catalog_merge():
    """合并：保留已有自定义字段、新增补模板、删除消失的"""
    gateway = [
        {"id": "keep-model", "display_name": "Keep", "context_window": 131072, "synced_from": "glm"},
        {"id": "deepseek-v4-pro", "display_name": "New", "context_window": None, "synced_from": "ark"},
    ]
    existing = {"models": [
        {"slug": "keep-model", "display_name": "【自定义】Keep", "description": "用户备注",
         "context_window": 8192, "max_context_window": 8192, "auto_compact_token_limit": 6144,
         "input_modalities": ["text"]},
        {"slug": "gone-model", "display_name": "已删除", "context_window": 131072},
    ]}

    catalog = codex_catalog.render_catalog(gateway, existing)
    slugs = {m["slug"] for m in catalog["models"]}
    assert slugs == {"keep-model", "deepseek-v4-pro"}

    keep = next(m for m in catalog["models"] if m["slug"] == "keep-model")
    assert keep["display_name"] == "【自定义】Keep"          # 自定义名称保留
    assert keep["description"] == "[智谱][keep-model]"       # 描述统一 [厂商][模型名]
    assert keep["context_window"] == 131072                  # 上下文以网关为准更新

    new = next(m for m in catalog["models"] if m["slug"] == "deepseek-v4-pro")
    assert new["display_name"] == "New"
    assert new["description"] == "[方舟][deepseek-v4-pro]"   # ark- 前缀剥离
    assert new["supports_parallel_tool_calls"] is True       # 模板字段补齐
    assert "supported_reasoning_levels" in new
    assert "model_messages" in new                           # 0.147 必填 instructions_template


@pytest.mark.asyncio
async def test_sync_codex_catalog_writes_file(db_session, tmp_path, monkeypatch):
    """同步服务写文件（原子写 + 合法 JSON）"""
    p = _provider("glm")
    db_session.add(p)
    await db_session.flush()
    db_session.add(Model(model="glm-4-flash", vendor=p.name, display_name="GLM-4 Flash",
                                model_type="llm", context_window=131072, is_active=True, upstream_model="glm-4-flash"))
    await db_session.commit()

    out = tmp_path / "model-catalog.json"
    stats = await codex_catalog.sync_codex_catalog(db_session, path=out)
    assert stats["total"] == 1
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["models"][0]["slug"] == "glm/glm-4-flash"
    assert data["models"][0]["context_window"] == 131072


@pytest.mark.asyncio
async def test_sync_skipped_without_path(db_session, monkeypatch):
    """未配置路径时跳过"""
    from src.config import settings
    monkeypatch.setattr(settings, "codex_catalog_path", None)
    stats = await codex_catalog.sync_codex_catalog(db_session)
    assert stats.get("skipped") is True
