"""
路由引擎单元测试
覆盖：通道选择策略、故障转移、熔断、全通道不可用
"""

import pytest
from sqlalchemy import select

from src.models import Provider, RouteChannel
from src.services.router import (
    RouterService, _select_channel, _weighted_random, _mark_channel_error,
)

router_service = RouterService()


# ── 加权随机选择 ───────────────────────────────────────────────────────────────

class TestWeightedRandom:
    def test_single_channel(self):
        """单通道总是被选中"""
        from unittest.mock import MagicMock
        channel = MagicMock()
        channel.weight = 100
        assert _weighted_random([channel]) is channel

    def test_empty(self):
        """空列表返回 None"""
        assert _weighted_random([]) is None

    def test_distribution(self):
        """权重分布：高权重通道被选中次数更多（100次采样）"""
        from unittest.mock import MagicMock
        heavy = MagicMock()
        heavy.weight = 90
        heavy.id = "heavy"
        light = MagicMock()
        light.weight = 10
        light.id = "light"

        heavy_count = 0
        for _ in range(1000):
            chosen = _weighted_random([heavy, light])
            if chosen.id == "heavy":
                heavy_count += 1
        # 90% 权重大约选中 900 次（允许 ±10% 波动）
        assert 800 <= heavy_count <= 980


# ── 通道选择（跳过不可用通道）────────────────────────────────────────────────

class TestSelectChannel:
    def test_skips_inactive(self):
        """非活跃通道被跳过"""
        from datetime import datetime, timezone
        from unittest.mock import MagicMock
        inactive = MagicMock()
        inactive.is_active = False
        inactive.health_status = "healthy"
        result = _select_channel([inactive], "priority")
        assert result is None

    def test_skips_circuit_open(self):
        """熔断中的通道被跳过"""
        from datetime import datetime, timedelta, timezone
        from unittest.mock import MagicMock
        open_ch = MagicMock()
        open_ch.is_active = True
        open_ch.health_status = "circuit_open"
        open_ch.circuit_open_until = datetime.now(timezone.utc) + timedelta(minutes=5)
        result = _select_channel([open_ch], "priority")
        assert result is None


# ── 故障转移 ───────────────────────────────────────────────────────────────────

class TestFailover:
    @pytest.mark.asyncio
    async def test_route_fails_over_to_backup(self, db_session, seed_model):
        """主通道失败时自动切换到备用通道"""
        from sqlalchemy import select as sa_select
        from src.models import RouteChannel as RC

        # 查询主通道（避免 lazy-load）
        primary_result = await db_session.execute(
            sa_select(RC).where(RC.model_id == seed_model.id)
        )
        primaries = primary_result.scalars().all()
        assert len(primaries) == 1
        primary = primaries[0]

        # 添加第二个通道（备用）
        result = await db_session.execute(select(Provider).where(Provider.name == "mock"))
        provider = result.scalar_one()
        backup = RouteChannel(
            model_id=seed_model.id,
            provider_id=provider.id,
            upstream_model=seed_model.id,
            weight=50,
            priority=50,  # 低优先级，作为备用
        )
        db_session.add(backup)
        await db_session.commit()

        # 模拟主通道熔断（health_status=circuit_open + 冷却期）
        from datetime import datetime, timedelta, timezone
        primary.health_status = "circuit_open"
        primary.circuit_open_until = datetime.now(timezone.utc) + timedelta(minutes=5)
        await db_session.commit()

        # 路由应该跳过主通道，选择备用通道
        model_id = await router_service.resolve_model_id(db_session, seed_model.id)
        channels, strategy = await router_service.get_channels(db_session, model_id)
        assert len(channels) == 2

        selected = _select_channel(channels, strategy)
        assert selected is not None
        assert selected.id == backup.id  # 备用通道被选中


# ── 熔断器 ─────────────────────────────────────────────────────────────────────

class TestCircuitBreaker:
    def test_opens_after_high_error_rate(self):
        """错误率超过阈值触发熔断"""
        from unittest.mock import MagicMock
        channel = MagicMock()
        channel.error_count = 4
        channel.success_count = 1  # 错误率 80% > 50%
        channel.health_status = "healthy"
        channel.circuit_open_until = None

        _mark_channel_error(channel)
        assert channel.health_status == "circuit_open"
        assert channel.circuit_open_until is not None

    def test_stays_closed_low_error_rate(self):
        """错误率低于阈值不熔断"""
        from unittest.mock import MagicMock
        channel = MagicMock()
        channel.error_count = 1
        channel.success_count = 9  # 错误率 10% < 50%
        channel.health_status = "healthy"
        channel.circuit_open_until = None

        _mark_channel_error(channel)
        assert channel.health_status == "healthy"
