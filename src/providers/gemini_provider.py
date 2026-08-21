"""
Google Gemini 供应商适配器
将 OpenAI 格式转换为 Gemini API 格式，响应转回 OpenAI 格式
"""

import json
import time
import uuid
from typing import Any, AsyncGenerator

import httpx

from src.config import settings
from loguru import logger

from src.providers.base import BaseProvider


class GeminiProvider(BaseProvider):
    """Google Gemini 系列模型适配器"""

    @property
    def name(self) -> str:
        return "gemini"

    def _url(self, model: str, stream: bool = False) -> str:
        action = "streamGenerateContent" if stream else "generateContent"
        return f"{self.base_url}/v1beta/models/{model}:{action}?key={self.api_key}"

    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json"}

    def _convert_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        """OpenAI messages → Gemini contents 格式"""
        messages = payload.get("messages", [])
        contents = []
        system_text = None

        for msg in messages:
            role = msg["role"]
            content = msg["content"]
            if role == "system":
                system_text = content
                continue
            # Gemini 的 role: user | model
            gemini_role = "model" if role == "assistant" else "user"
            contents.append({
                "role": gemini_role,
                "parts": [{"text": content}],
            })

        body: dict[str, Any] = {"contents": contents}
        if system_text:
            body["systemInstruction"] = {"parts": [{"text": system_text}]}

        gen_config: dict[str, Any] = {}
        if "temperature" in payload:
            gen_config["temperature"] = payload["temperature"]
        if "max_tokens" in payload:
            gen_config["maxOutputTokens"] = payload["max_tokens"]
        if "top_p" in payload:
            gen_config["topP"] = payload["top_p"]
        if gen_config:
            body["generationConfig"] = gen_config

        return body

    def _convert_response(self, gemini_resp: dict[str, Any], model: str) -> dict[str, Any]:
        """Gemini generateContent 响应 → OpenAI Chat Completion 格式"""
        candidates = gemini_resp.get("candidates", [])
        content_text = ""
        finish_reason = "stop"
        if candidates:
            cand = candidates[0]
            parts = cand.get("content", {}).get("parts", [])
            content_text = "".join(p.get("text", "") for p in parts)
            finish_reason = cand.get("finishReason", "STOP").lower()
            if finish_reason == "stop":
                finish_reason = "stop"
            elif finish_reason == "max_tokens":
                finish_reason = "length"

        usage_meta = gemini_resp.get("usageMetadata", {})
        prompt_tokens = usage_meta.get("promptTokenCount", 0)
        completion_tokens = usage_meta.get("candidatesTokenCount", 0)

        return {
            "id": f"chatcmpl-{uuid.uuid4().hex}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content_text},
                "finish_reason": finish_reason,
            }],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }

    async def chat_completions(
        self,
        upstream_model: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        body = self._convert_request(payload)
        request_model = payload.get("model", upstream_model)
        async with httpx.AsyncClient(timeout=self.timeout_seconds, proxy=settings.upstream_proxy or None) as client:
            try:
                resp = await client.post(
                    self._url(upstream_model),
                    headers=self._headers(),
                    json=body,
                )
                resp.raise_for_status()
                return self._convert_response(resp.json(), request_model)
            except httpx.HTTPStatusError as exc:
                logger.warning("gemini upstream error status={}", exc.response.status_code)
                try:
                    detail = exc.response.json()
                    msg = detail.get("error", {}).get("message", exc.response.text)
                except Exception:
                    msg = exc.response.text
                return self._openai_error(msg, "api_error", "upstream_error", exc.response.status_code)
            except Exception as exc:
                logger.error("gemini request failed: {}", exc)
                return self._openai_error(str(exc), "api_error", "upstream_error", 500)

    async def chat_completions_stream(
        self,
        upstream_model: str,
        payload: dict[str, Any],
    ) -> AsyncGenerator[str, None]:
        """
        Gemini SSE 流 → OpenAI SSE 格式
        Gemini 流式返回多个 JSON 对象，每个包含完整的 candidates
        """
        body = self._convert_request(payload)
        request_model = payload.get("model", upstream_model)
        chat_id = f"chatcmpl-{uuid.uuid4().hex}"
        created = int(time.time())

        async with httpx.AsyncClient(timeout=self.timeout_seconds, proxy=settings.upstream_proxy or None) as client:
            try:
                async with client.stream(
                    "POST",
                    self._url(upstream_model, stream=True),
                    headers=self._headers(),
                    json=body,
                ) as resp:
                    resp.raise_for_status()
                    buffer = ""
                    async for raw_chunk in resp.aiter_text():
                        buffer += raw_chunk
                        # Gemini 流式返回的是 JSON 数组片段，逐行处理
                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            line = line.strip().lstrip(",").lstrip("[").rstrip("]")
                            if not line or line in ("{", "}"):
                                continue
                            try:
                                event = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            candidates = event.get("candidates", [])
                            if not candidates:
                                continue
                            cand = candidates[0]
                            parts = cand.get("content", {}).get("parts", [])
                            delta_text = "".join(p.get("text", "") for p in parts)
                            finish = cand.get("finishReason", "")
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
                            if finish and finish != "FINISH_REASON_UNSPECIFIED":
                                stop_chunk = {
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
                                yield f"data: {json.dumps(stop_chunk)}\n\n"
                                yield "data: [DONE]\n\n"
                                return
                    yield "data: [DONE]\n\n"
            except httpx.HTTPStatusError as exc:
                logger.warning("gemini stream error status={}", exc.response.status_code)
                err = json.dumps({"error": {"message": "Upstream error", "type": "api_error", "code": "upstream_error"}})
                yield f"data: {err}\n\ndata: [DONE]\n\n"
            except Exception as exc:
                logger.error("gemini stream failed: {}", exc)
                err = json.dumps({"error": {"message": str(exc), "type": "api_error", "code": "upstream_error"}})
                yield f"data: {err}\n\ndata: [DONE]\n\n"

    async def image_generations(
        self,
        upstream_model: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Gemini Imagen 图像生成（基础实现）"""
        prompt = payload.get("prompt", "")
        n = payload.get("n", 1)
        body = {
            "instances": [{"prompt": prompt}],
            "parameters": {"sampleCount": n},
        }
        url = f"{self.base_url}/v1/projects/-/locations/us-central1/publishers/google/models/{upstream_model}:predict?key={self.api_key}"
        async with httpx.AsyncClient(timeout=self.timeout_seconds, proxy=settings.upstream_proxy or None) as client:
            try:
                resp = await client.post(url, headers=self._headers(), json=body)
                resp.raise_for_status()
                data = resp.json()
                predictions = data.get("predictions", [])
                result_data = []
                for pred in predictions:
                    b64 = pred.get("bytesBase64Encoded", "")
                    result_data.append({"b64_json": b64})
                return {"created": int(time.time()), "data": result_data}
            except httpx.HTTPStatusError as exc:
                logger.warning("gemini image error status={}", exc.response.status_code)
                return self._openai_error(exc.response.text, "api_error", "upstream_error", exc.response.status_code)
            except Exception as exc:
                logger.error("gemini image failed: {}", exc)
                return self._openai_error(str(exc), "api_error", "upstream_error", 500)
