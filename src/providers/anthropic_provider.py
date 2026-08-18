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

from src.config import settings
from src.providers.base import BaseProvider


def _convert_usage(usage: dict[str, Any]) -> dict[str, Any]:
    """Anthropic usage → OpenAI usage，保留缓存字段。

    Anthropic 的 input_tokens 只是「未缓存部分」，完整 prompt 规模为
        input_tokens + cache_creation_input_tokens + cache_read_input_tokens
    只取 input_tokens 会在命中缓存时严重少算（命中 90% 就少算 90%），
    导致计费与日志失真、命中率无法计算。这里合计为 prompt_tokens，
    并透传两个缓存字段供 services/cache_usage.py 归一化入库（Layer 3）。

    注意：命中部分上游实价 0.1×、写缓存 1.25×，网关当前按统一单价计费，
    分档计价（读/写/未缓存不同价）是后续改进项。
    """
    uncached = usage.get("input_tokens", 0) or 0
    cache_read = usage.get("cache_read_input_tokens", 0) or 0
    cache_write = usage.get("cache_creation_input_tokens", 0) or 0
    completion = usage.get("output_tokens", 0) or 0
    prompt_total = uncached + cache_read + cache_write

    out: dict[str, Any] = {
        "prompt_tokens": prompt_total,
        "completion_tokens": completion,
        "total_tokens": prompt_total + completion,
    }
    # 缓存字段原样透传（cache_usage.extract_cache_usage 解析 cache_read 为命中）
    if "cache_read_input_tokens" in usage:
        out["cache_read_input_tokens"] = cache_read
    if "cache_creation_input_tokens" in usage:
        out["cache_creation_input_tokens"] = cache_write
    return out


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

    def _endpoint(self, path: str) -> str:
        """构建 Anthropic API 端点，兼容 base_url 带/不带 /v1 后缀。

        registry 默认 base_url=https://api.anthropic.com（不带 /v1），但界面可配
        成 …/v1，此时若再拼 /v1/... 会得到 …/v1/v1/... 404。统一在此收敛。
        """
        base = self.base_url.rstrip("/")
        if base.endswith("/v1"):
            return f"{base}{path}"
        return f"{base}/v1{path}"

    async def health_check(self) -> bool:
        """健康检查：请求 Anthropic /v1/models 端点验证连通性

        - 未配置 key：直接 False，不发网络请求（上游必拒，省去网络等待）
        - 401/403：无 key 或 Key 无效 → False（避免"测试通过"假阳性）
        - 其他 4xx：连通正常 → True；5xx/异常：False
        - 探测用短超时（≤5s）：连通性测试不该等业务 60s 超时
        """
        if not self.api_key:
            return False
        try:
            probe_timeout = min(self.timeout_seconds, 5.0)
            async with httpx.AsyncClient(timeout=probe_timeout) as client:
                resp = await client.get(
                    self._endpoint("/models"),
                    headers=self._headers(),
                )
                if resp.status_code in (401, 403):
                    return False
                return resp.status_code < 500
        except Exception:
            return False

    def _apply_cache_control(self, body: dict[str, Any]) -> None:
        """注入 Anthropic 显式缓存标记（Layer 2，原地修改 body）。

        放置位置：最后一个 system content block。Anthropic 渲染顺序是
        tools → system → messages，所以 system 末尾的单个 breakpoint 能同时
        覆盖 tools + system —— 一个 breakpoint 拿到最大前缀（上限 4 个）。

        - system 字符串转为 content blocks 数组（Anthropic 两种形式都接受）
        - 低于模型最低门槛时上游静默不缓存（usage.cache_creation_input_tokens=0），
          不报错也不计费，所以无需在网关侧精确判断门槛（各模型 512~4096 且非单调）
        - 命中读 0.1×，写 1.25×(5m)/2×(1h)：多轮复用 system 才回本，故可配置关闭
        - 命中数据回流：usage.cache_read_input_tokens → cache_usage.py 归一化入库
        """
        if not settings.anthropic_cache_control:
            return
        system = body.get("system")
        if not system:
            return  # 无 system 时不注入（messages 是易变部分，不做 breakpoint）

        cache_control: dict[str, Any] = {"type": "ephemeral"}
        if settings.anthropic_cache_ttl and settings.anthropic_cache_ttl != "5m":
            cache_control["ttl"] = settings.anthropic_cache_ttl

        if isinstance(system, str):
            body["system"] = [{"type": "text", "text": system, "cache_control": cache_control}]
        elif isinstance(system, list) and system:
            # 已是 blocks 数组：标记最后一个 block（不覆盖客户端已有的 cache_control）
            blocks = [dict(b) if isinstance(b, dict) else b for b in system]
            last = blocks[-1]
            if isinstance(last, dict) and "cache_control" not in last:
                last["cache_control"] = cache_control
            body["system"] = blocks

    def _convert_request(self, upstream_model: str, payload: dict[str, Any]) -> dict[str, Any]:
        """
        OpenAI messages 格式 → Anthropic Messages API 格式
        - system 消息提取为顶层 system 字段
        - 其余消息保留 role/content
        - system 末尾注入 cache_control（显式缓存，命中省 90%）
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
        self._apply_cache_control(body)
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
            "usage": _convert_usage(usage),
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
                    self._endpoint("/messages"),
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
        usage 从 message_start（input+缓存字段）与 message_delta（output）累积，
        末尾输出一个 usage chunk 供网关计费与缓存命中监控
        """
        body = {**self._convert_request(upstream_model, payload), "stream": True}
        request_model = payload.get("model", upstream_model)
        chat_id = f"chatcmpl-{uuid.uuid4().hex}"
        created = int(time.time())
        usage_acc: dict[str, Any] = {}

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            try:
                async with client.stream(
                    "POST",
                    self._endpoint("/messages"),
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

                        # usage 累积：Anthropic 把 input/缓存字段放在 message_start，
                        # 最终 output_tokens 放在 message_delta（末尾 chunk 统一输出）
                        if etype == "message_start":
                            started = (event.get("message") or {}).get("usage") or {}
                            if started:
                                usage_acc.update(started)
                        elif etype == "message_delta":
                            delta_usage = event.get("usage") or {}
                            if delta_usage:
                                usage_acc.update(delta_usage)

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
                            # 末尾 usage chunk（OpenAI include_usage 风格）：
                            # 网关据此流后计费 + 解析缓存命中（Layer 3）
                            if usage_acc:
                                usage_chunk = {
                                    "id": chat_id,
                                    "object": "chat.completion.chunk",
                                    "created": created,
                                    "model": request_model,
                                    "choices": [],
                                    "usage": _convert_usage(usage_acc),
                                }
                                yield f"data: {json.dumps(usage_chunk)}\n\n"
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
