"""
认证服务与中间件
支持：
1. JWT Token 认证（dashboard、admin 接口）
2. API Key（sk-xxx 格式）认证（/v1/chat、/v1/images、/v1/models 接口）
3. 用户注册、登录、登出
4. API Key CRUD
"""

import hashlib
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.database import get_db
from src.models import ApiKey, Balance, User

# ── 密码工具 ──────────────────────────────────────────────────────────────────

def _hash_password(password: str) -> str:
    """bcrypt 替代：PBKDF2-SHA256（无需额外依赖）"""
    import hashlib
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260000)
    return f"pbkdf2:sha256:260000:{salt}:{dk.hex()}"


def _verify_password(password: str, stored_hash: str) -> bool:
    parts = stored_hash.split(":")
    if len(parts) != 5 or parts[0] != "pbkdf2":
        return False
    _, algo, iterations, salt, dk_hex = parts
    dk = hashlib.pbkdf2_hmac(algo, password.encode(), salt.encode(), int(iterations))
    return secrets.compare_digest(dk.hex(), dk_hex)


# ── API Key 工具 ──────────────────────────────────────────────────────────────

def _generate_api_key() -> str:
    """生成 sk- 前缀 + 48 位随机字符的 API Key"""
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz123456789"
    raw = "".join(secrets.choice(chars) for _ in range(48))
    return f"sk-{raw}"


def _hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _key_prefix(key: str) -> str:
    return key[:8]


# ── 内存缓存（开发用；生产替换为 Redis）────────────────────────────────────────

_api_key_cache: dict[str, tuple[dict, float]] = {}  # key_hash → (payload, expire_ts)
_blacklisted_tokens: set[str] = set()

_AUTH_CACHE_TTL = 60  # 秒


def _cache_get(key_hash: str) -> Optional[dict]:
    entry = _api_key_cache.get(key_hash)
    if entry and entry[1] > time.time():
        return entry[0]
    return None


def _cache_set(key_hash: str, payload: dict) -> None:
    _api_key_cache[key_hash] = (payload, time.time() + _AUTH_CACHE_TTL)


# ── JWT ───────────────────────────────────────────────────────────────────────

def _create_jwt(user_id: str, email: str, is_admin: bool) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {
        "sub": user_id,
        "email": email,
        "is_admin": is_admin,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def _decode_jwt(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail={"error": {"message": "Token expired", "type": "authentication_error", "code": "token_expired"}})
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail={"error": {"message": "Invalid token", "type": "authentication_error", "code": "invalid_token"}})


# ── FastAPI 依赖：JWT 认证 ────────────────────────────────────────────────────

_bearer = HTTPBearer(auto_error=False)


async def get_current_user_jwt(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> dict:
    """解析 JWT Token，返回用户信息字典；失败抛 401"""
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"message": "Missing authorization header", "type": "authentication_error", "code": "missing_auth"}},
        )
    token = credentials.credentials
    if token in _blacklisted_tokens:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"message": "Token has been revoked", "type": "authentication_error", "code": "token_revoked"}},
        )
    return _decode_jwt(token)


async def get_admin_user(current_user: dict = Depends(get_current_user_jwt)) -> dict:
    """要求当前用户具有 is_admin 权限"""
    if not current_user.get("is_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"message": "Admin access required", "type": "permission_error", "code": "forbidden"}},
        )
    return current_user


# ── FastAPI 依赖：API Key 认证 ────────────────────────────────────────────────

