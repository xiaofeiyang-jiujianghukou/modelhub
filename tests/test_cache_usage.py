"""
缓存命中字段归一化测试（Layer 3 命中监控）
各家 usage 缓存字段映射见 docs/CACHE_OPTIMIZATION_DESIGN.md 第一节
"""

import pytest

from src.services.cache_usage import extract_cache_usage, cache_hit_ratio


class TestExtractCacheUsage:
    def test_deepseek_explicit_fields(self):
        """DeepSeek: prompt_cache_hit_tokens / prompt_cache_miss_tokens 显式对"""
        usage = {"prompt_tokens": 1000, "prompt_cache_hit_tokens": 800, "prompt_cache_miss_tokens": 200}
        assert extract_cache_usage(usage) == (800, 200)

    def test_deepseek_miss_fallback(self):
        """DeepSeek 只给 hit 时 miss 从 prompt_tokens 推算"""
        usage = {"prompt_tokens": 1000, "prompt_cache_hit_tokens": 800}
        assert extract_cache_usage(usage) == (800, 200)

    def test_openai_style_details(self):
        """OpenAI 系（OpenAI/GLM/Kimi/Grok/混元/方舟）: prompt_tokens_details.cached_tokens"""
        usage = {"prompt_tokens": 5000, "prompt_tokens_details": {"cached_tokens": 4000}}
        assert extract_cache_usage(usage) == (4000, 1000)

    def test_anthropic_style(self):
        """Anthropic: cache_read_input_tokens"""
        usage = {"prompt_tokens": 2000, "cache_read_input_tokens": 1500}
        assert extract_cache_usage(usage) == (1500, 500)

    def test_minimax_top_level(self):
        """MiniMax: 顶层 cached_tokens"""
        usage = {"prompt_tokens": 3000, "cached_tokens": 2000}
        assert extract_cache_usage(usage) == (2000, 1000)

    def test_zero_cached_tokens_means_no_report(self):
        """cached_tokens=0 且无其他字段 → 视为未上报（区分 0 命中与未上报由上游显式 miss 表达）"""
        usage = {"prompt_tokens": 100, "prompt_tokens_details": {"cached_tokens": 0}}
        assert extract_cache_usage(usage) == (None, None)

    def test_no_cache_fields(self):
        """无任何缓存字段 → (None, None)"""
        usage = {"prompt_tokens": 100, "completion_tokens": 50}
        assert extract_cache_usage(usage) == (None, None)

    def test_none_usage(self):
        """usage 为 None → (None, None)"""
        assert extract_cache_usage(None) == (None, None)
        assert extract_cache_usage("not-a-dict") == (None, None)


class TestCacheHitRatio:
    def test_ratio(self):
        assert cache_hit_ratio(800, 200) == 0.8
        assert cache_hit_ratio(0, 100) == 0.0
        assert cache_hit_ratio(100, 0) == 1.0

    def test_ratio_insufficient_data(self):
        assert cache_hit_ratio(None, 100) is None
        assert cache_hit_ratio(100, None) is None
        assert cache_hit_ratio(0, 0) is None


@pytest.mark.asyncio
async def test_record_log_persists_cache_fields(db_session):
    """record_log 写入 cache_hit_tokens / cache_miss_tokens 落库"""
    from src.middleware.billing import billing_service

    log = await billing_service.record_log(
        db_session,
        request_id="req-cache-test-1",
        user_id="u1",
        api_key_id=None,
        model="deepseek/deepseek-v4-pro",
        provider="deepseek",
        request_type="chat",
        status="success",
        prompt_tokens=1000,
        completion_tokens=100,
        total_tokens=1100,
        cache_hit_tokens=800,
        cache_miss_tokens=200,
    )
    assert log.cache_hit_tokens == 800
    assert log.cache_miss_tokens == 200
