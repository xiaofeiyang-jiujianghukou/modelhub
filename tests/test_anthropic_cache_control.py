"""
Layer 2 显式缓存注入测试（Anthropic cache_control）
设计见 docs/CACHE_OPTIMIZATION_DESIGN.md B 组：命中 90% off，写 1.25x(5m)/2x(1h)
"""

import json

import pytest

from src.providers.anthropic_provider import AnthropicProvider, _convert_usage


def _provider() -> AnthropicProvider:
    return AnthropicProvider("https://api.anthropic.com", api_key="sk-test")


class TestCacheControlInjection:
    def test_system_string_becomes_block_with_cache_control(self):
        """system 字符串 → content blocks 数组 + cache_control（覆盖 tools+system 前缀）"""
        body = _provider()._convert_request("claude-opus-4-8", {
            "messages": [
                {"role": "system", "content": "你是一个专业助手。" * 50},
                {"role": "user", "content": "hi"},
            ],
        })
        assert isinstance(body["system"], list)
        assert body["system"][0]["type"] == "text"
        assert body["system"][0]["cache_control"] == {"type": "ephemeral"}

    def test_no_system_no_injection(self):
        """无 system 时不注入（messages 是易变部分，不做 breakpoint）"""
        body = _provider()._convert_request("claude-opus-4-8", {
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert "system" not in body

    def test_existing_cache_control_not_overwritten(self):
        """客户端已标记 cache_control 时不覆盖"""
        custom = {"type": "ephemeral", "ttl": "1h"}
        body = _provider()._convert_request("claude-opus-4-8", {
            "messages": [
                {"role": "system", "content": [
                    {"type": "text", "text": "a"},
                    {"type": "text", "text": "b", "cache_control": custom},
                ]},
                {"role": "user", "content": "hi"},
            ],
        })
        assert body["system"][-1]["cache_control"] == custom

    def test_marks_last_block_only(self):
        """blocks 数组只标记最后一个（单 breakpoint 拿最大前缀，上限 4 个）"""
        body = _provider()._convert_request("claude-opus-4-8", {
            "messages": [
                {"role": "system", "content": [
                    {"type": "text", "text": "first"},
                    {"type": "text", "text": "last"},
                ]},
                {"role": "user", "content": "hi"},
            ],
        })
        assert "cache_control" not in body["system"][0]
        assert body["system"][1]["cache_control"] == {"type": "ephemeral"}

    def test_ttl_1h_configurable(self, monkeypatch):
        """TTL 可配为 1h（写成本 2x，需 ≥3 次读回本）"""
        from src.config import settings
        monkeypatch.setattr(settings, "anthropic_cache_ttl", "1h")
        body = _provider()._convert_request("claude-opus-4-8", {
            "messages": [
                {"role": "system", "content": "x" * 5000},
                {"role": "user", "content": "hi"},
            ],
        })
        assert body["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}

    def test_disabled_by_config(self, monkeypatch):
        """开关关闭时不注入（一次性调用场景避免写缓存溢价）"""
        from src.config import settings
        monkeypatch.setattr(settings, "anthropic_cache_control", False)
        body = _provider()._convert_request("claude-opus-4-8", {
            "messages": [
                {"role": "system", "content": "x" * 5000},
                {"role": "user", "content": "hi"},
            ],
        })
        assert body["system"] == "x" * 5000  # 保持原样，未转 blocks


class TestConvertUsage:
    def test_prompt_tokens_includes_cache(self):
        """Anthropic input_tokens 只是未缓存部分，prompt 总量须含缓存读写"""
        usage = _convert_usage({
            "input_tokens": 100,
            "cache_read_input_tokens": 4000,
            "cache_creation_input_tokens": 500,
            "output_tokens": 50,
        })
        assert usage["prompt_tokens"] == 4600      # 100 + 4000 + 500
        assert usage["total_tokens"] == 4650
        assert usage["cache_read_input_tokens"] == 4000
        assert usage["cache_creation_input_tokens"] == 500

    def test_no_cache_fields_passthrough(self):
        """无缓存字段时不虚构（区分未上报与 0 命中）"""
        usage = _convert_usage({"input_tokens": 10, "output_tokens": 5})
        assert usage["prompt_tokens"] == 10
        assert "cache_read_input_tokens" not in usage
        assert "cache_creation_input_tokens" not in usage

    def test_layer3_parses_converted_usage(self):
        """转换后的 usage 能被 Layer 3 归一化为命中/未命中"""
        from src.services.cache_usage import extract_cache_usage, cache_hit_ratio

        usage = _convert_usage({
            "input_tokens": 100,
            "cache_read_input_tokens": 900,
            "output_tokens": 10,
        })
        hit, miss = extract_cache_usage(usage)
        assert hit == 900
        assert miss == 100          # prompt_total(1000) - hit(900)
        assert cache_hit_ratio(hit, miss) == 0.9


@pytest.mark.asyncio
async def test_stream_emits_usage_chunk_with_cache(monkeypatch):
    """流式末尾输出 usage chunk（含缓存字段）——否则流式计费与命中率拿不到数据"""
    import httpx

    events = [
        'event: message_start\ndata: {"type":"message_start","message":{"usage":'
        '{"input_tokens":50,"cache_read_input_tokens":2000,"cache_creation_input_tokens":0,"output_tokens":1}}}\n',
        'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"type":"text_delta","text":"hi"}}\n',
        'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":7}}\n',
        'event: message_stop\ndata: {"type":"message_stop"}\n',
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="".join(events))

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        "src.providers.anthropic_provider.httpx.AsyncClient",
        lambda *a, **k: real_client(transport=transport, **{**k, "proxy": None}),
    )

    chunks = []
    async for line in _provider().chat_completions_stream("claude-opus-4-8", {
        "messages": [{"role": "user", "content": "hi"}],
    }):
        chunks.append(line)

    usage_chunks = []
    for c in chunks:
        if not c.startswith("data: "):
            continue
        payload = c[6:].strip()
        if payload == "[DONE]":
            continue
        d = json.loads(payload)
        if d.get("usage"):
            usage_chunks.append(d["usage"])

    assert len(usage_chunks) == 1, chunks
    u = usage_chunks[0]
    assert u["prompt_tokens"] == 2050          # 50 未缓存 + 2000 命中
    assert u["completion_tokens"] == 7         # 来自 message_delta
    assert u["cache_read_input_tokens"] == 2000
