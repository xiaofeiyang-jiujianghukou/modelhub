"""
限流中间件
使用内存令牌桶算法实现 per-user 和 per-key 限流
"""

import time
import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from src.config import settings


@dataclass
class TokenBucket:
    """令牌桶"""
    capacity: int          # 桶容量
    refill_rate: int       # 每秒补充 token 数
    tokens: float = field(init=False)
    last_refill: float = field(default_factory=time.time)

    def __post_init__(self):
        self.tokens = float(self.capacity)

    def _refill(self) -> None:
        """补充 token"""
        now = time.time()
        elapsed = now - self.last_refill
        self.tokens = min(
            self.capacity,
            self.tokens + elapsed * self.refill_rate
        )
        self.last_refill = now

    def consume(self, tokens: int = 1) -> bool:
        """消费 token，返回是否成功"""
        self._refill()
        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False

    def get_retry_after(self) -> int:
        """计算还需多少秒才能获得 1 个 token"""
        self._refill()
        if self.tokens >= 1:
            return 0
        return int((1 - self.tokens) / self.refill_rate) + 1


class RateLimiter:
    """限流器管理类"""

    def __init__(self):
        # user_id -> TokenBucket
        self._users: defaultdict[str, TokenBucket] = defaultdict(
            lambda: TokenBucket(
                capacity=settings.rate_limit_rpm,
                refill_rate=settings.rate_limit_rpm / 60.0,
            )
        )
        # api_key_prefix -> TokenBucket
        self._keys: defaultdict[str, TokenBucket] = defaultdict(
            lambda: TokenBucket(
                capacity=settings.rate_limit_rpm,
                refill_rate=settings.rate_limit_rpm / 60.0,
            )
        )
        # 全局限流
        self._global = TokenBucket(
            capacity=settings.rate_limit_global_rpm,
            refill_rate=settings.rate_limit_global_rpm / 60.0,
        )
        self._lock = asyncio.Lock()

    async def check_global(self) -> tuple[bool, int]:
        """检查全局限流"""
        async with self._lock:
            success = self._global.consume()
            retry_after = self._global.get_retry_after()
            return success, retry_after

    async def check_user(self, user_id: str) -> tuple[bool, int]:
        """检查用户限流"""
        async with self._lock:
            bucket = self._users[user_id]
            success = bucket.consume()
            retry_after = bucket.get_retry_after()
            return success, retry_after

    async def check_key(self, key_prefix: str) -> tuple[bool, int]:
        """检查 API Key 限流"""
        async with self._lock:
            bucket = self._keys[key_prefix]
            success = bucket.consume()
            retry_after = bucket.get_retry_after()
            return success, retry_after


# 全局单例
rate_limiter = RateLimiter()


class RateLimitMiddleware(BaseHTTPMiddleware):
    """限流中间件"""

    async def dispatch(self, request: Request, call_next):
        # 跳过非 API 路径和健康检查
        if not request.url.path.startswith("/v1") or request.url.path == "/health":
            return await call_next(request)

        # 全局限流
        ok, retry_after = await rate_limiter.check_global()
        if not ok:
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "message": "Rate limit exceeded (global)",
                        "type": "rate_limit_error",
                        "code": "rate_limit_exceeded",
                    }
                },
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(settings.rate_limit_global_rpm),
                },
            )

        # 用户/API Key 限流
        # 从 request.state 中获取认证信息（由 auth 中间件设置）
        user_id = getattr(request.state, "user_id", None)
        key_prefix = getattr(request.state, "key_prefix", None)

        if key_prefix:
            ok, retry_after = await rate_limiter.check_key(key_prefix)
            if not ok:
                return JSONResponse(
                    status_code=429,
                    content={
                        "error": {
                            "message": "Rate limit exceeded (per API key)",
                            "type": "rate_limit_error",
                            "code": "rate_limit_exceeded",
                        }
                    },
                    headers={
                        "Retry-After": str(retry_after),
                        "X-RateLimit-Limit": str(settings.rate_limit_rpm),
                        "X-RateLimit-Remaining": "0",
                    },
                )
        elif user_id:
            ok, retry_after = await rate_limiter.check_user(user_id)
            if not ok:
                return JSONResponse(
                    status_code=429,
                    content={
                        "error": {
                            "message": "Rate limit exceeded (per user)",
                            "type": "rate_limit_error",
                            "code": "rate_limit_exceeded",
                        }
                    },
                    headers={
                        "Retry-After": str(retry_after),
                        "X-RateLimit-Limit": str(settings.rate_limit_rpm),
                        "X-RateLimit-Remaining": "0",
                    },
                )

        return await call_next(request)
