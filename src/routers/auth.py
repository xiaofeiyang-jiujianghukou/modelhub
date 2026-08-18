"""
认证与 API Key 管理接口
- POST /v1/auth/register   注册
- POST /v1/auth/login      登录（返回 JWT）
- POST /v1/auth/logout     登出
- GET/POST /v1/dashboard/keys         列表/创建 API Key
- DELETE /v1/dashboard/keys/{key_id}  撤销 API Key
- GET  /v1/dashboard/logs  请求日志（PRD F-070）
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db
from src.db.models import ApiKey, RequestLog, User, to_utc_timestamp
from src.middleware.auth import auth_service, get_current_user_jwt, get_api_key_user

router = APIRouter(tags=["Auth"])


# ── 请求/响应模型 ──────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: str = Field(..., description="邮箱")
    password: str = Field(..., description="密码（业务层校验至少8位）")
    display_name: Optional[str] = None


class LoginRequest(BaseModel):
    email: str
    password: str


class CreateKeyRequest(BaseModel):
    name: str = Field(..., description="Key 名称，如 'prod-server'")


# ── 注册 / 登录 / 登出 ─────────────────────────────────────────────────────────

@router.post("/auth/register")
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """注册新用户，自动创建余额记录"""
    user = await auth_service.register(db, req.email, req.password, req.display_name)
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "message": "Registration successful",
    }


@router.post("/auth/login")
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    """登录，返回 JWT Token（用于 dashboard 接口）"""
    token = await auth_service.login(db, req.email, req.password)
    return {"access_token": token, "token_type": "bearer"}


@router.post("/auth/logout")
async def logout(request: Request, token: str = Depends(get_current_user_jwt)):
    """登出，将 token 加入黑名单"""
    auth_service.logout(request.headers.get("Authorization", "").replace("Bearer ", ""))
    return {"message": "Logged out"}


# ── API Key 管理（JWT 认证）───────────────────────────────────────────────────

@router.get("/dashboard/keys")
async def list_keys(
    current_user: dict = Depends(get_current_user_jwt),
    db: AsyncSession = Depends(get_db),
):
    """列出当前用户所有活跃 API Key（脱敏）"""
    keys = await auth_service.list_api_keys(db, current_user["sub"])
    return {
        "object": "list",
        "data": [
            {
                "id": k.id,
                "name": k.name,
                "key_prefix": f"{k.key_prefix}...",
                "is_active": k.is_active,
                "last_used_at": to_utc_timestamp(k.last_used_at),
                "created_at": to_utc_timestamp(k.created_at),
            }
            for k in keys
        ],
    }


@router.post("/dashboard/keys")
async def create_key(
    req: CreateKeyRequest,
    current_user: dict = Depends(get_current_user_jwt),
    db: AsyncSession = Depends(get_db),
):
    """创建 API Key（明文仅返回一次）"""
    api_key, raw_key = await auth_service.create_api_key(db, current_user["sub"], req.name)
    return {
        "id": api_key.id,
        "name": api_key.name,
        "key": raw_key,  # 仅此一次显示
        "message": "Save this key now - it will not be shown again",
    }


@router.delete("/dashboard/keys/{key_id}")
async def revoke_key(
    key_id: str,
    current_user: dict = Depends(get_current_user_jwt),
    db: AsyncSession = Depends(get_db),
):
    """撤销 API Key（立即生效）"""
    await auth_service.revoke_api_key(db, current_user["sub"], key_id)
    return {"message": "API key revoked"}


# ── 请求日志查询（JWT 认证，PRD F-070）────────────────────────────────────────

@router.get("/dashboard/logs")
async def list_logs(
    limit: int = 20,
    offset: int = 0,
    current_user: dict = Depends(get_current_user_jwt),
    db: AsyncSession = Depends(get_db),
):
    """查询当前用户的请求日志（按时间倒序）"""
    result = await db.execute(
        select(RequestLog)
        .where(RequestLog.user_id == current_user["sub"])
        .order_by(RequestLog.created_at.desc())
        .limit(min(limit, 100))
        .offset(offset)
    )
    logs = result.scalars().all()
    return {
        "object": "list",
        "data": [
            {
                "request_id": l.request_id,
                "model": l.model,
                "provider": l.provider,
                "request_type": l.request_type,
                "status": l.status,
                "status_code": l.status_code,
                "prompt_tokens": l.prompt_tokens,
                "completion_tokens": l.completion_tokens,
                "cache_hit_tokens": l.cache_hit_tokens,
                "cache_miss_tokens": l.cache_miss_tokens,
                "total_tokens": l.total_tokens,
                "cost_usd": float(l.cost_usd) if l.cost_usd else None,
                "latency_ms": l.latency_ms,
                "error_code": l.error_code,
                # SQLite 的 DateTime(timezone=True) 读出是 naive，存的是 UTC；
                # 必须补上 UTC 时区再 timestamp()，否则被误当本地时区（+8）解释，少 8 小时
                "created_at": to_utc_timestamp(l.created_at),
            }
            for l in logs
        ],
        "total": len(logs),
    }
