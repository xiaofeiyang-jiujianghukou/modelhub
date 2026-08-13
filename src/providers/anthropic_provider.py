"""
Anthropic 供应商适配器
将 OpenAI Chat Completions 格式转换为 Anthropic Messages API 格式，响应转回 OpenAI 格式
"""

import json
import time
import uuid
from typing import Any, AsyncGenerator

import httpx
from loguru import logger

from src.providers.base import BaseProvider


class AnthropicProvider(BaseProvider):
    """Anthropic Claude 系列模型适配器"""

    @property
    def name(self) -> str:
        return "anthropic"

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

    def _convert_request(self, upstream_model: str, payload: dict[str, Any]) -> dict[str, Any]:
        """
        OpenAI messages 格式 → Anthropic Messages API 格式
        - system 消息提取为顶层 system 字段
        - 其余消息保留 role/content
        """
        messages = payload.get("messages", [])
        system_content = None
        anthropic_messages = []

        for msg in messages:
            if msg["role"] == "system":
                system_content = msg["content"]
            else:
                anthropic_messages.append({
                    "role": msg["role"],
                    "content": msg["content"],
                })

        body: dict[str, Any] = {
            "model": upstream_model,
            "messages": anthropic_messages,
            "max_tokens": payload.get("max_tokens", 4096),
        }
        if system_content:
            body["system"] = system_content
        if "temperature" in payload:
            body["temperature"] = payload["temperature"]
        if "top_p" in payload:
            body["top_p"] = payload["top_p"]
        if "stop" in payload:
            stop = payload["stop"]
            body["stop_sequences"] = [stop] if isinstance(stop, str) else stop
        return body

    def _convert_response(self, anthropic_resp: dict[str, Any], model: str) -> dict[str, Any]:
        """Anthropic 响应 → OpenAI Chat Completion 格式"""
        content = ""
        if anthropic_resp.get("content"):
            content = "".join(
                block.get("text", "") for block in anthropic_resp["content"]
                if block.get("type") == "text"
            )
        usage = anthropic_resp.get("usage", {})
        return {
            "id": f"chatcmpl-{anthropic_resp.get('id', uuid.uuid4().hex)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": anthropic_resp.get("stop_reason", "stop"),
            }],
            "usage": {
                "prompt_tokens": usage.get("input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
            },
        }

    async def chat_completions(
        self,
        upstream_model: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        body = self._convert_request(upstream_model, payload)
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            try:
                resp = await client.post(
                    f"{self.base_url}/v1/messages",
                    headers=self._headers(),
                    json=body,
                )
                resp.raise_for_status()
                return self._convert_response(resp.json(), payload.get("model", upstream_model))
            except httpx.HTTPStatusError as exc:
                logger.warning("anthropic upstream error status={}", exc.response.status_code)
                try:
                    detail = exc.response.json()
                    msg = detail.get("error", {}).get("message", exc.response.text)
                except Exception:
                    msg = exc.response.text
                return self._openai_error(msg, "api_error", "upstream_error", exc.response.status_code)
            except Exception as exc:
                logger.error("anthropic request failed: {}", exc)
                return self._openai_error(str(exc), "api_error", "upstream_error", 500)

    async def chat_completions_stream(
        self,
        upstream_model: str,
        payload: dict[str, Any],
    ) -> AsyncGenerator[str, None]:
        """
        流式对话：Anthropic stream=true → 转换为 OpenAI SSE 格式
        Anthropic 事件类型：content_block_delta / message_delta / message_stop
        """
        body = {**self._convert_request(upstream_model, payload), "stream": True}
        request_model = payload.get("model", upstream_model)
        chat_id = f"chatcmpl-{uuid.uuid4().hex}"
        created = int(time.time())

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            try:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/v1/messages",
                    headers=self._headers(),
                    json=body,
                ) as resp:
                    resp.raise_for_status()
                    async for raw_line in resp.aiter_lines():
                        if not raw_line.startswith("data:"):
                            continue
                        data_str = raw_line[5:].strip()
                        if not data_str:
                            continue
                        try:
                            event = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue

                        etype = event.get("type", "")

                        if etype == "content_block_delta":
                            delta_text = event.get("delta", {}).get("text", "")
                            if delta_text:
                                chunk = {
                                    "id": chat_id,
                                    "object": "chat.completion.chunk",
                                    "created": created,
                                    "model": request_model,
                                    "choices": [{
                                        "index": 0,
                                        "delta": {"content": delta_text},
                                        "finish_reason": None,
                                    }],
                                }
                                yield f"data: {json.dumps(chunk)}\n\n"

                        elif etype == "message_stop":
                            chunk = {
                                "id": chat_id,
                                "object": "chat.completion.chunk",
                                "created": created,
                                "model": request_model,
                                "choices": [{
                                    "index": 0,
                                    "delta": {},
                                    "finish_reason": "stop",
                                }],
                            }
                            yield f"data: {json.dumps(chunk)}\n\n"
                            yield "data: [DONE]\n\n"
                            return

            except httpx.HTTPStatusError as exc:
                logger.warning("anthropic stream error status=", exc.response.status_code)
                err = json.dumps({"error": {"message": "Upstream error", "type": "api_error", "code": "upstream_error"}})
                yield f"data: {err}\n\ndata: [DONE]\n\n"
            except Exception as exc:
                logger.error("anthropic stream failed: {}", exc)
                err = json.dumps({"error": {"message": str(exc), "type": "api_error", "code": "upstream_error"}})
                yield f"data: {err}\n\ndata: [DONE]\n\n"

    async def image_generations(
        self,
        upstream_model: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Anthropic 目前不支持图像生成"""
        return self._openai_error(
            "Anthropic does not support image generation",
            "invalid_request_error",
            "unsupported_operation",
            400,
        )
