"""
视频生成接口（异步任务模式，PRD 3.6）
- POST /v1/videos/generations   发起视频生成任务
- GET  /v1/videos/tasks/{task_id} 查询任务状态

MVP 说明：视频生成依赖上游（Veo/Seedance）的真实异步任务接口。
当前实现为任务注册 + 状态查询框架，上游接入后即可工作。
"""

import time
import uuid
from typing import Optional

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db
from src.db.models import User, Model, VideoTask
from src.middleware.auth import get_api_key_user
from src.middleware.billing import billing_service, calc_video_cost
from src.services.model_key import format_model_key, parse_model_key

router = APIRouter(tags=["Videos"])


# ── 请求/响应模型 ──────────────────────────────────────────────────────────────

class VideoRequest(BaseModel):
    model: str
    prompt: str
    duration: Optional[int] = Field(default=5, ge=1, le=30, description="视频秒数 1-30")
    resolution: Optional[str] = Field(default="720p", description="分辨率: 480p/720p/1080p")


class VideoTaskResponse(BaseModel):
    task_id: str
    status: str
    created: int
    estimated_seconds: Optional[int] = None


# ── 接口 ─────────────────────────────────────────────────────────────────────

@router.post("/videos/generations")
async def create_video_task(
    request: VideoRequest,
    http_req: Request,
    db: AsyncSession = Depends(get_db),
):
    """发起视频生成任务（异步），返回 task_id 供轮询"""
    user: User = getattr(http_req.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail={"error": {"message": "Invalid authentication", "type": "authentication_error", "code": "invalid_api_key"}})

    # 余额预检
    try:
        await billing_service.precheck_balance(db, user.id)
    except HTTPException as e:
        return JSONResponse(status_code=e.status_code, content=e.detail)

    # 模型校验
    mid, vendor = parse_model_key(request.model)
    if vendor:
        model = await Model.get_by_model_and_vendor(db, mid, vendor)
    else:
        model = await Model.get_by_alias(db, mid)
    if not model or not model.is_active or model.model_type != "video":
        raise HTTPException(
            status_code=400,
            detail={"error": {"message": f"Video model '{request.model}' does not exist or is not available", "type": "invalid_request_error", "code": "invalid_model"}},
        )

    # 创建任务
    task_id = f"vtask-{uuid.uuid4().hex[:20]}"
    task = VideoTask(
        id=task_id,
        user_id=user.id,
        model=format_model_key(model.model, model.vendor),
        prompt=request.prompt,
        status="pending",
        duration_seconds=request.duration,
    )
    db.add(task)
    await db.commit()

    return {
        "task_id": task_id,
        "status": "pending",
        "created": int(time.time()),
        "estimated_seconds": max(30, request.duration * 10),
    }


@router.get("/videos/tasks/{task_id}")
async def get_video_task(
    task_id: str,
    http_req: Request,
    db: AsyncSession = Depends(get_db),
):
    """查询视频任务状态"""
    user: User = getattr(http_req.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail={"error": {"message": "Invalid authentication", "type": "authentication_error", "code": "invalid_api_key"}})

    task = await db.get(VideoTask, task_id)
    if not task or task.user_id != user.id:
        raise HTTPException(
            status_code=404,
            detail={"error": {"message": "Task not found", "type": "invalid_request_error", "code": "not_found"}},
        )

    result = {
        "task_id": task.id,
        "status": task.status,
        "created": int(task.created_at.timestamp()),
        "completed": int(task.completed_at.timestamp()) if task.completed_at else None,
    }
    if task.status == "succeeded":
        result["result"] = {
            "url": task.result_url,
            "duration": task.duration_seconds,
            "resolution": "720p",
        }
        result["usage"] = {"billed_seconds": task.billed_seconds}
    if task.status == "failed":
        result["error"] = task.error_message
    return result
