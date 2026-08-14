"""
数据库连接与会话管理
支持 SQLite（开发）和 PostgreSQL（生产），统一使用 SQLAlchemy 异步接口
"""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy import event

from src.config import settings
from src.db.models import Base

# ── 引擎 ─────────────────────────────────────────────────────────────────────

def _make_engine() -> AsyncEngine:
    url = settings.database_url
    connect_args = {}
    # SQLite 需要 check_same_thread=False 才能在异步中使用
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        connect_args["timeout"] = 30  # busy_timeout：写锁冲突时等待而非立即报 database is locked
    return create_async_engine(
        url,
        echo=settings.debug,
        connect_args=connect_args,
        pool_pre_ping=True,
    )


engine: AsyncEngine = _make_engine()

# SQLite 并发优化：WAL 模式（读不阻塞写）+ 长 busy_timeout，缓解后台同步与前台请求的写锁冲突
if settings.database_url.startswith("sqlite"):

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()

# async_sessionmaker 工厂
AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


# ── 初始化 ───────────────────────────────────────────────────────────────────

async def init_db() -> None:
    """创建所有数据表（如不存在）"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    """关闭数据库连接池"""
    await engine.dispose()


# ── FastAPI 依赖注入用的会话工厂 ─────────────────────────────────────────────

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI Depends 用：获取数据库会话，请求结束后自动关闭"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """后台任务或服务层直接使用的上下文管理器版本"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
