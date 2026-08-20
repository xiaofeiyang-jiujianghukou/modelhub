"""
计费中间件与服务
实现：余额预检、Token 计费扣减、消费记录写入
按审核报告 M-2：扣费通过 PostgreSQL SELECT FOR UPDATE，Redis 仅作读缓存（MVP 用内存缓存）
"""

import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException
from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Balance, Model, RequestLog, Transaction
from src.config import settings

# ── 内存余额缓存（生产替换为 Redis）────────────────────────────────────────────

_balance_cache: dict[str, tuple[float, float]] = {}  # user_id → (amount_usd, expire_ts)
_BALANCE_CACHE_TTL = 30  # 秒


def _balance_cache_get(user_id: str) -> Optional[float]:
    entry = _balance_cache.get(user_id)
    if entry and entry[1] > time.time():
        return entry[0]
    return None


def _balance_cache_set(user_id: str, amount: float) -> None:
    _balance_cache[user_id] = (amount, time.time() + _BALANCE_CACHE_TTL)


def _balance_cache_invalidate(user_id: str) -> None:
    _balance_cache.pop(user_id, None)


# ── 定价计算 ───────────────────────────────────────────────────────────────────

def _to_usd(price: Optional[float], currency: Optional[str]) -> float:
    """原始币种价格 → USD（余额/流水统一 USD 记账）"""
    if price is None:
        return 0.0
    v = float(price)
    cur = (currency or "USD").upper()
    if cur == "CNY":
        return v / settings.usd_to_cny_rate
    return v


def calc_llm_cost(
    model: Model,
    prompt_tokens: int,
    completion_tokens: int,
) -> float:
    """LLM 按 token 计算费用（美元）"""
    input_price = _to_usd(model.input_price, model.price_currency)
    output_price = _to_usd(model.output_price, model.price_currency)
    cost = (prompt_tokens / 1_000_000) * input_price + (completion_tokens / 1_000_000) * output_price
    return round(cost, 8)


def calc_image_cost(model: Model, image_count: int) -> float:
    """图像按张计算费用（美元）"""
    unit = _to_usd(model.unit_price, model.price_currency)
    return round(unit * image_count, 8)


def calc_video_cost(model: Model, video_seconds: float) -> float:
    """视频按秒计算费用（美元）"""
    unit = _to_usd(model.unit_price, model.price_currency)
    return round(unit * video_seconds, 8)


# ── 计费服务 ───────────────────────────────────────────────────────────────────