async def get_api_key_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> tuple[User, ApiKey]:
    """
    从 Authorization: Bearer sk-xxx 中验证 API Key
    先查缓存，未命中再查数据库
    返回 (user, api_key)
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail={"error": {"message": "Missing or invalid Authorization header", "type": "authentication_error", "code": "invalid_api_key"}},
        )

    raw_key = auth_header[7:].strip()
    if not raw_key.startswith("sk-"):
        raise HTTPException(
            status_code=401,
            detail={"error": {"message": "Invalid API key format", "type": "authentication_error", "code": "invalid_api_key"}},
        )

    key_hash = _hash_api_key(raw_key)
    prefix = _key_prefix(raw_key)

    # 查缓存
    cached = _cache_get(key_hash)
    if cached:
        return cached["user_id"], cached["api_key_id"]

    # 查数据库
    result = await db.execute(
        select(ApiKey).where(
            ApiKey.key_prefix == prefix,
            ApiKey.key_hash == key_hash,
            ApiKey.is_active == True,  # noqa: E712
        )
    )
    api_key_obj = result.scalar_one_or_none()

    if not api_key_obj:
        raise HTTPException(
            status_code=401,
            detail={"error": {"message": "Invalid API key", "type": "authentication_error", "code": "invalid_api_key"}},
        )

    # 验证用户是否活跃
    user = await db.get(User, api_key_obj.user_id)
    if not user or not user.is_active:
        raise HTTPException(
            status_code=401,
            detail={"error": {"message": "User account is disabled", "type": "authentication_error", "code": "invalid_api_key"}},
        )

    # 写缓存
    _cache_set(key_hash, {"user_id": user.id, "api_key_id": api_key_obj.id})

    # 异步更新 last_used_at
    api_key_obj.last_used_at = datetime.now(timezone.utc)
    await db.commit()

    return user, api_key_obj


# ── 认证业务逻辑 ──────────────────────────────────────────────────────────────

class AuthService:
    """用户注册、登录、登出 + API Key CRUD"""

    @staticmethod
    async def register(db: AsyncSession, email: str, password: str, display_name: Optional[str] = None) -> User:
        """注册新用户（M-1：MVP 免邮箱验证）"""
        # 检查邮箱重复
        existing = await db.scalar(select(User).where(User.email == email))
        if existing:
            raise HTTPException(status_code=400, detail={"error": {"message": "Email already registered", "type": "invalid_request_error", "code": "email_exists"}})
        if len(password) < 8:
            raise HTTPException(status_code=400, detail={"error": {"message": "Password must be at least 8 characters", "type": "invalid_request_error", "code": "weak_password"}})

        # 首个注册用户自动成为管理员（自用网关免配置）
        user_count = await db.scalar(select(func.count()).select_from(User))
        user = User(
            email=email,
            password_hash=_hash_password(password),
            display_name=display_name or email.split("@")[0],
            is_admin=(user_count == 0),
        )
        db.add(user)
        # 同时初始化余额记录
        await db.flush()
        balance = Balance(user_id=user.id, amount_usd=0.0)
        db.add(balance)
        await db.commit()
        await db.refresh(user)
        return user

    @staticmethod
    async def login(db: AsyncSession, email: str, password: str) -> str:
        """登录，返回 JWT Token"""
        user = await db.scalar(select(User).where(User.email == email))
        if not user or not _verify_password(password, user.password_hash):
            raise HTTPException(
                status_code=401,
                detail={"error": {"message": "Invalid email or password", "type": "authentication_error", "code": "invalid_credentials"}},
            )
        if not user.is_active:
            raise HTTPException(
                status_code=401,
                detail={"error": {"message": "Account is disabled", "type": "authentication_error", "code": "account_disabled"}},
            )
        return _create_jwt(user.id, user.email, user.is_admin)

    @staticmethod
    def logout(token: str) -> None:
        """登出：将 token 加入黑名单（内存实现；生产用 Redis + TTL）"""
        _blacklisted_tokens.add(token)

    @staticmethod
    async def create_api_key(db: AsyncSession, user_id: str, name: str) -> tuple[ApiKey, str]:
        """创建 API Key，返回 (ApiKey 对象, 明文 key)；明文仅返回一次"""
        raw_key = _generate_api_key()
        api_key = ApiKey(
            user_id=user_id,
            name=name,
            key_prefix=_key_prefix(raw_key),
            key_hash=_hash_api_key(raw_key),
        )
        db.add(api_key)
        await db.commit()
        await db.refresh(api_key)
        return api_key, raw_key

    @staticmethod
    async def list_api_keys(db: AsyncSession, user_id: str) -> list[ApiKey]:
        result = await db.execute(
            select(ApiKey).where(ApiKey.user_id == user_id, ApiKey.is_active == True)  # noqa: E712
        )
        return list(result.scalars().all())

    @staticmethod
    async def revoke_api_key(db: AsyncSession, user_id: str, key_id: str) -> None:
        api_key = await db.scalar(
            select(ApiKey).where(ApiKey.id == key_id, ApiKey.user_id == user_id)
        )
        if not api_key:
            raise HTTPException(status_code=404, detail={"error": {"message": "API key not found", "type": "invalid_request_error", "code": "not_found"}})
        api_key.is_active = False
        # 清除缓存中对应条目
        stale = [k for k, v in _api_key_cache.items() if v[0].get("api_key_id") == key_id]
        for k in stale:
            _api_key_cache.pop(k, None)
        await db.commit()


auth_service = AuthService()


# ── 认证中间件 ──────────────────────────────────────────────────────────────────

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


class AuthMiddleware(BaseHTTPMiddleware):
    """认证中间件"""

    async def dispatch(self, request, call_next):
        # 跳过不需要认证的路径
        if request.url.path in ["/", "/health", "/docs", "/redoc", "/openapi.json", "/login", "/dashboard"]:
            return await call_next(request)

        # 跳过非 /v1 路径
        if not request.url.path.startswith("/v1"):
            return await call_next(request)

        # 跳过认证相关路径（注册/登录/登出）和 dashboard 管理路径（JWT 由路由依赖处理）
        if request.url.path.startswith("/v1/auth") or request.url.path.startswith("/v1/dashboard"):
            return await call_next(request)

        # 提取 API Key：兼容两种方式
        # 1. Authorization: Bearer sk-xxx（OpenAI 协议）
        # 2. x-api-key: sk-xxx（Anthropic 协议）
        auth_header = request.headers.get("Authorization", "")
        api_key_header = request.headers.get("x-api-key", "")
        token = ""
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
        elif api_key_header:
            token = api_key_header.strip()
        else:
            return JSONResponse(
                status_code=401,
                content={
                    "error": {
                        "message": "Missing or invalid Authorization header",
                        "type": "authentication_error",
                        "code": "invalid_api_key",
                    }
                },
            )

        # 非 sk- 开头：尝试 JWT 认证（Web 控制台登录态）
        if not token.startswith("sk-"):
            try:
                jwt_payload = _decode_jwt(token)
            except HTTPException as e:
                return JSONResponse(status_code=e.status_code, content=e.detail)
            # JWT 有效：从数据库加载用户并设置 state
            from src.database import AsyncSessionLocal as _SessionLocal
            from src.models import User
            async with _SessionLocal() as db:
                jwt_user = await db.get(User, jwt_payload["sub"])
                if not jwt_user or not jwt_user.is_active:
                    return JSONResponse(
                        status_code=401,
                        content={
                            "error": {
                                "message": "User account is disabled",
                                "type": "authentication_error",
                                "code": "invalid_api_key",
                            }
                        },
                    )
                request.state.user = jwt_user
                request.state.user_id = jwt_user.id
                # JWT 登录态等价于该用户活跃 Key 发起请求（chat 等路由按 Key 归属计费/记日志）
                from src.models import ApiKey as _ApiKey
                from sqlalchemy import select as _select
                _jwt_key_result = await db.execute(
                    _select(_ApiKey).where(
                        _ApiKey.user_id == jwt_user.id, _ApiKey.is_active == True
                    ).limit(1)
                )
                _jwt_key = _jwt_key_result.scalar_one_or_none()
                if _jwt_key:
                    request.state.api_key = _jwt_key
                    request.state.api_key_id = _jwt_key.id
            return await call_next(request)

        # 查询数据库验证 API Key
        # 动态导入：便于测试时 monkeypatch 为测试库
        from src.database import AsyncSessionLocal as _SessionLocal
        from src.models import ApiKey, User
        from sqlalchemy import select
        import hashlib

        token_hash = hashlib.sha256(token.encode()).hexdigest()
        key_prefix = token[:8]

        async with _SessionLocal() as db:
            # 查询 API Key
            result = await db.execute(
                select(ApiKey, User)
                .join(User, ApiKey.user_id == User.id)
                .where(ApiKey.key_prefix == key_prefix)
                .where(ApiKey.key_hash == token_hash)
                .where(ApiKey.is_active == True)
                .where(User.is_active == True)
            )
            row = result.one_or_none()

            if not row:
                return JSONResponse(
                    status_code=401,
                    content={
                        "error": {
                            "message": "Invalid API key",
                            "type": "authentication_error",
                            "code": "invalid_api_key",
                        }
                    },
                )

            api_key, user = row

            # 设置到 request.state
            request.state.user = user
            request.state.api_key = api_key
            request.state.user_id = user.id
            request.state.key_prefix = key_prefix

            # 更新最后使用时间
            api_key.last_used_at = datetime.now(timezone.utc)
            await db.commit()

        return await call_next(request)
