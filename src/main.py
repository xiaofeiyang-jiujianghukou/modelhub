"""
Multi-Model Intelligent Orchestration Gateway
多模型智能编排网关 - 应用入口
"""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from loguru import logger

from src.config import settings
from src.database import init_db, close_db, AsyncSessionLocal
from src.middleware.auth import AuthMiddleware
from src.middleware.rate_limit import RateLimitMiddleware
from src.services.health import init_health_checker, start_health_checks, stop_health_checks

# 导入路由
from src.routers import models_list, chat, images, dashboard, auth, web, videos, anthropic, responses, providers


# ── 应用生命周期 ───────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动/关闭钩子"""
    # 启动时执行
    logger.info(f"Starting {settings.app_name} v{settings.app_version}")
    await init_db()
    # 结构迁移（guarded ALTER，幂等）
    from scripts.migrate_db import run_db_migrations
    applied = await run_db_migrations()
    if applied:
        logger.info(f"DB migration applied: {', '.join(applied)}")

    # 启动健康检查后台任务
    init_health_checker(
        lambda: AsyncSessionLocal(),
        settings.health_check_interval_seconds,
    )
    await start_health_checks()

    yield

    # 关闭时执行
    logger.info("Shutting down...")
    await stop_health_checks()
    await close_db()


# ── FastAPI 应用 ──────────────────────────────────────────────────────────────

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
    lifespan=lifespan,
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 自定义中间件
app.add_middleware(AuthMiddleware)
app.add_middleware(RateLimitMiddleware)


# ── 全局异常处理 ───────────────────────────────────────────────────────────────

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """统一 HTTPException 响应格式为 OpenAI 标准错误结构"""
    # 若 detail 已是 {"error": {...}} 结构则原样返回，否则包装
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        content = exc.detail
    elif isinstance(exc.detail, dict):
        content = {"error": exc.detail}
    else:
        content = {"error": {"message": str(exc.detail), "type": "api_error", "code": "error"}}
    return JSONResponse(status_code=exc.status_code, content=content)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """全局异常处理器"""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)

    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "message": "Internal server error",
                "type": "api_error",
                "code": "internal_error",
            }
        },
    )


# ── 路由挂载 ───────────────────────────────────────────────────────────────────

app.include_router(models_list.router, prefix="/v1")
app.include_router(chat.router, prefix="/v1")
app.include_router(images.router, prefix="/v1")
app.include_router(dashboard.router, prefix="/v1")
app.include_router(auth.router, prefix="/v1")
app.include_router(videos.router, prefix="/v1")
app.include_router(anthropic.router, prefix="/v1")
app.include_router(responses.router, prefix="/v1")
app.include_router(providers.router, prefix="/v1")
app.include_router(web.router)


# ── 健康检查端点 ───────────────────────────────────────────────────────────────

@app.get("/health")
async def health_check():
    """
    健康检查端点，返回服务状态和各供应商连通性
    """
    # TODO: 查询并返回各供应商的实际健康状态
    return {
        "status": "healthy",
        "timestamp": asyncio.get_event_loop().time(),
        "providers": {
            # "openai": "healthy",
            # "anthropic": "degraded",
            # ...
        },
    }


@app.get("/")
async def root():
    """根路径，跳转到 Web 控制台（未登录由前端自动跳 /login）"""
    return RedirectResponse(url="/dashboard")


# ── 启动入口 ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "src.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        log_level=settings.log_level.lower(),
    )