class BillingService:
    """
    计费服务
    precheck_balance：请求前预检余额
    deduct：请求后原子扣减
    record_log：异步写入请求日志
    """

    @staticmethod
    async def precheck_balance(db: AsyncSession, user_id: str, min_balance: float = 0.001) -> float:
        """
        检查余额是否满足最低要求，先读缓存，不足则查 DB
        返回当前余额；余额不足抛 402
        """
        cached = _balance_cache_get(user_id)
        if cached is not None:
            if cached < min_balance:
                raise HTTPException(
                    status_code=402,
                    detail={"error": {"message": "Insufficient balance", "type": "billing_error", "code": "insufficient_balance"}},
                )
            return cached

        balance_row = await db.get(Balance, user_id)
        if not balance_row:
            raise HTTPException(
                status_code=402,
                detail={"error": {"message": "Balance record not found", "type": "billing_error", "code": "insufficient_balance"}},
            )
        amount = float(balance_row.amount_usd)
        _balance_cache_set(user_id, amount)

        if amount < min_balance:
            raise HTTPException(
                status_code=402,
                detail={"error": {"message": "Insufficient balance", "type": "billing_error", "code": "insufficient_balance"}},
            )
        return amount

    @staticmethod
    async def deduct(
        db: AsyncSession,
        user_id: str,
        cost_usd: float,
        description: str = "",
        request_log_id: Optional[str] = None,
    ) -> float:
        """
        原子扣减余额，返回扣减后余额；余额不足时抛 402。

        用条件 UPDATE 实现乐观锁：WHERE amount>=cost 保证只有余额充足时才扣，
        rowcount=0 表示余额不足或无记录。这避免 read-then-write 竞态：
        SQLite 无 SELECT FOR UPDATE，两并发请求读到同旧值会算出同结果 -> 少扣。
        """
        if cost_usd <= 0:
            return 0.0

        now = datetime.now(timezone.utc)
        # 原子扣减：单条 UPDATE 在 SQLite/PG 上都保证原子，WHERE 子句防超扣
        # synchronize_session=False：amount_usd 是 Numeric(Decimal)，ORM 的 Python 端
        # evaluator 算 Decimal-float 会抛 TypeError；交由 SQL 层处理（SQLite 隐式转换）
        stmt = (
            update(Balance)
            .where(Balance.user_id == user_id)
            .where(Balance.amount_usd >= cost_usd)
            .values(amount_usd=Balance.amount_usd - cost_usd, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        result = await db.execute(stmt)
        if result.rowcount == 0:
            # 失败路径才多查一次，区分"无记录"与"余额不足"给出准确消息
            existing = await db.get(Balance, user_id)
            if not existing:
                raise HTTPException(
                    status_code=402,
                    detail={"error": {"message": "Balance record not found", "type": "billing_error", "code": "insufficient_balance"}},
                )
            raise HTTPException(
                status_code=402,
                detail={"error": {"message": "Insufficient balance", "type": "billing_error", "code": "insufficient_balance"}},
            )

        # 查回扣减后余额写交易记录（expire 确保拿到 UPDATE 后的 DB 真值）
        balance_row = await db.get(Balance, user_id)
        await db.refresh(balance_row)
        new_balance = float(balance_row.amount_usd)

        txn = Transaction(
            user_id=user_id,
            type="usage",
            amount_usd=-cost_usd,
            balance_after=new_balance,
            description=description,
            request_log_id=request_log_id,
        )
        db.add(txn)
        await db.commit()

        _balance_cache_set(user_id, new_balance)
        logger.debug("deducted user={} cost={:.6f} new_balance={:.6f}", user_id, cost_usd, new_balance)
        return new_balance

    @staticmethod
    async def topup(
        db: AsyncSession,
        user_id: str,
        amount_usd: float,
        stripe_payment_id: Optional[str] = None,
    ) -> float:
        """充值余额（原子加款，防并发充值少充）"""
        # get-or-create 余额记录
        balance_row = await db.get(Balance, user_id)
        if not balance_row:
            balance_row = Balance(user_id=user_id, amount_usd=0.0)
            db.add(balance_row)
            await db.flush()

        # 原子加款：UPDATE amount=amount+topup，避免两请求读到同旧值少充
        now = datetime.now(timezone.utc)
        stmt = (
            update(Balance)
            .where(Balance.user_id == user_id)
            .values(amount_usd=Balance.amount_usd + amount_usd, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        await db.execute(stmt)
        await db.refresh(balance_row)
        new_balance = float(balance_row.amount_usd)

        txn = Transaction(
            user_id=user_id,
            type="topup",
            amount_usd=amount_usd,
            balance_after=new_balance,
            description=f"Top up ${amount_usd:.2f}",
            stripe_payment_id=stripe_payment_id,
        )
        db.add(txn)
        await db.commit()
        _balance_cache_set(user_id, new_balance)
        return new_balance

    @staticmethod
    async def record_log(
        db: AsyncSession,
        request_id: str,
        user_id: str,
        api_key_id: Optional[str],
        model: str,
        provider: Optional[str],
        request_type: str,
        status: str,
        status_code: Optional[int] = None,
        prompt_tokens: Optional[int] = None,
        completion_tokens: Optional[int] = None,
        total_tokens: Optional[int] = None,
        cache_hit_tokens: Optional[int] = None,
        cache_miss_tokens: Optional[int] = None,
        image_count: Optional[int] = None,
        video_seconds: Optional[float] = None,
        cost_usd: Optional[float] = None,
        latency_ms: Optional[int] = None,
        error_code: Optional[str] = None,
    ) -> RequestLog:
        """写入请求日志（异步，不阻塞响应）"""
        log = RequestLog(
            request_id=request_id,
            user_id=user_id,
            api_key_id=api_key_id,
            model=model,
            provider=provider,
            request_type=request_type,
            status=status,
            status_code=status_code,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cache_hit_tokens=cache_hit_tokens,
            cache_miss_tokens=cache_miss_tokens,
            image_count=image_count,
            video_seconds=video_seconds,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            error_code=error_code,
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)
        return log


billing_service = BillingService()
