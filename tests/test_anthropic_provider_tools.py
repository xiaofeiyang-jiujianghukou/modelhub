"""
Anthropic 适配器工具调用往返测试
网关是「OpenAI 中间格式 ↔ Anthropic 特有格式」的转换层，两个方向都要通：
- 请求：chat tools → input_schema；assistant.tool_calls → tool_use；role=tool → tool_result
- 响应：tool_use → tool_calls；stop_reason=tool_use → finish_reason=tool_calls
"""

import json

import pytest

from src.providers.anthropic_provider import AnthropicProvider


def _provider() -> AnthropicProvider:
    return AnthropicProvider("https://api.anthropic.com", api_key="sk-test")


OPENAI_TOOLS = [{
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "查询天气",
        "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
    },
}]


class TestToolsRequestConversion:
    def test_tools_to_input_schema(self):
        """chat tools → Anthropic tools（parameters → input_schema）"""
        body = _provider()._convert_request("claude-opus-4-8", {
            "messages": [{"role": "user", "content": "北京天气"}],
            "tools": OPENAI_TOOLS,
        })
        assert body["tools"] == [{
            "name": "get_weather",
            "description": "查询天气",
            "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
        }]

    @pytest.mark.parametrize("openai_tc,expected", [
        ("auto", {"type": "auto"}),
        ("none", {"type": "none"}),
        ("required", {"type": "any"}),
        ({"type": "function", "function": {"name": "get_weather"}}, {"type": "tool", "name": "get_weather"}),
    ])
    def test_tool_choice_mapping(self, openai_tc, expected):
        body = _provider()._convert_request("claude-opus-4-8", {
            "messages": [{"role": "user", "content": "hi"}],
            "tools": OPENAI_TOOLS,
            "tool_choice": openai_tc,
        })
        assert body["tool_choice"] == expected

    def test_no_tools_no_field(self):
        """无 tools 时不带 tools/tool_choice 字段"""
        body = _provider()._convert_request("claude-opus-4-8", {
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert "tools" not in body and "tool_choice" not in body

    def test_assistant_tool_calls_to_tool_use(self):
        """assistant.tool_calls → tool_use blocks（arguments JSON 串 → input 对象）"""
        body = _provider()._convert_request("claude-opus-4-8", {
            "messages": [
                {"role": "user", "content": "北京天气"},
                {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "call_1", "type": "function",
                    "function": {"name": "get_weather", "arguments": '{"city":"北京"}'},
                }]},
            ],
        })
        blocks = body["messages"][1]["content"]
        assert body["messages"][1]["role"] == "assistant"
        assert blocks == [{"type": "tool_use", "id": "call_1", "name": "get_weather", "input": {"city": "北京"}}]

    def test_tool_message_to_tool_result(self):
        """role="tool" → user 消息里的 tool_result（Anthropic 不认识 role=tool）"""
        body = _provider()._convert_request("claude-opus-4-8", {
            "messages": [
                {"role": "user", "content": "北京天气"},
                {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "call_1", "type": "function",
                    "function": {"name": "get_weather", "arguments": "{}"},
                }]},
                {"role": "tool", "tool_call_id": "call_1", "content": "晴 25℃"},
            ],
        })
        assert not any(m["role"] == "tool" for m in body["messages"])
        assert body["messages"][2] == {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "call_1", "content": "晴 25℃"},
        ]}

    def test_parallel_tool_results_merged_into_one_user_message(self):
        """并行工具结果必须合并进同一条 user 消息（拆开会被 Anthropic 判为不匹配）"""
        body = _provider()._convert_request("claude-opus-4-8", {
            "messages": [
                {"role": "user", "content": "两地天气"},
                {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "c1", "type": "function", "function": {"name": "get_weather", "arguments": "{}"}},
                    {"id": "c2", "type": "function", "function": {"name": "get_weather", "arguments": "{}"}},
                ]},
                {"role": "tool", "tool_call_id": "c1", "content": "北京晴"},
                {"role": "tool", "tool_call_id": "c2", "content": "上海雨"},
            ],
        })
        tool_msgs = [m for m in body["messages"] if m["role"] == "user" and isinstance(m["content"], list)]
        assert len(tool_msgs) == 1
        assert [b["tool_use_id"] for b in tool_msgs[0]["content"]] == ["c1", "c2"]

    def test_malformed_arguments_degrade_to_empty(self):
        """arguments 非法 JSON 时降级为空 input，不抛异常"""
        body = _provider()._convert_request("claude-opus-4-8", {
            "messages": [
                {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "c1", "type": "function",
                    "function": {"name": "f", "arguments": "{不是json"},
                }]},
            ],
        })
        assert body["messages"][0]["content"][0]["input"] == {}


