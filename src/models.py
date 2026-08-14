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
    """用户 API Key，仅存哈希，原文仅创建时返回一次"""

    __tablename__ = "api_keys"

    id: str = Column(String(36), primary_key=True, default=_uuid)
    user_id: str = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name: str = Column(String(100), nullable=False)
    key_prefix: str = Column(String(8), nullable=False)   # sk-AbCdEf 前 8 位，快速查询
    key_hash: str = Column(String(64), nullable=False, unique=True)  # SHA-256
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

class ModelCatalog(Base):
    """
    可用模型目录（审核报告 M-5：route_strategy 移至此表）
    """

    __tablename__ = "models"

    id: str = Column(String(100), primary_key=True)  # 如 'gpt-4o'
    display_name: Optional[str] = Column(String(200), nullable=True)
    owned_by: Optional[str] = Column(String(100), nullable=True)
    model_type: str = Column(String(20), nullable=False)   # 'llm' | 'image' | 'video'
    input_price: Optional[float] = Column(Numeric(10, 6), nullable=True)   # 每 1M tokens
    output_price: Optional[float] = Column(Numeric(10, 6), nullable=True)  # 每 1M tokens
    unit_price: Optional[float] = Column(Numeric(10, 6), nullable=True)    # 每张/每秒
    context_window: Optional[int] = Column(Integer, nullable=True)
    price_source: str = Column(String(10), default="default", nullable=False)  # official | default | manual
    last_synced_at: Optional[datetime] = Column(DateTime(timezone=True), nullable=True)
    synced_from: Optional[str] = Column(String(100), nullable=True)        # 同步来源 provider.name
    supports_streaming: bool = Column(Boolean, default=True, nullable=False)
    route_strategy: str = Column(String(30), default="weighted_random", nullable=False)  # M-5
    is_active: bool = Column(Boolean, default=True, nullable=False)
    created_at: datetime = Column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: datetime = Column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)

    route_channels = relationship("RouteChannel", back_populates="model")
    aliases = relationship("ModelAlias", back_populates="model")

    @classmethod
    async def get_by_id_or_alias(cls, db: AsyncSession, model_id: str):
        """通过 ID 或别名获取模型"""
        from sqlalchemy import select
        # 先尝试直接查询
        result = await db.execute(select(cls).where(cls.id == model_id))
        model = result.scalar_one_or_none()
        if model:
            return model
        # 尝试通过别名查询
        result = await db.execute(
            select(cls)
            .join(ModelAlias, cls.id == ModelAlias.model_id)
            .where(ModelAlias.alias == model_id)
        )
        return result.scalar_one_or_none()

    @classmethod
    async def resolve_or_default(cls, db: AsyncSession, model_name: str):
        """
        解析模型，若不存在则尝试官方模型名兜底映射到默认模型
        返回: (model, actual_model_name)
        """
        from src.config import settings
        model = await cls.get_by_id_or_alias(db, model_name)
        if model and model.is_active:
            return model, model_name

        # 官方客户端默认模型名兜底（Codex: gpt-*/o1/o3/o4；Claude: claude-*）
        if model_name.startswith(("gpt-", "o1", "o3", "o4", "claude-")):
            default = settings.default_claude_model
            default_model = await cls.get_by_id_or_alias(db, default)
            if default_model and default_model.is_active:
                return default_model, default

        return model, model_name


# ── 模型别名表（审核报告 M-3）────────────────────────────────────────────────

class ModelAlias(Base):
    """模型别名（如 gpt-4-turbo → gpt-4o）"""

    __tablename__ = "model_aliases"

    alias: str = Column(String(100), primary_key=True)
    model_id: str = Column(String(100), ForeignKey("models.id"), nullable=False)
    created_at: datetime = Column(DateTime(timezone=True), default=_now, nullable=False)

    model = relationship("ModelCatalog", back_populates="aliases")


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

    route_channels = relationship("RouteChannel", back_populates="provider")


# ── 路由通道表 ─────────────────────────────────────────────────────────────────

class RouteChannel(Base):
    """
    路由通道（审核报告 M-5：strategy 已移至 models 表）
    health_status 使用审核报告 m-2 建议的枚举值
    """

    __tablename__ = "route_channels"

    id: str = Column(String(36), primary_key=True, default=_uuid)
    model_id: str = Column(String(100), ForeignKey("models.id"), nullable=False)
    provider_id: str = Column(String(36), ForeignKey("providers.id"), nullable=False)
    upstream_model: str = Column(String(200), nullable=False)  # 上游实际模型名
    weight: int = Column(Integer, default=100, nullable=False)
    priority: int = Column(Integer, default=0, nullable=False)
    is_active: bool = Column(Boolean, default=True, nullable=False)
    # m-2: healthy | degraded | circuit_open | circuit_half_open
    health_status: str = Column(String(30), default="healthy", nullable=False)
    error_count: int = Column(Integer, default=0, nullable=False)
    success_count: int = Column(Integer, default=0, nullable=False)
    last_checked_at: Optional[datetime] = Column(DateTime(timezone=True), nullable=True)
    circuit_open_until: Optional[datetime] = Column(DateTime(timezone=True), nullable=True)

    model = relationship("ModelCatalog", back_populates="route_channels")
    provider = relationship("Provider", back_populates="route_channels")

    __table_args__ = (
        Index("idx_route_channels_model", "model_id", "is_active"),
    )


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
