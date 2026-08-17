"""
OpenAI 供应商适配器
直接转发 OpenAI 格式请求，同时支持 LLM 和图像生成
"""

import json
from typing import Any, AsyncGenerator

import httpx
from loguru import logger

from src.providers.base import BaseProvider


# reasoning_content 透传缓存：tool_call_id → reasoning_content
# DeepSeek thinking mode 要求多轮对话把上一轮 assistant 的 reasoning_content 原样传回，
# 但 Anthropic/Responses 协议无此字段，客户端不会回传。网关在此缓存（响应方向），
# 下一轮请求按 tool_call_id 补回（请求方向），对客户端透明，保持模型完整思考能力。
_reasoning_cache: dict[str, str] = {}


def _restore_reasoning(messages: list[dict]) -> None:
    """请求方向：按 assistant 消息的首个 tool_call id 补回缓存的 reasoning_content（原地修改）"""
    for m in messages:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            tc_id = m["tool_calls"][0].get("id", "")
            if tc_id and tc_id in _reasoning_cache:
                m["reasoning_content"] = _reasoning_cache[tc_id]


def _cache_reasoning_from_message(message: dict) -> None:
    """响应方向（非流式）：把 assistant 消息的 reasoning_content 按 tool_call id 缓存"""
    reasoning = message.get("reasoning_content")
    if not reasoning:
        return
    for tc in message.get("tool_calls") or []:
        tc_id = tc.get("id", "")
        if tc_id:
            _reasoning_cache[tc_id] = reasoning