class TestToolsResponseConversion:
    def test_tool_use_to_tool_calls(self):
        """Anthropic tool_use → OpenAI tool_calls，stop_reason → finish_reason=tool_calls"""
        out = _provider()._convert_response({
            "id": "msg_1",
            "content": [
                {"type": "text", "text": "我查一下"},
                {"type": "tool_use", "id": "toolu_1", "name": "get_weather", "input": {"city": "北京"}},
            ],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }, "claude-opus-4-8")

        choice = out["choices"][0]
        assert choice["finish_reason"] == "tool_calls"
        tc = choice["message"]["tool_calls"][0]
        assert tc["id"] == "toolu_1"
        assert tc["function"]["name"] == "get_weather"
        assert json.loads(tc["function"]["arguments"]) == {"city": "北京"}
        assert choice["message"]["content"] == "我查一下"

    @pytest.mark.parametrize("stop_reason,expected", [
        ("end_turn", "stop"),
        ("stop_sequence", "stop"),
        ("max_tokens", "length"),
        ("tool_use", "tool_calls"),
        ("refusal", "content_filter"),
    ])
    def test_finish_reason_map(self, stop_reason, expected):
        out = _provider()._convert_response(
            {"content": [{"type": "text", "text": "x"}], "stop_reason": stop_reason, "usage": {}},
            "claude-opus-4-8",
        )
        assert out["choices"][0]["finish_reason"] == expected

    def test_plain_text_has_no_tool_calls(self):
        out = _provider()._convert_response(
            {"content": [{"type": "text", "text": "hello"}], "stop_reason": "end_turn", "usage": {}},
            "claude-opus-4-8",
        )
        assert "tool_calls" not in out["choices"][0]["message"]
        assert out["choices"][0]["message"]["content"] == "hello"


@pytest.mark.asyncio
async def test_stream_tool_use_to_openai_delta(monkeypatch):
    """流式 tool_use：content_block_start + input_json_delta → OpenAI tool_calls delta"""
    import httpx

    events = [
        'data: {"type":"message_start","message":{"usage":{"input_tokens":20,"output_tokens":1}}}\n',
        'data: {"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"toolu_9","name":"get_weather","input":{}}}\n',
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\\"city\\":"}}\n',
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"\\"北京\\"}"}}\n',
        'data: {"type":"content_block_stop","index":0}\n',
        'data: {"type":"message_delta","delta":{"stop_reason":"tool_use"},"usage":{"output_tokens":12}}\n',
        'data: {"type":"message_stop"}\n',
    ]

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda req: httpx.Response(200, text="".join(events)))
    monkeypatch.setattr(
        "src.providers.anthropic_provider.httpx.AsyncClient",
        lambda *a, **k: real_client(transport=transport, *a, **k),
    )

    parsed = []
    async for line in _provider().chat_completions_stream("claude-opus-4-8", {
        "messages": [{"role": "user", "content": "北京天气"}],
        "tools": OPENAI_TOOLS,
    }):
        if line.startswith("data: ") and line[6:].strip() != "[DONE]":
            parsed.append(json.loads(line[6:].strip()))

    # 首个 tool_calls delta 带 id + name
    starts = [d for d in parsed if d.get("choices") and (d["choices"][0]["delta"].get("tool_calls") or [{}])[0].get("id")]
    assert len(starts) == 1
    first = starts[0]["choices"][0]["delta"]["tool_calls"][0]
    assert first["index"] == 0
    assert first["id"] == "toolu_9"
    assert first["function"]["name"] == "get_weather"

    # arguments 增量拼接后是完整 JSON
    args = "".join(
        tc["function"].get("arguments", "")
        for d in parsed if d.get("choices")
        for tc in (d["choices"][0]["delta"].get("tool_calls") or [])
    )
    assert json.loads(args) == {"city": "北京"}

    # finish_reason 报 tool_calls
    finishes = [d["choices"][0]["finish_reason"] for d in parsed if d.get("choices") and d["choices"][0].get("finish_reason")]
    assert finishes == ["tool_calls"]
