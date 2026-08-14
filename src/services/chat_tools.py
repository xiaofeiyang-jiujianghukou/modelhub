"""
统一工具调用中间格式（Claude Code / Codex / 未来协议共用）

中间格式 = chat/completions 的 tools 约定（单一数据源，避免「改了 A 漏了 B」）：
- 工具定义: {"type": "function", "function": {"name", "description", "parameters"}}
- 工具调用: assistant 消息 tool_calls = [{"id", "type": "function", "function": {"name", "arguments"}}]
- 工具结果: {"role": "tool", "tool_call_id", "content"}

各协议适配器（anthropic.py / responses.py）只负责：
1. 请求方向：把协议特有的 tools / tool_use / tool_result（或 function_call / function_call_output）
   解析成上述中间格式
2. 响应方向：把 chat 的 tool_calls 反序列化成协议特有的 tool_use / function_call
"""

from typing import Optional


def chat_tool(name: str, description: str = "", schema: Optional[dict] = None) -> dict:
    """构建 chat 工具定义（schema 为 JSON Schema）"""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description or "",
            "parameters": schema or {"type": "object"},
        },
    }


def chat_tool_call_element(call_id: str, name: str, arguments: str) -> dict:
    """构建 tool_calls 的单个元素（arguments 为 JSON 字符串）"""
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def chat_assistant_tool_calls(calls: list[dict]) -> dict:
    """构建 assistant 消息（含 tool_calls）；calls = [{id, name, arguments(JSON str)}]"""
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            chat_tool_call_element(c.get("id", ""), c.get("name", ""), c.get("arguments", "{}"))
            for c in calls
        ],
    }


def chat_tool_result(tool_call_id: str, content: str) -> dict:
    """构建 tool 消息（工具执行结果）"""
    return {"role": "tool", "tool_call_id": tool_call_id, "content": content}


def parse_chat_tool_calls(message: dict) -> list[dict]:
    """解析 chat message 的 tool_calls → [{id, name, arguments}]"""
    result = []
    for tc in message.get("tool_calls") or []:
        fn = tc.get("function") or {}
        result.append({
            "id": tc.get("id", ""),
            "name": fn.get("name", ""),
            "arguments": fn.get("arguments", ""),
        })
    return result


def normalize_tool_message_order(messages: list[dict]) -> list[dict]:
    """
    规范化 tool 消息顺序（发送上游前兜底，OpenAI/DeepSeek 要求）：

    assistant 带 tool_calls 的消息后必须紧跟对应的 role=tool 消息，
    中间不能插入 user/system/assistant(空) 等消息，否则上游报
    "An assistant message with 'tool_calls' must be followed by tool messages
     responding to each 'tool_call_id'. (insufficient tool messages...)"。

    来源：Anthropic/Responses 转换时若用户文本与 tool_result 混在同一条消息，
    可能把 user 文本插到 tool 之前。此函数把非 tool 消息整体挪到工具结果之后。

    返回新列表，不修改入参。
    """
    result: list[dict] = []
    i = 0
    n = len(messages)
    while i < n:
        m = messages[i]
        if m.get("role") == "assistant" and m.get("tool_calls"):
            # 收集本 assistant 之后、下一个 assistant(tool_calls) 之前的消息
            tools: list[dict] = []
            others: list[dict] = []
            j = i + 1
            while j < n:
                nxt = messages[j]
                if nxt.get("role") == "assistant" and nxt.get("tool_calls"):
                    break  # 下一个工具调用组，留给外层处理
                if nxt.get("role") == "tool":
                    tools.append(nxt)
                else:
                    others.append(nxt)
                j += 1
            result.append(m)
            result.extend(tools)   # 工具结果紧跟 assistant(tool_calls)
            result.extend(others)  # 文本等消息挪到工具结果之后
            i = j
        else:
            result.append(m)
            i += 1
    return result
