"""
模型参考价 + 上下文窗口数据服务。

数据源：数据库表 model_references（dashboard 界面可管理），
首次启动用 config/model_reference.json 做种子导入（幂等，不覆盖界面已改的值）。

sync 流程：
- static 供应商（无 /models 端点）：模型清单 + 价格 + 上下文从本表查（vendor 归属）
- api 供应商：上游返回价格/上下文优先，缺失时用本表兜底
"""

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import ModelReference

_REFERENCE_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "model_reference.json"


def _read_seed_json() -> dict:
    if not _REFERENCE_PATH.exists():
        return {}
    return json.loads(_REFERENCE_PATH.read_text(encoding="utf-8"))


async def seed_from_json(db: AsyncSession) -> int:
    """从 JSON 种子导入（幂等：已存在的行跳过，不覆盖界面手工修改）"""
    data = _read_seed_json()
    count = 0

    # static 供应商的模型清单
    for vendor, models in data.get("static_models", {}).items():
        for m in models:
            count += await _upsert(db, m["id"], {
                "vendor": vendor,
                "upstream_model": m.get("upstream_model"),
                "display_name": m.get("display_name"),
                "input_price": m.get("input_price"),
                "output_price": m.get("output_price"),
                "price_currency": m.get("currency", "USD"),
                "context_window": m.get("context_window"),
                "price_source": m.get("price_source", "official"),
            })

    # api 供应商的官方核对兜底值
    for model_id, ref in data.get("reference", {}).items():
        count += await _upsert(db, model_id, {
            "input_price": ref.get("input_price"),
            "output_price": ref.get("output_price"),
            "price_currency": ref.get("currency", "USD"),
            "context_window": ref.get("context_window"),
            "price_source": ref.get("price_source", "official"),
        })

    return count


async def _upsert(db: AsyncSession, model_id: str, fields: dict) -> int:
    """存在则跳过（不覆盖），不存在则插入。返回新增条数"""
    existing = await db.get(ModelReference, model_id)
    if existing:
        return 0
    db.add(ModelReference(model_id=model_id, **fields))
    return 1


async def static_models_for(db: AsyncSession, vendor_key: str) -> list[ModelReference]:
    """static 供应商的内置模型清单（含价格/上下文）"""
    result = await db.execute(
        select(ModelReference).where(ModelReference.vendor == vendor_key).order_by(ModelReference.model_id)
    )
    return list(result.scalars().all())


async def reference_for(db: AsyncSession, model_id: str) -> ModelReference | None:
    """模型官方核对值（输入价/输出价/上下文窗口）"""
    return await db.get(ModelReference, model_id)
