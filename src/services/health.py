"""
供应商健康检查后台任务
定期向各供应商发送轻量请求，更新健康状态
"""

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Provider, RouteChannel
from src.providers import get_provider
from src.config import settings


class HealthChecker:
    """健康检查器"""

    def __init__(self, db_session_factory, interval_seconds: int = 30):
        self.db_session_factory = db_session_factory
        self.interval = interval_seconds
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self):
        """启动后台检查任务"""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        """停止后台任务"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self):
        """检查循环"""
        while self._running:
            try:
                await self._check_all_providers()
            except Exception as e:
                print(f"Health check error: {e}")
            await asyncio.sleep(self.interval)

    async def _check_all_providers(self):
        """检查所有供应商的健康状态"""
        async with self.db_session_factory() as db:
            # 获取所有活跃的路由通道
            result = await db.execute(
                select(RouteChannel, Provider)
                .join(Provider, RouteChannel.provider_id == Provider.id)
                .where(RouteChannel.is_active == True)
                .where(Provider.is_active == True)
            )
            rows = result.all()

            for channel, provider in rows:
                await self._check_channel(db, channel, provider)

    @staticmethod
    def _ensure_aware(dt: Optional[datetime]) -> Optional[datetime]:
        """SQLite 存储会丢失时区信息，读取时补上 UTC 时区"""
        if dt is None:
            return None
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt

    async def _check_channel(self, db, channel: RouteChannel, provider: Provider):
        """检查单个通道"""
        now = datetime.now(timezone.utc)

        # 检查是否在熔断冷却期（处理 SQLite naive datetime）
        circuit_open_until = self._ensure_aware(channel.circuit_open_until)
        if circuit_open_until and circuit_open_until > now:
            # 仍在冷却期，跳过检查
            return

        try:
            # 获取 provider 实例
            provider_instance = get_provider(provider.name)
            if not provider_instance:
                return

            # 发送健康检查请求（轻量级）
            is_healthy = await provider_instance.health_check()

            if is_healthy:
                # 成功：更新状态为 healthy，重置错误计数（不依赖熔断计数）
                await db.execute(
                    update(RouteChannel)
                    .where(RouteChannel.id == channel.id)
                    .values(
                        health_status="healthy",
                        last_checked_at=now,
                        circuit_open_until=None,
                    )
                )
            else:
                # 检查失败：仅标记 degraded，不累加熔断计数
                # （熔断器由真实请求失败驱动，避免健康检查误熔断）
                await db.execute(
                    update(RouteChannel)
                    .where(RouteChannel.id == channel.id)
                    .values(
                        health_status="degraded",
                        last_checked_at=now,
                    )
                )
                await db.commit()

        except Exception as e:
            # 异常：仅标记 degraded，不触发熔断
            await db.execute(
                update(RouteChannel)
                .where(RouteChannel.id == channel.id)
                .values(
                    health_status="degraded",
                    last_checked_at=now,
                )
            )
            await db.commit()

    async def _handle_error(self, db, channel: RouteChannel, now: datetime):
        """处理检查失败"""
        channel.error_count += 1
        total_requests = channel.success_count + channel.error_count
        error_rate = channel.error_count / max(total_requests, 1)

        # 判断是否触发熔断
        threshold = settings.circuit_breaker_threshold  # 默认 0.5

        if error_rate >= threshold and total_requests >= 3:
            # 触发熔断（冷却期使用 aware datetime）
            cooldown_seconds = settings.circuit_breaker_cooldown_seconds
            await db.execute(
                update(RouteChannel)
                .where(RouteChannel.id == channel.id)
                .values(
                    health_status="circuit_open",
                    circuit_open_until=datetime.now(timezone.utc) + timedelta(seconds=cooldown_seconds),
                    last_checked_at=now,
                )
            )
        elif error_rate >= threshold * 0.8:
            # 接近阈值，标记为 degraded
            await db.execute(
                update(RouteChannel)
                .where(RouteChannel.id == channel.id)
                .values(
                    health_status="degraded",
                    last_checked_at=now,
                )
            )
        else:
            # 仅更新计数
            await db.execute(
                update(RouteChannel)
                .where(RouteChannel.id == channel.id)
                .values(
                    error_count=channel.error_count,
                    last_checked_at=now,
                )
            )

        await db.commit()


# 全局实例
health_checker: Optional[HealthChecker] = None


def init_health_checker(db_session_factory, interval_seconds: int = 30):
    """初始化全局健康检查器"""
    global health_checker
    health_checker = HealthChecker(db_session_factory, interval_seconds)


async def start_health_checks():
    """启动健康检查"""
    if health_checker:
        await health_checker.start()


async def stop_health_checks():
    """停止健康检查"""
    if health_checker:
        await health_checker.stop()
