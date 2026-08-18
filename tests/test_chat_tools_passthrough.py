"""
OpenAI 端点（/v1/chat/completions）工具调用透传测试

此前 ChatRequest 无 tools/tool_choice 字段、ChatMessage 无 tool_calls/tool_call_id，
Pydantic 静默丢弃 → 所有走 OpenAI 协议的客户端（Cherry Studio / OpenAI SDK）
都用不了工具调用。这里锁住整条透传链路。
"""

import pytest

from src.routers.chat import ChatRequest, _build_payload


TOOLS = [{
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "查天气",
        "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
    },
}]


class TestToolsPassthrough:
    def test_tools_reach_payload(self):
        """tools 不再被静默丢弃"""
        payload = _build_payload(ChatRequest(
            model="deepseek/deepseek-v4-pro",
            messages=[{"role": "user", "content": "北京天气"}],
            tools=TOOLS,
        ))
        assert payload["tools"] == TOOLS

    @pytest.mark.parametrize("tc", ["auto", "none", "required", {"type": "function", "function": {"name": "get_weather"}}])
    def test_tool_choice_reach_payload(self, tc):
        payload = _build_payload(ChatRequest(
            model="deepseek/deepseek-v4-pro",
            messages=[{"role": "user", "content": "hi"}],
            tools=TOOLS,
            tool_choice=tc,
        ))
        assert payload["tool_choice"] == tc

    def test_no_tools_no_field(self):
        """未传 tools 时 payload 不带该字段（不污染上游请求）"""
        payload = _build_payload(ChatRequest(
            model="deepseek/deepseek-v4-pro",
            messages=[{"role": "user", "content": "hi"}],
        ))
        assert "tools" not in payload and "tool_choice" not in payload


class TestToolMessageFields:
    def test_assistant_tool_calls_preserved(self):
        """assistant.tool_calls 不再被丢弃，且 content=null 合法"""
        calls = [{"id": "call_1", "type": "function",
                  "function": {"name": "get_weather", "arguments": '{"city":"北京"}'}}]
        payload = _build_payload(ChatRequest(
            model="deepseek/deepseek-v4-pro",
            messages=[
                {"role": "user", "content": "北京天气"},
                {"role": "assistant", "content": None, "tool_calls": calls},
            ],
        ))
        assert payload["messages"][1]["tool_calls"] == calls

    def test_tool_result_message_preserved(self):
        """role="tool" 的 tool_call_id 不再被丢弃（否则上游无法匹配调用）"""
        payload = _build_payload(ChatRequest(
            model="deepseek/deepseek-v4-pro",
            messages=[
                {"role": "user", "content": "北京天气"},
                {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "call_1", "type": "function", "function": {"name": "get_weather", "arguments": "{}"}},
                ]},
                {"role": "tool", "tool_call_id": "call_1", "content": "晴 25℃"},
            ],
        ))
        tool_msg = payload["messages"][2]
        assert tool_msg["role"] == "tool"
        assert tool_msg["tool_call_id"] == "call_1"
        assert tool_msg["content"] == "晴 25℃"

    def test_none_fields_excluded(self):
        """未设置的字段不以 null 形式发给上游（部分上游对 null 敏感）"""
        payload = _build_payload(ChatRequest(
            model="deepseek/deepseek-v4-pro",
            messages=[{"role": "user", "content": "hi"}],
        ))
        msg = payload["messages"][0]
        assert msg == {"role": "user", "content": "hi"}
        assert "tool_calls" not in msg and "tool_call_id" not in msg and "name" not in msg

    def test_multimodal_content_blocks_accepted(self):
        """content 支持 blocks 数组（多模态），此前 content: str 会校验失败"""
        blocks = [{"type": "text", "text": "这是什么"},
                  {"type": "image_url", "image_url": {"url": "https://x/y.png"}}]
        payload = _build_payload(ChatRequest(
            model="deepseek/deepseek-v4-pro",
            messages=[{"role": "user", "content": blocks}],
        ))
        assert payload["messages"][0]["content"] == blocks


def test_stabilize_payload_now_effective_on_openai_path():
    """修复后 P1 的 tools 排序在 OpenAI 路径才真正生效（此前 payload 无 tools 可排）"""
    from src.services.prefix_cache import stabilize_payload

    def _t(name):
        return {"type": "function", "function": {"name": name, "parameters": {}}}

    payload = _build_payload(ChatRequest(
        model="deepseek/deepseek-v4-pro",
        messages=[{"role": "user", "content": "hi"}],
        tools=[_t("zeta"), _t("alpha")],
    ))
    stabilize_payload(payload)
    assert [t["function"]["name"] for t in payload["tools"]] == ["alpha", "zeta"]