class OpenAIProvider(BaseProvider):
    """OpenAI 官方 API 适配器（直接转发，格式已兼容）"""

    def __init__(self, base_url: str, api_key: str, timeout_seconds: float = 30.0):
        super().__init__(base_url, api_key, timeout_seconds)
        # base_url 为完整 API 根路径（如 https://api.deepseek.com/v1 或 https://open.bigmodel.cn/api/paas/v4）

    @property
    def name(self) -> str:
        return "openai"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _client(self) -> httpx.AsyncClient:
        """创建 HTTP 客户端，忽略系统代理（trust_env=False）"""
        return httpx.AsyncClient(timeout=self.timeout_seconds, trust_env=False)

    async def health_check(self) -> bool:
        """健康检查：请求 /models 端点验证连通性

        - 未配置 key：直接 False，不发网络请求（上游必拒，省去网络等待）
        - 2xx / 404（端点存在性差异）：连通正常 → True
        - 401/403：API Key 无效 → False（避免"测试通过"假阳性）
        - 其他 4xx：连通正常 → True；5xx/异常：False
        - 探测用短超时（≤5s）：连通性测试不该等业务 60s 超时（国内直连被墙时秒回失败）
        """
        if not self.api_key:
            return False
        try:
            probe_timeout = min(self.timeout_seconds, 5.0)
            async with httpx.AsyncClient(timeout=probe_timeout, trust_env=False) as client:
                resp = await client.get(
                    f"{self.base_url}/models",
                    headers=self._headers(),
                )
                if resp.status_code in (401, 403):
                    return False
                return resp.status_code < 500
        except Exception:
            return False

    async def chat_completions(
        self,
        upstream_model: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """非流式对话：直接转发至 OpenAI"""
        body = {**payload, "model": upstream_model, "stream": False}
        _restore_reasoning(body.get("messages", []))  # 多轮：按 tool_call_id 补回 reasoning_content
        async with self._client() as client:
            try:
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json=body,
                )
                resp.raise_for_status()
                result = resp.json()
                # 缓存本轮 assistant 的 reasoning_content（供下一轮补回）
                msg = (result.get("choices") or [{}])[0].get("message", {})
                _cache_reasoning_from_message(msg)
                return result
            except httpx.HTTPStatusError as exc:
                logger.warning(
                    "openai upstream error status={} body={}",
                    exc.response.status_code,
                    exc.response.text[:200],
                )
                try:
                    err_body = exc.response.json()
                except Exception:
                    err_body = {"error": {"message": exc.response.text, "type": "api_error", "code": "upstream_error"}}
                err_body["_status_code"] = exc.response.status_code
                return err_body
            except Exception as exc:
                logger.error("openai request failed: {}", exc)
                return self._openai_error(str(exc), "api_error", "upstream_error", 500)

    async def chat_completions_stream(
        self,
        upstream_model: str,
        payload: dict[str, Any],
    ) -> AsyncGenerator[str, None]:
        """流式对话：透明代理 SSE 流（顺带累积 reasoning_content 缓存，不改 SSE 内容）"""
        body = {**payload, "model": upstream_model, "stream": True}
        # 要求上游在流末尾返回 usage（OpenAI 兼容，DeepSeek/方舟等支持），网关据此计费
        if "stream_options" not in body:
            body["stream_options"] = {"include_usage": True}
        _restore_reasoning(body.get("messages", []))  # 多轮：按 tool_call_id 补回 reasoning_content
        async with self._client() as client:
            try:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json=body,
                ) as resp:
                    # 非 2xx：在流关闭前读取 body，携带真实错误消息
                    if resp.status_code >= 400:
                        err_body: dict = {}
                        try:
                            raw = await resp.aread()
                            err_body = json.loads(raw)
                        except Exception:
                            pass
                        msg = err_body.get("error", {}).get("message", "Upstream error")
                        code = err_body.get("error", {}).get("code", "upstream_error")
                        err = json.dumps({
                            "error": {"message": msg, "type": "upstream_error", "code": code},
                            "_status_code": resp.status_code,
                        })
                        # 拆行 yield：每行一个 data 块（合并会导致上层 JSON 解析失败）
                        yield f"data: {err}\n\n"
                        yield f"data: [DONE]\n\n"
                        return
                    reasoning_parts: list[str] = []
                    cached_ids: set[str] = set()
                    async for line in resp.aiter_lines():
                        if line:
                            # 累积 reasoning_content，首个 tool_call id 出现时缓存（供下一轮补回）
                            if line.startswith("data: ") and not line.startswith("data: [DONE]"):
                                try:
                                    chunk = json.loads(line[6:].strip())
                                    delta = (chunk.get("choices") or [{}])[0].get("delta", {})
                                    if delta.get("reasoning_content"):
                                        reasoning_parts.append(delta["reasoning_content"])
                                    for tc in delta.get("tool_calls") or []:
                                        tc_id = tc.get("id")
                                        if tc_id and tc_id not in cached_ids and reasoning_parts:
                                            _reasoning_cache[tc_id] = "".join(reasoning_parts)
                                            cached_ids.add(tc_id)
                                except Exception:
                                    pass
                            yield line + "\n"
            except Exception as exc:
                logger.error("openai stream failed: {}", exc)
                err = json.dumps({"error": {"message": str(exc), "type": "api_error", "code": "upstream_error"}})
                yield f"data: {err}\n\n"
                yield f"data: [DONE]\n\n"

    async def image_generations(
        self,
        upstream_model: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """图像生成：直接转发至 OpenAI Images API"""
        body = {**payload, "model": upstream_model}
        async with self._client() as client:
            try:
                resp = await client.post(
                    f"{self.base_url}/images/generations",
                    headers=self._headers(),
                    json=body,
                )
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as exc:
                logger.warning("openai image error status={}", exc.response.status_code)
                try:
                    err_body = exc.response.json()
                except Exception:
                    err_body = {"error": {"message": exc.response.text, "type": "api_error", "code": "upstream_error"}}
                err_body["_status_code"] = exc.response.status_code
                return err_body
            except Exception as exc:
                logger.error("openai image request failed: {}", exc)
                return self._openai_error(str(exc), "api_error", "upstream_error", 500)
