"""
路由引擎单元测试
覆盖：通道选择策略、故障转移、熔断、全通道不可用
"""

import pytest
from sqlalchemy import select

from src.db.models import Model, Provider
from src.services.router import (
    RouterService, _select_channel, _weighted_random, _mark_channel_error,
)
from src.services.model_key import parse_model_key, format_model_key, strip_context_suffix

router_service = RouterService()


# ── 模型键解析（厂商/模型 新格式 + @ 旧格式兼容 + [1M] 后缀剥离）──────────────

class TestParseModelKey:
    def test_vendor_first_slash(self):
        """新格式：厂商/模型"""
        assert parse_model_key("deepseek/deepseek-v4-pro") == ("deepseek-v4-pro", "deepseek")

    def test_vendor_first_slash_with_context_suffix(self):
        """新格式：厂商/模型[1M] 剥离上下文后缀"""
        assert parse_model_key("deepseek/deepseek-v4-flash[1M]") == ("deepseek-v4-flash", "deepseek")
        assert parse_model_key("deepseek/deepseek-v4-flash[1m]") == ("deepseek-v4-flash", "deepseek")
        assert parse_model_key("ark/glm-5.2[128K]") == ("glm-5.2", "ark")

    def test_legacy_at_format(self):
        """旧格式兼容：模型@厂商"""
        assert parse_model_key("deepseek-v4-pro@deepseek") == ("deepseek-v4-pro", "deepseek")

    def test_bare_name(self):
        """裸模型名 → 无厂商"""
        assert parse_model_key("glm-5.2") == ("glm-5.2", None)

    def test_format_model_key(self):
        """对外唯一键 = 厂商/模型"""
        assert format_model_key("deepseek-v4-pro", "deepseek") == "deepseek/deepseek-v4-pro"

    def test_strip_context_suffix(self):
        assert strip_context_suffix("deepseek-v4-flash[1M]") == "deepseek-v4-flash"


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


# ── 精确路由（vendor/model 唯一确定一条路）──────────────────────────────────

class TestPreciseRouting:
    @pytest.mark.asyncio
    async def test_precise_vendor_model_returns_only_that_channel(self, db_session, seed_model):
        """'厂商/模型' 唯一键只返回该厂商通道，不会跨厂商兜底"""
        from src.services.model_key import format_model_key

        # 添加另一个厂商的同模型名记录
        backup_provider = Provider(name="mock2", base_url="https://mock2.internal", auth_type="bearer", credentials_enc="{}")
        db_session.add(backup_provider)
        await db_session.flush()
        backup = Model(
            model=seed_model.model, vendor="mock2", model_type="llm",
            upstream_model=seed_model.model, weight=50, priority=50,
        )
        db_session.add(backup)
        await db_session.commit()

        channels, strategy = await router_service.get_channels(
            db_session, format_model_key(seed_model.model, "mock")
        )
        assert len(channels) == 1
        assert channels[0].vendor == "mock"  # 只路由用户指定的厂商

    @pytest.mark.asyncio
    async def test_unknown_vendor_returns_empty(self, db_session, seed_model):
        """厂商不存在时返回空通道（由调用方透传上游错误）"""
        from src.services.model_key import format_model_key
        channels, _ = await router_service.get_channels(
            db_session, format_model_key(seed_model.model, "no-such-vendor")
        )
        assert channels == []


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
