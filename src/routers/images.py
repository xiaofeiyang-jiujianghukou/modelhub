"""
POST /v1/images/generations 图像生成接口
兼容 OpenAI Images API
"""

import json
import time
import uuid
from typing import Optional

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db
from src.db.models import User, ApiKey, Model
from src.middleware.billing import billing_service, calc_image_cost
from src.services.router import router_service
from src.services.model_key import format_model_key, parse_model_key

router = APIRouter(tags=["Images"])


# ── 请求模型 ────────────────────────────────────────────────────────────────

class ImageRequest(BaseModel):
    model: str
    prompt: str
    n: Optional[int] = Field(default=1, ge=1, le=4)
    size: Optional[str] = Field(default="1024x1024")
    quality: Optional[str] = Field(default="standard")
    response_format: Optional[str] = Field(default="url")


# ── 响应模型 ────────────────────────────────────────────────────────────────

class ImageData(BaseModel):
    url: Optional[str] = None
    b64_json: Optional[str] = None
    revised_prompt: Optional[str] = None


class ImageResponse(BaseModel):
    created: int
    data: list[ImageData]


@router.post("/images/generations")
async def create_image(
    request: ImageRequest,
    http_req: Request,
    db: AsyncSession = Depends(get_db),
) -> ImageResponse:
    """
    图像生成接口，兼容 OpenAI Images API

    支持:
    - 多张生成 (n: 1-4)
    - 多种尺寸
    - 质量 (standard | hd)
    - 返回格式 (url | b64_json)
    """
    # 从 request.state 获取认证信息
    user: User = getattr(http_req.state, "user", None)
    api_key: ApiKey = getattr(http_req.state, "api_key", None)

    if not user or not api_key:
        raise HTTPException(status_code=401, detail="Invalid authentication")

    # 余额预检（不足返回 402）
    try:
        await billing_service.precheck_balance(db, user.id)
    except HTTPException as e:
        return JSONResponse(status_code=e.status_code, content=e.detail)

    # 查询模型信息
    mid, vendor = parse_model_key(request.model)
    if vendor:
        model = await Model.get_by_model_and_vendor(db, mid, vendor)
    else:
        model = await Model.get_by_alias(db, mid)
    if not model or not model.is_active or model.model_type != "image":
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "message": f"Image model '{request.model}' does not exist or is not available",
                    "type": "invalid_request_error",
                    "code": "invalid_model",
                }
            },
        )

    # 生成请求 ID
    req_id = f"img-{uuid.uuid4().hex[:20]}"

    # 构建 payload
    payload = request.model_dump()

    # 记录开始时间
    start_time = time.time()

    # 通过路由引擎转发（含故障转移）
    result, channel, provider, error_info = await router_service.route_image(db, format_model_key(model.model, model.vendor), payload)

    if result is None:
        # 透传上游真实错误
        if error_info:
            err_status = error_info.get("status_code", 502)
            if err_status >= 500:
                err_status = 502
            raise HTTPException(
                status_code=err_status,
                detail={
                    "error": {
                        "message": f"[{error_info.get('provider', 'upstream')}] {error_info.get('message', 'upstream error')}",
                        "type": "upstream_error",
                        "code": "upstream_error",
                    }
                },
            )
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "message": "No available provider for image model",
                    "type": "api_error",
                    "code": "no_available_provider",
                }
            },
        )

    latency_ms = int((time.time() - start_time) * 1000)
    provider_name = provider.name if provider else "unknown"

    # 按张计费并扣费
    image_count = request.n or 1
    cost = calc_image_cost(model, image_count)

    if cost > 0:
        try:
            await billing_service.deduct(
                db, user.id, cost,
                description=f"image {request.model} x{image_count}",
                request_log_id=req_id,
            )
        except HTTPException as e:
            logger.warning("image deduct failed user={} cost={}", user.id, cost)

    # 写入请求日志
    await billing_service.record_log(
        db,
        request_id=req_id,
        user_id=user.id,
        api_key_id=api_key.id,
        model=request.model,
        provider=provider_name,
        request_type="image",
        status="success",
        status_code=200,
        image_count=image_count,
        cost_usd=cost,
        latency_ms=latency_ms,
    )

    # 附加路由元数据
    response = JSONResponse(content=result)
    response.headers["X-Gateway-Model"] = request.model
    response.headers["X-Gateway-Provider"] = provider_name
    response.headers["X-Gateway-Latency"] = f"{latency_ms}ms"
    response.headers["X-Gateway-Request-ID"] = req_id
    return response
