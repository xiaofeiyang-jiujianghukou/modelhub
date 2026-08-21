"""
SQLAlchemy ORM 数据模型
包含所有数据库表定义，对应 PRD 第 8 章（按审核报告修订）
"""

import uuid
from datetime import datetime, timezone
from typing import Optional, TYPE_CHECKING

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Index, Integer,
    Numeric, String, Text, UniqueConstraint, func,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, relationship

if TYPE_CHECKING:
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


def to_utc_timestamp(dt: Optional[datetime]) -> Optional[int]:
    """把 DB 读出的 datetime 转成 UTC 时间戳（秒）。

    SQLite 的 DateTime(timezone=True) 存的是 UTC，但读出是 naive（无 tzinfo），
    直接 .timestamp() 会把它误当本地时区（+8）解释，导致时间戳少 8 小时。
    统一在此补 UTC 时区再转换。
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


class Base(DeclarativeBase):
    pass


# ── 用户表 ────────────────────────────────────────────────────────────────────

class User(Base):
    """用户账号"""

    __tablename__ = "users"

    id: str = Column(String(36), primary_key=True, default=_uuid)
    email: str = Column(String(255), unique=True, nullable=False, index=True)
    password_hash: str = Column(String(255), nullable=False)
    display_name: Optional[str] = Column(String(100))
    is_admin: bool = Column(Boolean, default=False, nullable=False)
    is_active: bool = Column(Boolean, default=True, nullable=False)
    created_at: datetime = Column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: datetime = Column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)

    # 关联
    api_keys = relationship("ApiKey", back_populates="user", cascade="all, delete-orphan")
    balance = relationship("Balance", back_populates="user", uselist=False, cascade="all, delete-orphan")
    transactions = relationship("Transaction", back_populates="user")
    request_logs = relationship("RequestLog", back_populates="user")


# ── API Key 表 ─────────────────────────────────────────────────────────────────

class ApiKey(Base):
    """用户 API Key，哈希用于鉴权；key_enc 加密存明文（可揭示复制），旧 key 为 NULL"""

    __tablename__ = "api_keys"

    id: str = Column(String(36), primary_key=True, default=_uuid)
    user_id: str = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name: str = Column(String(100), nullable=False)
    key_prefix: str = Column(String(8), nullable=False)   # sk-AbCdEf 前 8 位，快速查询
    key_hash: str = Column(String(64), nullable=False, unique=True)  # SHA-256
    key_enc: Optional[str] = Column(Text, nullable=True)  # AES-256-GCM 加密的明文 key（可揭示）；旧 key 为 NULL
    is_active: bool = Column(Boolean, default=True, nullable=False)
    monthly_limit_usd: Optional[float] = Column(Numeric(10, 6), nullable=True)
    created_at: datetime = Column(DateTime(timezone=True), default=_now, nullable=False)
    last_used_at: Optional[datetime] = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", back_populates="api_keys")

    __table_args__ = (
        Index("idx_api_keys_prefix", "key_prefix"),
    )


# ── 余额表 ─────────────────────────────────────────────────────────────────────

class Balance(Base):
    """账户余额（一用户一行）"""

    __tablename__ = "balances"

    user_id: str = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    amount_usd: float = Column(Numeric(12, 6), default=0.0, nullable=False)
    updated_at: datetime = Column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)

    user = relationship("User", back_populates="balance")


# ── 交易记录表 ─────────────────────────────────────────────────────────────────

class Transaction(Base):
    """充值与消费流水"""

    __tablename__ = "transactions"

    id: str = Column(String(36), primary_key=True, default=_uuid)
    user_id: str = Column(String(36), ForeignKey("users.id"), nullable=False)
    type: str = Column(String(20), nullable=False)          # 'topup' | 'usage'
    amount_usd: float = Column(Numeric(12, 6), nullable=False)  # 正=充值，负=消费
    balance_after: float = Column(Numeric(12, 6), nullable=False)
    description: Optional[str] = Column(Text, nullable=True)
    request_log_id: Optional[str] = Column(String(36), nullable=True)
    stripe_payment_id: Optional[str] = Column(String(255), nullable=True)
    created_at: datetime = Column(DateTime(timezone=True), default=_now, nullable=False)

    user = relationship("User", back_populates="transactions")

    __table_args__ = (
        Index("idx_transactions_user_id", "user_id", "created_at"),
    )


# ── 请求日志表 ─────────────────────────────────────────────────────────────────

class RequestLog(Base):
    """每次 API 请求的日志（审核报告 m-3：新增 image_count / video_seconds）"""

    __tablename__ = "request_logs"

    id: str = Column(String(36), primary_key=True, default=_uuid)
    user_id: str = Column(String(36), ForeignKey("users.id"), nullable=False)
    api_key_id: Optional[str] = Column(String(36), ForeignKey("api_keys.id"), nullable=True)
    request_id: str = Column(String(64), nullable=False, unique=True)  # X-Gateway-Request-ID
    model: str = Column(String(100), nullable=False)
    provider: Optional[str] = Column(String(100), nullable=True)
    request_type: str = Column(String(20), nullable=False)   # 'chat' | 'image' | 'video'
    status: str = Column(String(20), nullable=False)          # 'success' | 'error'
    status_code: Optional[int] = Column(Integer, nullable=True)
    prompt_tokens: Optional[int] = Column(Integer, nullable=True)
    completion_tokens: Optional[int] = Column(Integer, nullable=True)
    total_tokens: Optional[int] = Column(Integer, nullable=True)
    cache_hit_tokens: Optional[int] = Column(Integer, nullable=True)   # 前缀缓存命中（Layer 3 监控）
    cache_miss_tokens: Optional[int] = Column(Integer, nullable=True)  # 前缀缓存未命中
    image_count: Optional[int] = Column(Integer, nullable=True)         # 图像张数（m-3）
    video_seconds: Optional[float] = Column(Numeric(10, 3), nullable=True)  # 视频秒数（m-3）
    cost_usd: Optional[float] = Column(Numeric(12, 6), nullable=True)
    latency_ms: Optional[int] = Column(Integer, nullable=True)
    error_code: Optional[str] = Column(String(100), nullable=True)
    created_at: datetime = Column(DateTime(timezone=True), default=_now, nullable=False)

    user = relationship("User", back_populates="request_logs")

    __table_args__ = (
        Index("idx_request_logs_user", "user_id", "created_at"),
    )


# ── 模型目录表 ─────────────────────────────────────────────────────────────────

class Model(Base):
    """
    模型目录（单表）：模型名 + 厂商 复合唯一。
    模型名可重复（同一模型多厂商提供），「模型名 + 厂商」确定唯一记录；
    每条记录独立定价 + 独立上游模型名（路由信息合并自原 RouteChannel）。
    """

    __tablename__ = "models"

    model: str = Column(String(100), primary_key=True)         # 模型名，如 'glm-5.2'（可重复）
    vendor: str = Column(String(50), primary_key=True)         # 厂商 key（与 model 复合唯一）
    display_name: Optional[str] = Column(String(200), nullable=True)
    owned_by: Optional[str] = Column(String(100), nullable=True)
    model_type: str = Column(String(20), nullable=False)   # 'llm' | 'image' | 'video'
    input_price: Optional[float] = Column(Numeric(10, 6), nullable=True)   # 每 1M tokens
    output_price: Optional[float] = Column(Numeric(10, 6), nullable=True)  # 每 1M tokens
    unit_price: Optional[float] = Column(Numeric(10, 6), nullable=True)    # 每张/每秒
    price_currency: str = Column(String(3), default="USD", nullable=False)  # 价格币种：CNY | USD（官方原始币种，避免折算精度丢失）
    context_window: Optional[int] = Column(Integer, nullable=True)
    price_source: str = Column(String(10), default="default", nullable=False)  # official | default | manual
    upstream_model: Optional[str] = Column(String(200), nullable=True)   # 上游真实模型名（路由用）
    alias: Optional[str] = Column(String(100), nullable=True, unique=True, index=True)  # 客户端别名（全局唯一）
    weight: int = Column(Integer, default=100, nullable=False)
    priority: int = Column(Integer, default=0, nullable=False)
    # m-2: healthy | degraded | circuit_open | circuit_half_open
    health_status: str = Column(String(30), default="healthy", nullable=False)
    error_count: int = Column(Integer, default=0, nullable=False)
    success_count: int = Column(Integer, default=0, nullable=False)
    last_checked_at: Optional[datetime] = Column(DateTime(timezone=True), nullable=True)
    circuit_open_until: Optional[datetime] = Column(DateTime(timezone=True), nullable=True)
    last_synced_at: Optional[datetime] = Column(DateTime(timezone=True), nullable=True)
    synced_from: Optional[str] = Column(String(100), nullable=True)        # 同步来源 provider.name
    supports_streaming: bool = Column(Boolean, default=True, nullable=False)
    route_strategy: str = Column(String(30), default="weighted_random", nullable=False)
    is_active: bool = Column(Boolean, default=True, nullable=False)
    created_at: datetime = Column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: datetime = Column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)

    __table_args__ = (
        Index("idx_models_vendor", "vendor", "is_active"),
    )

    @classmethod
    async def get_by_model_and_vendor(cls, db: AsyncSession, model: str, vendor: str):
        """按复合主键精确查"""
        row = await db.get(cls, (model, vendor))
        return row if (row and row.is_active) else None

    @classmethod
    async def get_by_alias(cls, db: AsyncSession, alias: str):
        """按别名查（全局唯一），不存在返回 None"""
        from sqlalchemy import select
        result = await db.execute(
            select(cls).where(cls.alias == alias, cls.is_active == True)
            .limit(1)
        )
        return result.scalar_one_or_none()

    @classmethod
    async def resolve_or_default(cls, db: AsyncSession, model_name: str):
        """
        解析模型名（vendor/model 或 vendor/model@alias），不存在时兜底到默认模型。
        返回: (model, actual_model_name)
        """
        from src.config import settings
        from src.services.model_key import parse_model_key

        mid, vendor = parse_model_key(model_name)
        if vendor:
            model = await cls.get_by_model_and_vendor(db, mid, vendor)
        else:
            model = await cls.get_by_alias(db, mid)
        if model and model.is_active:
            from src.services.model_key import format_model_key
            actual = format_model_key(model.model, model.vendor)
            return model, actual

        # Claude Code 上下文窗口后缀剥离（deepseek-v4-pro[1M] → deepseek-v4-pro）
        import re as _re
        cleaned = _re.sub(r"\[\d+[MK]\]$", "", model_name, flags=_re.I)
        if cleaned != model_name:
            mid2, vendor2 = parse_model_key(cleaned)
            if vendor2:
                model = await cls.get_by_model_and_vendor(db, mid2, vendor2) or await cls.get_by_alias(db, mid2)
                if model and model.is_active:
                    from src.services.model_key import format_model_key
                    actual = format_model_key(model.model, model.vendor)
                    return model, actual

        # 官方客户端默认模型名兜底（Codex: gpt-*/o1/o3/o4；Claude: claude-*）
        if model_name.startswith(("gpt-", "o1", "o3", "o4", "claude-")):
            default = settings.default_claude_model
            dmid, dvendor = parse_model_key(default)
            if dvendor:
                default_model = await cls.get_by_model_and_vendor(db, dmid, dvendor)
                if default_model and default_model.is_active:
                    return default_model, default

        return model, model_name

# ── 供应商表 ───────────────────────────────────────────────────────────────────

class Provider(Base):
    """上游 AI 供应商配置，凭证加密存储"""

    __tablename__ = "providers"

    id: str = Column(String(36), primary_key=True, default=_uuid)
    name: str = Column(String(100), unique=True, nullable=False)
    base_url: str = Column(String(500), nullable=False)
    auth_type: str = Column(String(20), nullable=False)    # 'bearer' | 'ak_sk'
    credentials_enc: str = Column(Text, nullable=False)    # AES-256-GCM 加密 JSON
    timeout_ms: int = Column(Integer, default=30000, nullable=False)
    is_active: bool = Column(Boolean, default=True, nullable=False)
    last_synced_at: Optional[datetime] = Column(DateTime(timezone=True), nullable=True)
    last_sync_status: Optional[str] = Column(String(20), nullable=True)     # success | error | pending
    last_sync_error: Optional[str] = Column(Text, nullable=True)
    created_at: datetime = Column(DateTime(timezone=True), default=_now, nullable=False)


# ── 视频任务表（审核报告 M-4，MVP 建表但不实现功能）─────────────────────────

class VideoTask(Base):
    """异步视频生成任务"""

    __tablename__ = "video_tasks"

    id: str = Column(String(64), primary_key=True)   # vtask-xxx
    user_id: str = Column(String(36), ForeignKey("users.id"), nullable=False)
    api_key_id: Optional[str] = Column(String(36), ForeignKey("api_keys.id"), nullable=True)
    model: str = Column(String(100), nullable=False)
    prompt: str = Column(Text, nullable=False)
    status: str = Column(String(20), default="pending", nullable=False)
    result_url: Optional[str] = Column(String(1000), nullable=True)
    duration_seconds: Optional[int] = Column(Integer, nullable=True)
    billed_seconds: Optional[int] = Column(Integer, nullable=True)
    cost_usd: Optional[float] = Column(Numeric(12, 6), nullable=True)
    error_message: Optional[str] = Column(Text, nullable=True)
    created_at: datetime = Column(DateTime(timezone=True), default=_now, nullable=False)
    completed_at: Optional[datetime] = Column(DateTime(timezone=True), nullable=True)


# ── 模型参考价/上下文表（官方核对数据源，dashboard 界面可管理）────────────────

class ModelReference(Base):
    """
    模型官方参考价 + 上下文窗口（sync 时的兜底 / static 供应商清单数据源）。

    与 Model（实际同步结果）分离：本表是"官方参考值"，由界面/种子维护，
    sync 时优先用上游返回，上游缺失时用本表兜底；static 供应商（无 /models 端点）的
    模型清单也存本表（vendor 字段标记归属）。
    """

    __tablename__ = "model_references"

    model_id: str = Column(String(100), primary_key=True)  # 网关模型 ID
    vendor: Optional[str] = Column(String(50), nullable=True, index=True)  # 供应商 key（static 清单归属）
    upstream_model: Optional[str] = Column(String(200), nullable=True)     # 上游真实模型名（static 用）
    display_name: Optional[str] = Column(String(200), nullable=True)
    input_price: Optional[float] = Column(Numeric(10, 6), nullable=True)   # 每 1M tokens（币种见 price_currency）
    output_price: Optional[float] = Column(Numeric(10, 6), nullable=True)  # 每 1M tokens（币种见 price_currency）
    price_currency: str = Column(String(3), default="USD", nullable=False)  # 价格币种：CNY | USD（官方原始币种）
    context_window: Optional[int] = Column(Integer, nullable=True)
    price_source: str = Column(String(10), default="official", nullable=False)  # official | default | manual
    created_at: datetime = Column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: datetime = Column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)
