"""
路由引擎服务
根据模型名选择最优上游通道，支持加权随机、优先级、故障转移
"""

import random
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Model, Provider
from src.providers.base import BaseProvider
from src.providers.openai_provider import OpenAIProvider
from src.providers.anthropic_provider import AnthropicProvider
from src.providers.gemini_provider import GeminiProvider
from src.providers.mock_provider import MockProvider
from src.config import settings
from src.services.model_key import parse_model_key, format_model_key, strip_context_suffix
from src.services.chat_tools import normalize_tool_message_order


# 统一适配器工厂（按注册表 adapter + AES-GCM 解密凭证构建）
# 兼容 legacy 明文凭证（crypto.decrypt_credentials 内部处理）
from src.providers import build_provider  # noqa: E402


def _ensure_aware(dt: Optional[datetime]) -> Optional[datetime]:
    """SQLite 读出的 datetime 无时区，统一补 UTC"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _channel_key(channel: Model) -> str:
    """通道唯一标识：厂商key/模型名"""
    return format_model_key(channel.model, channel.vendor)


def _select_channel(channels: list[Model], strategy: str) -> Optional[Model]:
    """从候选通道中按策略选择一个"""
    now = datetime.now(timezone.utc)
    available = []
    for ch in channels:
        if not ch.is_active:
            continue
        if ch.health_status == "circuit_open":
            circuit_open_until = _ensure_aware(ch.circuit_open_until)
            if circuit_open_until and now < circuit_open_until:
                continue
            # 到达冷却期，转为 half-open
            ch.health_status = "circuit_half_open"
        available.append(ch)

    if not available:
        return None

    if strategy == "priority":
        available.sort(key=lambda c: c.priority, reverse=True)
        return available[0]
    elif strategy == "lowest_latency":
        # MVP：无延迟记录时降级为 weighted_random
        return _weighted_random(available)
    else:
        # weighted_random（默认）
        return _weighted_random(available)


def _weighted_random(channels: list[Model]) -> Optional[Model]:
    """按权重加权随机选择"""
    if not channels:
        return None
    total = sum(c.weight for c in channels)
    if total <= 0:
        return random.choice(channels)
    r = random.uniform(0, total)
    cumulative = 0
    for ch in channels:
        cumulative += ch.weight
        if r <= cumulative:
            return ch
    return channels[-1]


def _mark_channel_error(channel: Model) -> None:
    """标记通道错误，必要时触发熔断"""
    channel.error_count += 1
    total = channel.error_count + channel.success_count
    if total >= 5:
        error_rate = channel.error_count / total
        if error_rate > settings.circuit_breaker_threshold:
            cooldown = settings.circuit_breaker_cooldown_seconds
            channel.health_status = "circuit_open"
            channel.circuit_open_until = datetime.now(timezone.utc) + timedelta(seconds=cooldown)
            logger.warning(
                "circuit breaker opened for channel={} model={} error_rate={:.2f}",
                _channel_key(channel), channel.model, error_rate,
            )


def _mark_channel_success(channel: Model) -> None:
    """标记通道成功，若处于 half-open 则恢复"""
    channel.success_count += 1
    if channel.health_status == "circuit_half_open":
        channel.health_status = "healthy"
        channel.error_count = 0
        logger.info("circuit breaker closed for channel={}", _channel_key(channel))


class RouterService:
    """
    路由引擎
    负责：模型名 → 路由通道选择 → 供应商适配器调用 → 故障转移
    """

    async def resolve_model_id(self, db: AsyncSession, model_name: str) -> str:
        """解析模型名（vendor/model 或 alias）为路由键 vendor/model"""
        mid, vendor = parse_model_key(model_name)
        if vendor:
            return format_model_key(mid, vendor)
        row = await Model.get_by_alias(db, mid)
        if row:
            return format_model_key(row.model, row.vendor)
        raise ValueError(f"model name '{model_name}' is not a valid vendor/model key or alias")

    async def get_channels(
        self,
        db: AsyncSession,
        model_id: str,
    ) -> tuple[list[Model], str]:
        """
        获取模型通道：'厂商key/模型名' → 精确唯一一条（厂商+模型确定的路）
        """
        mid, vendor = parse_model_key(model_id)
        if not vendor:
            raise ValueError(f"model_id '{model_id}' missing vendor prefix (expected vendor/model)")
        model = await db.get(Model, (mid, vendor))
        if not model or not model.is_active:
            return [], "weighted_random"
        provider = await db.scalar(
            select(Provider).where(Provider.name == vendor, Provider.is_active == True)
        )
        if not provider:
            return [], "weighted_random"
        return [model], model.route_strategy

    async def route_chat(
        self,
        db: AsyncSession,
        model_name: str,
        payload: dict[str, Any],
        stream: bool = False,
    ) -> tuple[Any, Optional[Model], Optional[Provider], Optional[dict]]:
        """
        路由文本对话请求，支持故障转移
        返回：(response_or_generator, used_channel, used_provider, last_error_info)
        last_error_info: 全部通道失败时，最后一次上游错误的详情
        """
        # 唯一发送入口：发送前统一规范化 tool 消息顺序
        # （assistant(tool_calls) 后必须紧跟 tool，否则 DeepSeek 等上游报 insufficient tool messages）
        # 公共方法放 chat_tools.py，这里只调用；不绑定具体供应商适配器（openai/anthropic/gemini 共用）
        payload["messages"] = normalize_tool_message_order(payload.get("messages", []))

        model_id = await self.resolve_model_id(db, model_name)
        channels, strategy = await self.get_channels(db, model_id)

        if not channels:
            return None, None, None, None

        tried: set[str] = set()
        last_error_info: Optional[dict] = None

        for attempt in range(settings.max_retries + 1):
            remaining = [c for c in channels if _channel_key(c) not in tried]
            channel = _select_channel(remaining, strategy)
            if not channel:
                break
            tried.add(_channel_key(channel))

            # 加载供应商
            provider_obj = await db.scalar(select(Provider).where(Provider.name == channel.vendor))
            if not provider_obj or not provider_obj.is_active:
                _mark_channel_error(channel)
                await db.commit()
                continue

            adapter = build_provider(provider_obj)
            if not adapter:
                continue

            try:
                if stream:
                    gen = adapter.chat_completions_stream(channel.upstream_model, payload)
                    _mark_channel_success(channel)
                    await db.commit()
                    return gen, channel, provider_obj, None
                else:
                    result = await adapter.chat_completions(channel.upstream_model, payload)
                    sc = result.get("_status_code", 200)
                    if sc in (401, 403):
                        # Key 无效/无权限：重试无意义，直接返回上游错误（不重试）
                        _mark_channel_error(channel)
                        await db.commit()
                        return result, channel, provider_obj, None
                    if sc >= 500 or sc == 429:
                        # 触发故障转移：5xx 服务端错误 + 429 限流/余额不足
                        err_msg = result.get("error", {}).get("message", "upstream error")
                        last_error_info = {
                            "status_code": sc,
                            "message": err_msg,
                            "provider": provider_obj.name,
                            "model": channel.upstream_model,
                        }
                        raise RuntimeError(err_msg)
                    _mark_channel_success(channel)
                    await db.commit()
                    return result, channel, provider_obj, None
            except Exception as exc:
                if last_error_info is None:
                    # 网络/超时等异常，标记 502
                    last_error_info = {
                        "status_code": 502,
                        "message": str(exc),
                        "provider": provider_obj.name if provider_obj else "unknown",
                        "model": channel.upstream_model if channel else None,
                    }
                logger.warning(
                    "channel {} failed on attempt {}: {}",
                    _channel_key(channel), attempt + 1, exc,
                )
                _mark_channel_error(channel)
                await db.commit()

        return None, None, None, last_error_info

    async def route_image(
        self,
        db: AsyncSession,
        model_name: str,
        payload: dict[str, Any],
    ) -> tuple[Optional[dict], Optional[Model], Optional[Provider], Optional[dict]]:
        """路由图像生成请求"""
        model_id = await self.resolve_model_id(db, model_name)
        channels, strategy = await self.get_channels(db, model_id)

        if not channels:
            return None, None, None, None

        tried: set[str] = set()
        last_error_info: Optional[dict] = None

        for attempt in range(settings.max_retries + 1):
            remaining = [c for c in channels if _channel_key(c) not in tried]
            channel = _select_channel(remaining, strategy)
            if not channel:
                break
            tried.add(_channel_key(channel))

            provider_obj = await db.scalar(select(Provider).where(Provider.name == channel.vendor))
            if not provider_obj or not provider_obj.is_active:
                _mark_channel_error(channel)
                await db.commit()
                continue

            adapter = build_provider(provider_obj)
            if not adapter:
                continue

            try:
                result = await adapter.image_generations(channel.upstream_model, payload)
                sc = result.get("_status_code", 200)
                if sc in (401, 403):
                    # Key 无效/无权限：重试无意义，直接返回上游错误（不重试）
                    _mark_channel_error(channel)
                    await db.commit()
                    return result, channel, provider_obj, None
                if sc >= 500 or sc == 429:
                    # 触发故障转移：5xx 服务端错误 + 429 限流/余额不足
                    err_msg = result.get("error", {}).get("message", "upstream error")
                    last_error_info = {
                        "status_code": sc,
                        "message": err_msg,
                        "provider": provider_obj.name,
                        "model": channel.upstream_model,
                    }
                    raise RuntimeError(err_msg)
                _mark_channel_success(channel)
                await db.commit()
                return result, channel, provider_obj, None
            except Exception as exc:
                if last_error_info is None:
                    last_error_info = {
                        "status_code": 502,
                        "message": str(exc),
                        "provider": provider_obj.name if provider_obj else "unknown",
                        "model": channel.upstream_model if channel else None,
                    }
                logger.warning("image channel {} failed: {}", _channel_key(channel), exc)
                _mark_channel_error(channel)
                await db.commit()

        return None, None, None, last_error_info


# 全局单例
router_service = RouterService()
