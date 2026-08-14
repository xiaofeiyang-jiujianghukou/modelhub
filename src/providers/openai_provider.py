"""
OpenAI 供应商适配器
直接转发 OpenAI 格式请求，同时支持 LLM 和图像生成
"""

import json
from typing import Any, AsyncGenerator

import httpx
from loguru import logger

from src.providers.base import BaseProvider


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

        - 2xx / 404（端点存在性差异）：连通正常 → True
        - 401/403：API Key 无效 → False（避免"测试通过"假阳性）
        - 其他 4xx：连通正常 → True；5xx/异常：False
        """
        try:
            async with self._client() as client:
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
        async with self._client() as client:
            try:
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json=body,
                )
                resp.raise_for_status()
                return resp.json()
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
        """流式对话：透明代理 SSE 流"""
        body = {**payload, "model": upstream_model, "stream": True}
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
                    async for line in resp.aiter_lines():
                        if line:
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
