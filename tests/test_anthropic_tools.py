"""
Anthropic 协议 tools 双向转换单元测试

覆盖：Anthropic tools ↔ OpenAI tools、tool_use ↔ tool_calls、tool_result ↔ tool 消息
（修复：网关此前丢失 tools，导致 Claude Code 收到上游 <tools> 标签原文）
"""

from src.routers.anthropic import (
    AnthropicMessage,
    _anthropic_tools_to_openai,
    _anthropic_tool_choice_to_openai,
    _anthropic_message_to_openai,
    _openai_message_to_anthropic_content,
    _to_anthropic_response,
)
from src.services.chat_tools import (
    chat_tool_call_element,
    chat_tool_result,
    normalize_tool_message_order,
)


def test_tools_to_openai():
    atools = [{
        "name": "bash", "description": "run cmd",
        "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}},
    }]
    out = _anthropic_tools_to_openai(atools)
    assert out[0]["type"] == "function"
    assert out[0]["function"]["name"] == "bash"
    assert out[0]["function"]["parameters"]["properties"]["command"]["type"] == "string"


def test_tool_choice_to_openai():
    assert _anthropic_tool_choice_to_openai({"type": "any"}) == "required"
    assert _anthropic_tool_choice_to_openai({"type": "tool", "name": "bash"}) == {
        "type": "function", "function": {"name": "bash"},
    }
    assert _anthropic_tool_choice_to_openai(None) is None


def test_assistant_tool_use_to_tool_calls():
    m = AnthropicMessage(role="assistant", content=[
        {"type": "tool_use", "id": "toolu_1", "name": "bash", "input": {"command": "ls"}},
    ])
    out = _anthropic_message_to_openai(m)
    assert len(out) == 1
    assert out[0]["role"] == "assistant"
    assert out[0]["tool_calls"][0]["function"]["name"] == "bash"
    assert out[0]["tool_calls"][0]["function"]["arguments"] == '{"command": "ls"}'


def test_user_tool_result_to_tool_message():
    m = AnthropicMessage(role="user", content=[
        {"type": "tool_result", "tool_use_id": "toolu_1", "content": "file1.py"},
    ])
    out = _anthropic_message_to_openai(m)
    assert len(out) == 1
    assert out[0]["role"] == "tool"
    assert out[0]["tool_call_id"] == "toolu_1"
    assert out[0]["content"] == "file1.py"


def test_user_text_and_tool_result_mixed():
    m = AnthropicMessage(role="user", content=[
        {"type": "text", "text": "继续"},
        {"type": "tool_result", "tool_use_id": "toolu_1", "content": "ok"},
    ])
    out = _anthropic_message_to_openai(m)
    # 工具结果必须紧跟 assistant(tool_calls)，文本挪到 tool 之后
    assert out[0] == {"role": "tool", "tool_call_id": "toolu_1", "content": "ok"}
    assert out[1] == {"role": "user", "content": "继续"}


def test_openai_tool_calls_to_anthropic_tool_use():
    message = {
        "content": None,
        "tool_calls": [{
            "id": "call_1", "type": "function",
            "function": {"name": "bash", "arguments": '{"command": "ls"}'},
        }],
    }
    content = _openai_message_to_anthropic_content(message)
    assert content[0]["type"] == "tool_use"
    assert content[0]["name"] == "bash"
    assert content[0]["input"] == {"command": "ls"}


def test_to_anthropic_response_stop_reason_tool_use():
    result = {
        "choices": [{
            "message": {
                "content": None,
                "tool_calls": [{
                    "id": "call_1", "type": "function",
                    "function": {"name": "bash", "arguments": '{"command": "ls"}'},
                }],
            },
            "finish_reason": "tool_calls",
        }],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
    out = _to_anthropic_response(result, "msg_1", "deepseek/deepseek-v4-pro")
    assert out["stop_reason"] == "tool_use"
    assert out["content"][0]["type"] == "tool_use"


def test_normalize_tool_message_order_fixes_user_interleaved():
    """assistant(tool_calls) 与 tool 之间插了 user 文本 → 必须重排为 tool 紧跟 assistant"""
    messages = [
        {"role": "user", "content": "查天气"},
        {"role": "assistant", "content": None, "tool_calls": [chat_tool_call_element("call_1", "get_weather", '{"city":"深圳"}')]},
        {"role": "user", "content": "继续"},
        {"role": "tool", "tool_call_id": "call_1", "content": "深圳晴 28 度"},
        {"role": "assistant", "content": "结果：深圳晴 28 度"},
    ]
    out = normalize_tool_message_order(messages)
    assert [m.get("role") for m in out] == ["user", "assistant", "tool", "user", "assistant"]
    assert out[2]["tool_call_id"] == "call_1"  # tool 紧跟 assistant


def test_normalize_tool_message_order_parallel_calls():
    """并行 tool_calls：多个 tool 消息都必须紧跟 assistant"""
    messages = [
        {"role": "assistant", "content": None, "tool_calls": [
            chat_tool_call_element("call_1", "bash", '{"command":"ls"}'),
            chat_tool_call_element("call_2", "bash", '{"command":"pwd"}'),
        ]},
        {"role": "user", "content": "中间插话"},
        {"role": "tool", "tool_call_id": "call_1", "content": "file1"},
        {"role": "tool", "tool_call_id": "call_2", "content": "/home"},
    ]
    out = normalize_tool_message_order(messages)
    assert [m.get("role") for m in out] == ["assistant", "tool", "tool", "user"]
    assert out[1]["tool_call_id"] == "call_1"
    assert out[2]["tool_call_id"] == "call_2"


def test_normalize_tool_message_order_noop_when_ordered():
    """已经有序的消息序列不应被改变（幂等）"""
    messages = [
        {"role": "assistant", "content": None, "tool_calls": [chat_tool_call_element("call_1", "bash", "{}")]},
        {"role": "tool", "tool_call_id": "call_1", "content": "out"},
        {"role": "assistant", "content": "完成"},
        {"role": "user", "content": "好的"},
    ]
    out = normalize_tool_message_order(messages)
    assert [m.get("role") for m in out] == ["assistant", "tool", "assistant", "user"]
