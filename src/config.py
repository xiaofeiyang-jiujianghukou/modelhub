"""
配置管理模块
通过 Pydantic Settings 从环境变量加载配置，支持 .env 文件
"""

from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional


class Settings(BaseSettings):
    """网关配置，所有字段均可通过环境变量覆盖"""

    # ── 基础服务配置 ──────────────────────────────────────────
    app_name: str = "模枢 ModelHub"
    app_version: str = "1.0.0"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8000

    # ── 数据库配置 ──────────────────────────────────────────────
    # 开发：sqlite+aiosqlite:///./gateway.db
    # 生产：postgresql+asyncpg://user:pass@host/db
    database_url: str = "sqlite+aiosqlite:///./gateway.db"

    # ── 缓存配置（开发用内存字典，生产换 Redis URL）────────────
    redis_url: Optional[str] = None  # None 表示使用内存缓存

    # ── 安全配置 ────────────────────────────────────────────────
    # JWT 签名密钥，生产环境必须通过环境变量注入
    secret_key: str = "change-me-in-production-use-a-long-random-string"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24  # 24 小时

    # 供应商凭证加密密钥（AES-256-GCM），32 字节 base64
    credentials_encryption_key: str = Field(
        default="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        description="32 bytes base64 encoded key for AES-256-GCM encryption",
    )

    # ── 上游供应商 API Key ───────────────────────────────────────
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    gemini_api_key: Optional[str] = None
    deepseek_api_key: Optional[str] = None
    glm_api_key: Optional[str] = None
    ark_api_key: Optional[str] = None  # 火山引擎方舟

    # ── 限流配置 ────────────────────────────────────────────────
    rate_limit_rpm: int = 300          # per-user RPM
    rate_limit_global_rpm: int = 10000  # 全局 RPM

    # ── 计费配置 ────────────────────────────────────────────────
    signup_bonus_usd: float = 10.0     # 新用户注册赠送额度（商用可设 0）

    # ── 路由配置 ────────────────────────────────────────────────
    max_retries: int = 2               # 故障转移最大重试次数
    upstream_timeout_seconds: float = 30.0
    # Anthropic 协议层默认模型（claude-* 请求映射到该模型）；glm-4-flash 免费，便于测试
    default_claude_model: str = "glm/glm-4-flash"
    # 币种换算：价格统一以 USD 存储，接口按需换算返回 CNY
    usd_to_cny_rate: float = 7.2

    # ── Codex 模型目录（模型变化时自动重写，Codex 启动时加载最新清单）────────
    codex_catalog_path: Optional[str] = None   # 如 /home/xxx/.codex/model-catalog.local.json

    # ── 前缀缓存优化（docs/CACHE_OPTIMIZATION_DESIGN.md Layer 1）──────────────
    prefix_cache_stabilize: bool = True        # tools 确定性排序（跨请求字节一致，最大化上游前缀缓存命中）
    prefix_cache_key_vendors: str = "hunyuan,grok"  # 注入 prompt_cache_key 的厂商（会话级稳定 ID）

    # ── 显式缓存注入（Layer 2）：Anthropic Claude cache_control ────────────────
    # 命中省 90%（读 0.1×），但写缓存溢价 1.25×(5m)/2×(1h) —— 需 ≥2 次读才回本，
    # 多轮对话（Claude Code 等 system 复用场景）默认划算；一次性调用可关闭。
    anthropic_cache_control: bool = True
    anthropic_cache_ttl: str = "5m"            # "5m"（写 1.25×）| "1h"（写 2×，需 ≥3 次读回本）

    # ── 健康检查 ────────────────────────────────────────────────
    health_check_interval_seconds: int = 30
    circuit_breaker_threshold: float = 0.5   # 错误率阈值
    circuit_breaker_window_seconds: int = 60
    circuit_breaker_cooldown_seconds: int = 60

    # ── CORS ────────────────────────────────────────────────────
    cors_origins: list[str] = ["*"]

    # ── 日志 ────────────────────────────────────────────────────
    log_level: str = "INFO"
    log_json: bool = False

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


# 全局单例
settings = Settings()
