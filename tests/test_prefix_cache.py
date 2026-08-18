"""
前缀缓存 Layer 1 测试：tools 稳定化排序 + cache-key 注入
（docs/CACHE_OPTIMIZATION_DESIGN.md 设计原则：前缀从第 0 个 token 完全匹配才命中）
"""

import pytest

from src.services.prefix_cache import (
    stabilize_payload,
    session_cache_key,
    apply_provider_cache_optimizations,
)


def _tools(names: list[str]) -> list[dict]:
    return [{"type": "function", "function": {"name": n, "parameters": {}}} for n in names]


class TestStabilizePayload:
    def test_tools_sorted_by_name(self):
        """tools 乱序输入 → 按名字排序输出"""
        payload = {"tools": _tools(["zeta", "alpha", "middle"])}
        stabilize_payload(payload)
        assert [t["function"]["name"] for t in payload["tools"]] == ["alpha", "middle", "zeta"]

    def test_same_set_same_output_regardless_of_input_order(self):
        """核心不变量：相同集合 → 相同序列化字节（客户端顺序抖动不影响）"""
        a = {"tools": _tools(["b", "a", "c"])}
        b = {"tools": _tools(["c", "b", "a"])}
        stabilize_payload(a)
        stabilize_payload(b)
        assert a["tools"] == b["tools"]

    def test_no_tools_or_single_tool_untouched(self):
        """无 tools / 单个 tool 不动（排序无意义）"""
        p1: dict = {"messages": []}
        stabilize_payload(p1)
        assert "tools" not in p1
        p2 = {"tools": _tools(["only"])}
        stabilize_payload(p2)
        assert [t["function"]["name"] for t in p2["tools"]] == ["only"]

    def test_messages_not_modified(self):
        """稳定化只动 tools，不动 messages（历史 append-only 是前缀命中的前提）"""
        msgs = [{"role": "user", "content": "hi"}]
        payload = {"messages": msgs, "tools": _tools(["b", "a"])}
        stabilize_payload(payload)
        assert payload["messages"] is msgs


class TestSessionCacheKey:
    def test_same_session_stable_across_rounds(self):
        """同一会话跨轮次（历史追加）→ key 不变（首条 user 是锚点）"""
        round1 = {"messages": [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "第一问"},
        ]}
        round2 = {"messages": [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "第一问"},
            {"role": "assistant", "content": "答一"},
            {"role": "user", "content": "第二问"},
        ]}
        assert session_cache_key(round1) == session_cache_key(round2)

    def test_different_sessions_different_keys(self):
        a = {"messages": [{"role": "user", "content": "会话A"}]}
        b = {"messages": [{"role": "user", "content": "会话B"}]}
        assert session_cache_key(a) != session_cache_key(b)

    def test_content_blocks_supported(self):
        """content 为 block 数组（Claude Code 风格）时取 text 拼接"""
        a = {"messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]}
        b = {"messages": [{"role": "user", "content": "hi"}]}
        assert session_cache_key(a) == session_cache_key(b)


class TestApplyProviderCacheOptimizations:
    def test_inject_for_configured_vendor(self):
        """配置内的厂商（hunyuan/grok）注入 prompt_cache_key"""
        payload = {"messages": [{"role": "user", "content": "hi"}]}
        apply_provider_cache_optimizations(payload, "grok")
        assert "prompt_cache_key" in payload and len(payload["prompt_cache_key"]) == 32

    def test_not_injected_for_other_vendors(self):
        """配置外厂商不注入"""
        payload = {"messages": [{"role": "user", "content": "hi"}]}
        apply_provider_cache_optimizations(payload, "deepseek")
        assert "prompt_cache_key" not in payload

    def test_failover_cleans_stale_key(self):
        """故障转移安全：grok 失败切 deepseek 时，残留的 key 被清理"""
        payload = {"messages": [{"role": "user", "content": "hi"}]}
        apply_provider_cache_optimizations(payload, "grok")
        assert "prompt_cache_key" in payload
        apply_provider_cache_optimizations(payload, "deepseek")
        assert "prompt_cache_key" not in payload


@pytest.mark.asyncio
async def test_route_chat_stabilizes_tools(db_session, monkeypatch):
    """route_chat 链路集成：发送前 tools 已排序（payload 进入 adapter 时字节稳定）"""
    from src.db.models import Model, Provider
    from src.services.router import router_service
    from src.providers.openai_provider import OpenAIProvider

    captured: dict = {}

    async def fake_chat(self, upstream_model, payload):
        captured["tools"] = list(payload.get("tools", []))
        return {"id": "x", "choices": [{"message": {"content": "ok"}}], "usage": {}}

    monkeypatch.setattr(OpenAIProvider, "chat_completions", fake_chat)

    p = Provider(name="deepseek", base_url="https://api.deepseek.com/v1",
                 auth_type="bearer", credentials_enc='{"api_key":"sk-x"}', timeout_ms=30000)
    m = Model(model="stable-test-model", vendor="deepseek", display_name="t",
              owned_by="deepseek", model_type="llm", upstream_model="stable-test-model",
              route_strategy="priority", is_active=True)
    db_session.add_all([p, m])
    await db_session.commit()

    payload = {
        "model": "stable-test-model",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": _tools(["z_tool", "a_tool"]),
    }
    result, _, _, err = await router_service.route_chat(db_session, "deepseek/stable-test-model", payload)
    assert result is not None
    assert [t["function"]["name"] for t in captured["tools"]] == ["a_tool", "z_tool"]
