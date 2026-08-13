"""
Mock 供应商 - 用于测试和演示
返回模拟响应，不需要真实 API Key
"""

import asyncio
from typing import Any, AsyncGenerator

from src.providers.base import BaseProvider


class MockProvider(BaseProvider):
    """Mock 供应商，返回模拟数据"""

    def __init__(self, base_url: str = "https://mock.internal", api_key: str = "mock", timeout_seconds: float = 30.0):
        super().__init__(base_url, api_key, timeout_seconds)

    @property
    def name(self) -> str:
        return "mock"

    async def chat_completions(
        self,
        upstream_model: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """模拟聊天响应"""
        # 提取用户消息
        user_message = ""
        if "messages" in payload:
            for msg in reversed(payload["messages"]):
                if msg.get("role") == "user":
                    user_message = msg.get("content", "")
                    break

        # 模拟 token 计数
        prompt_tokens = len(user_message.split())
        max_tokens = payload.get("max_tokens") or 100  # 默认 100，避免 None
        completion_tokens = min(max_tokens, 50)
        total_tokens = prompt_tokens + completion_tokens

        return {
            "id": f"chatcmpl-mock-{upstream_model}",
            "object": "chat.completion",
            "created": 1234567890,
            "model": upstream_model,
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": f"[Mock Response for {upstream_model}]\n\n你好！这是一个模拟响应。你的消息是：\n\n\"{user_message}\"\n\n当前使用 Mock 模式，无需真实 API Key 即可测试网关功能。",
                },
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            },
        }

    async def chat_completions_stream(
        self,
        upstream_model: str,
        payload: dict[str, Any],
    ) -> AsyncGenerator[str, None]:
        """模拟流式聊天响应"""
        # 提取用户消息
        user_message = ""
        if "messages" in payload:
            for msg in reversed(payload["messages"]):
                if msg.get("role") == "user":
                    user_message = msg.get("content", "")
                    break

        # 模拟流式输出
        response_text = f"[Mock Stream for {upstream_model}]\n\n你好！这是一个模拟流式响应。你的消息是：\n\n\"{user_message}\"\n\n当前使用 Mock 模式。"

        words = response_text.split()
        for i, word in enumerate(words):
            chunk = {
                "id": f"chatcmpl-mock-{upstream_model}",
                "object": "chat.completion.chunk",
                "created": 1234567890,
                "model": upstream_model,
                "choices": [{
                    "index": 0,
                    "delta": {"content": word + " "},
                    "finish_reason": None,
                }],
            }
            yield f"data: {__import__('json').dumps(chunk)}\n\n"
            await asyncio.sleep(0.05)  # 模拟网络延迟

        # 最后一个 chunk
        final_chunk = {
            "id": f"chatcmpl-mock-{upstream_model}",
            "object": "chat.completion.chunk",
            "created": 1234567890,
            "model": upstream_model,
            "choices": [{
                "index": 0,
                "delta": {},
                "finish_reason": "stop",
            }],
        }
        yield f"data: {__import__('json').dumps(final_chunk)}\n\n"
        yield "data: [DONE]\n\n"

    async def image_generations(
        self,
        upstream_model: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """模拟图像生成"""
        prompt = payload.get("prompt", "")
        n = payload.get("n", 1)

        data = []
        for i in range(n):
            data.append({
                "url": f"https://via.placeholder.com/1024x1024/0066cc/ffffff?text=Mock+Image+{i+1}",
                "revised_prompt": prompt,
            })

        return {
            "created": 1234567890,
            "data": data,
        }

    # 兼容旧接口（chat 方法名）
    async def chat(self, request, req_id: str):
        """旧版 chat 方法（兼容）"""
        payload = {
            "messages": [m.dict() for m in request.messages],
            "max_tokens": request.max_tokens,
        }
        result = await self.chat_completions(request.model, payload)
        return type("Response", (), {
            "choices": result["choices"],
            "usage": result["usage"],
        })()

    async def stream_chat(self, request, req_id: str):
        """旧版 stream_chat 方法（兼容）"""
        payload = {
            "messages": [m.dict() for m in request.messages],
        }
        async for chunk in self.chat_completions_stream(request.model, payload):
            # 解析 SSE 格式的 chunk，提取 choices[0]
            import json
            if chunk.startswith("data: "):
                data_str = chunk[6:]  # Remove "data: "
                if data_str == "[DONE]\n\n":
                    yield {"index": 0, "delta": {}, "finish_reason": "stop"}
                else:
                    data = json.loads(data_str.strip())
                    if "choices" in data and data["choices"]:
                        yield data["choices"][0]

    async def generate_image(self, request, req_id: str):
        """旧版 generate_image 方法（兼容）"""
        payload = {
            "prompt": request.prompt,
            "n": request.n,
            "size": request.size,
            "quality": request.quality,
        }
        result = await self.image_generations(request.model, payload)
        return type("Response", (), {"data": result["data"]})()
