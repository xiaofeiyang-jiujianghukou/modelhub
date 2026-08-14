"""
GET /v1/dashboard/balance 余额查询接口
返回当前用户账户余额（JWT 认证，与 dashboard 其他接口一致）
"""

from datetime import datetime, timezone
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from src.database import get_db
from src.db.models import User, Balance
from src.middleware.auth import get_current_user_jwt

router = APIRouter(tags=["Dashboard"])


class BalanceResponse(BaseModel):
    balance_usd: float
    currency: str = "USD"
    updated_at: int


@router.get("/dashboard/balance")
async def get_balance(
    current_user: dict = Depends(get_current_user_jwt),
    db: AsyncSession = Depends(get_db),
) -> BalanceResponse:
    """
    查询当前账户余额

    使用 JWT 认证（登录后获取），返回关联用户的余额信息
    """
    user_id = current_user["sub"]

    # 查询余额
    result = await db.execute(
        select(Balance).where(Balance.user_id == user_id)
    )
    balance = result.scalar_one_or_none()

    if balance is None:
        # 创建初始余额记录
        balance = Balance(user_id=user_id, amount_usd=0.0)
        db.add(balance)
        await db.commit()
        await db.refresh(balance)

    return BalanceResponse(
        balance_usd=float(balance.amount_usd),
        currency="USD",
        updated_at=int(balance.updated_at.timestamp()),
    )


@router.get("/dashboard/me")
async def get_me(current_user: dict = Depends(get_current_user_jwt)):
    """当前登录用户信息（前端权限门控：供应商管理 tab）"""
    return {
        "email": current_user.get("email", ""),
        "is_admin": bool(current_user.get("is_admin", False)),
    }
